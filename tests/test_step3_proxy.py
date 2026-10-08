"""Proxy ownership across acquisition/body cancellation and real local sockets."""
import asyncio
from contextlib import asynccontextmanager
import logging
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from h3.app import proxy

REAL_CLIENT = httpx.AsyncClient

class Body(httpx.AsyncByteStream):
    def __init__(self, error=None): self.error, self.closed = error, False
    async def __aiter__(self):
        yield b'first'
        if self.error: raise self.error
    async def aclose(self): self.closed = True


def request():
    async def receive():
        await asyncio.Future()
    return Request({'type':'http','method':'GET','path':'/','headers':[], 'asgi':{'spec_version':'2.4'}}, receive)


@asynccontextmanager
async def loopback(app):
    sock = socket.socket(); sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='critical', access_log=False))
    runner = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if runner.done(): runner.result()
                await asyncio.sleep(0.001)
        yield port
    finally:
        server.should_exit = True
        await asyncio.wait_for(runner, 5)
        sock.close()


class Step3ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_construction_error_still_closes_client(self):
        owned = REAL_CLIENT()
        with patch.object(proxy.httpx, 'AsyncClient', return_value=owned), patch.object(owned, 'stream', side_effect=ValueError('bad headers')):
            with self.assertRaisesRegex(ValueError, 'bad headers'):
                await proxy.worker_proxy('http://fake', request(), {})
        self.assertTrue(owned.is_closed)

    async def test_acquisition_connect_error_and_header_timeout_close_client(self):
        for error, status in ((httpx.ConnectError('connect'),502), (httpx.ReadTimeout('headers'),504)):
            with self.subTest(error=type(error).__name__):
                owned = REAL_CLIENT(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(error)))
                with patch.object(proxy.httpx, 'AsyncClient', return_value=owned) as factory:
                    with self.assertRaises(HTTPException) as caught: await proxy.worker_proxy('http://fake', request(), {})
                self.assertEqual(caught.exception.status_code, status)
                self.assertTrue(owned.is_closed)
                timeout = factory.call_args.kwargs['timeout']
                self.assertTrue(all(getattr(timeout,key) is not None for key in ('connect','read','write','pool')))

    async def test_body_read_timeout_and_normal_eof_close_stream_and_client(self):
        for error in (None, httpx.ReadTimeout('body')):
            with self.subTest(error=error):
                body = Body(error)
                owned = REAL_CLIENT(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body)))
                async def send(_message): pass
                with patch.object(proxy.httpx, 'AsyncClient', return_value=owned):
                    response = await proxy.worker_proxy('http://fake', request(), {})
                    if error:
                        with self.assertRaises(httpx.ReadTimeout): await response(request().scope, request().receive, send)
                    else: await response(request().scope, request().receive, send)
                self.assertTrue(body.closed); self.assertTrue(owned.is_closed)

    async def test_cancellation_during_acquisition_closes_owned_client(self):
        entered = asyncio.Event()
        async def handler(_req): entered.set(); await asyncio.Future()
        owned = REAL_CLIENT(transport=httpx.MockTransport(handler))
        with patch.object(proxy.httpx, 'AsyncClient', return_value=owned):
            task = asyncio.create_task(proxy.worker_proxy('http://fake', request(), {}))
            await asyncio.wait_for(entered.wait(), 2); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(owned.is_closed)

    async def test_body_cancellation_and_repeated_cancel_wait_for_cleanup(self):
        body = Body(); owned = REAL_CLIENT(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body)))
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        real_close = owned.aclose
        async def close(): cleanup.set(); await release.wait(); await real_close()
        async def send(message):
            if message['type'] == 'http.response.body': entered.set(); await asyncio.Future()
        with patch.object(proxy.httpx, 'AsyncClient', return_value=owned), patch.object(owned, 'aclose', side_effect=close):
            response = await proxy.worker_proxy('http://fake', request(), {})
            task = asyncio.create_task(response(request().scope, request().receive, send))
            await asyncio.wait_for(entered.wait(),2); task.cancel()
            await asyncio.wait_for(cleanup.wait(),2); task.cancel(); release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(body.closed); self.assertTrue(owned.is_closed)

    async def test_cleanup_failure_preserves_primary_timeout(self):
        primary = httpx.ReadTimeout('primary')
        owned = REAL_CLIENT(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(primary)))
        async def bad_close(): await REAL_CLIENT.aclose(owned); raise OSError('cleanup')
        with patch.object(proxy.httpx, 'AsyncClient', return_value=owned), patch.object(owned, 'aclose', side_effect=bad_close):
            with self.assertRaises(HTTPException) as caught: await proxy.worker_proxy('http://fake', request(), {})
        self.assertEqual(caught.exception.status_code,504)
        self.assertIs(caught.exception.__cause__, primary)
        self.assertIn('OSError', primary.__notes__[0]); self.assertTrue(owned.is_closed)

    async def socket_case(self, mode):
        active, handlers, owned, downstream = set(), set(), [], []
        count = 3 if mode == 'disconnect' else 1
        accepted = asyncio.Queue()
        async def upstream(reader, writer):
            task = asyncio.current_task(); handlers.add(task)
            try:
                await reader.readuntil(b'\r\n\r\n'); active.add(writer); await accepted.put(writer)
                if mode == 'body_timeout':
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nfirst'); await writer.drain()
                await reader.read()  # server sees EOF only when proxy releases its socket
            finally:
                active.discard(writer); writer.close(); await writer.wait_closed(); handlers.discard(task)
        worker = await asyncio.start_server(upstream, '127.0.0.1', 0)
        worker_port = worker.sockets[0].getsockname()[1]
        app = FastAPI()
        @app.get('/')
        async def route(req: Request):
            return await proxy.worker_proxy(f'http://127.0.0.1:{worker_port}/', req, {})
        def factory(*args, **kwargs):
            client = REAL_CLIENT(*args, trust_env=False, **kwargs); owned.append(client); return client
        try:
            with patch.object(proxy.httpx, 'AsyncClient', side_effect=factory), \
                 patch.object(proxy, 'PROXY_TIMEOUT', httpx.Timeout(connect=1,read=5 if mode=='disconnect' else .08,write=1,pool=1)):
                async with loopback(app) as port:
                    for _ in range(count):
                        reader, writer = await asyncio.open_connection('127.0.0.1', port)
                        downstream.append(writer)
                        writer.write(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'); await writer.drain()
                        await asyncio.wait_for(accepted.get(),2)
                        if mode == 'disconnect': writer.close(); await writer.wait_closed()
                        else:
                            data = await asyncio.wait_for(reader.read(),2)
                            self.assertIn(b'504' if mode=='headers_timeout' else b'first', data)
                            writer.close(); await writer.wait_closed()
                    async with asyncio.timeout(3):
                        while active or handlers or not all(c.is_closed for c in owned): await asyncio.sleep(.005)
                    self.assertEqual(len(owned),count)
                    self.assertEqual(active,set()); self.assertEqual(handlers,set())
                    self.assertTrue(all(c.is_closed for c in owned))
        finally:
            for writer in downstream:
                writer.close(); await writer.wait_closed()
            worker.close(); await worker.wait_closed()
            for writer in list(active): writer.close()
            for task in list(handlers): task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)

    async def test_three_downstream_header_disconnects_leave_zero_upstream_sockets_tasks(self):
        await self.socket_case('disconnect')

    async def test_real_header_timeout_releases_socket(self):
        await self.socket_case('headers_timeout')

    async def test_real_body_read_timeout_releases_socket(self):
        await self.socket_case('body_timeout')
