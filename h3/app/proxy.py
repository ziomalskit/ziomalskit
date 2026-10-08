"""Bounded worker streaming with one owner for all upstream resources."""
from __future__ import annotations

import asyncio
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

PROXY_TIMEOUT = httpx.Timeout(connect=5, read=60, write=5, pool=5)


async def _cleanup(client, stream, entered, primary=None, *, opening=None, disconnect=None):
    async def close():
        errors = []
        acquired = entered
        if disconnect is not None:
            disconnect.cancel()
            await asyncio.gather(disconnect, return_exceptions=True)
        if opening is not None:
            opening.cancel()
            result = (await asyncio.gather(opening, return_exceptions=True))[0]
            acquired = acquired or not isinstance(result, BaseException)
        if acquired:
            try:
                await stream.__aexit__(None, None, None)
            except BaseException as error:
                errors.append(error)
        try:
            await client.aclose()
        except BaseException as error:
            errors.append(error)
        return errors

    task = asyncio.create_task(close())
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
    errors = task.result()
    if primary is not None:
        for error in errors:
            primary.add_note(f"Proxy cleanup failed: {type(error).__name__}")
    elif errors:
        raise BaseExceptionGroup("Proxy cleanup failed", errors)
    if cancellation is not None and primary is None:
        raise cancellation


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, upstream, client, stream):
        headers = {key: upstream.headers[key] for key in (
            "content-type", "content-length", "content-range", "accept-ranges", "content-disposition"
        ) if key in upstream.headers}
        super().__init__(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers)
        self.client, self.stream = client, stream

    async def __call__(self, scope, receive, send):
        primary = None
        try:
            await super().__call__(scope, receive, send)
        except BaseException as error:
            primary = error
            raise
        finally:
            await _cleanup(self.client, self.stream, True, primary)


async def worker_proxy(target: str, request: Request, headers: dict):
    client = httpx.AsyncClient(timeout=PROXY_TIMEOUT)
    stream = opening = disconnect = None

    async def disconnected():
        while (await request.receive())["type"] != "http.disconnect":
            pass

    entered = False
    primary = None
    handed_off = False
    try:
        stream = client.stream("GET", target, headers=headers)
        opening = asyncio.create_task(stream.__aenter__())
        disconnect = asyncio.create_task(disconnected())
        await asyncio.wait((opening, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if disconnect.done():
            # No body generator exists yet. Cancelling acquisition and closing
            # the client here is essential for header-stalled worker sockets.
            return Response(status_code=499)
        upstream = await opening
        entered = True
        disconnect.cancel()
        await asyncio.gather(disconnect, return_exceptions=True)
        disconnect = None
        response = OwnedStreamingResponse(upstream, client, stream)
        handed_off = True
        return response
    except httpx.TimeoutException as error:
        primary = error
        raise HTTPException(504, "Worker response timed out") from error
    except httpx.HTTPError as error:
        primary = error
        raise HTTPException(502, "Worker connection failed") from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if not handed_off:
            await _cleanup(client, stream, entered, primary, opening=opening, disconnect=disconnect)
