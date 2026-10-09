"""One-use WebSocket authentication and real local PTY/process cleanup."""
import asyncio
import errno
import importlib
import os
from pathlib import Path
import shlex
import signal
import socket
import sys
import unittest
from unittest.mock import patch

import httpx

from h3.app.terminal import SessionTokens, TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX
from h3.scripts import runtime_config
from tests.helpers import load_controller
from tests.test_step3_api import api


async def websocket_exchange(m, protocols=(), *, headers=(), query=b"", command=None, marker=None):
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "websocket.connect"})
    events = []
    scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "scheme": "ws", "path": "/api/terminal/ws", "raw_path": b"/api/terminal/ws",
             "root_path": "", "query_string": query, "headers": [(b"host", b"fixture"), *headers],
             "client": ("127.0.0.1", 12345), "server": ("fixture", 80), "subprotocols": list(protocols)}
    transcript = ""

    async def send(event):
        nonlocal transcript
        events.append(event)
        if event["type"] == "websocket.accept" and command:
            incoming.put_nowait({"type": "websocket.receive", "text": command})
        if event["type"] == "websocket.send":
            transcript += event.get("text", "")
            if marker and marker in transcript:
                incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    await asyncio.wait_for(m.app(scope, incoming.get, send), timeout=8)
    return events, transcript


class TokenTests(unittest.TestCase):
    def test_random_one_use_valid_invalid_and_reused_tokens(self):
        tokens = SessionTokens()
        first, second = tokens.issue(), tokens.issue()
        self.assertNotEqual(first, second)
        self.assertFalse(tokens.consume("invalid"))
        self.assertFalse(tokens.consume("A" * 43))
        self.assertTrue(tokens.consume(first))
        self.assertFalse(tokens.consume(first))
        self.assertTrue(tokens.consume(second))
        self.assertEqual(tokens.pending, {})

    def test_expiry_at_boundary_fails_closed_and_capacity_is_bounded(self):
        now = [10]
        tokens = SessionTokens(clock=lambda: now[0], ttl=30, max_pending=1)
        first = tokens.issue()
        with self.assertRaises(RuntimeError):
            tokens.issue()
        now[0] = 40
        self.assertFalse(tokens.consume(first))
        self.assertTrue(tokens.consume(tokens.issue()))

    def test_controller_restart_invalidates_tickets(self):
        first = SessionTokens().issue()
        self.assertFalse(SessionTokens().consume(first))

    def test_runtime_settings_default_and_preserve_explicit_overrides(self):
        with load_controller() as m:
            path = m.ROOT / "runtime-test.env"
            runtime_config.write_runtime(path, {})
            values = runtime_config.read_env(path)
            self.assertEqual((values["H3_PROMPT_PREFETCH"], values["H3_ENABLE_TERMINAL"]), ("3", "0"))
            runtime_config.write_runtime(path, {"H3_PROMPT_PREFETCH": "2", "H3_ENABLE_TERMINAL": "1"})
            runtime_config.write_runtime(path, {})
            values = runtime_config.read_env(path)
            self.assertEqual((values["H3_PROMPT_PREFETCH"], values["H3_ENABLE_TERMINAL"]), ("2", "1"))


class TerminalAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_config_http_and_websocket_refuse_sessions(self):
        with load_controller() as m:
            async with api(m) as c:
                self.assertFalse((await c.get("/api/config")).json()["terminal_enabled"])
                self.assertEqual((await c.post("/api/terminal/session")).status_code, 403)
            with patch.object(m.PTYSession, "start") as start:
                events, _text = await websocket_exchange(m)
            self.assertEqual(events, [{"type": "websocket.close", "code": 1008, "reason": ""}])
            start.assert_not_called()

    async def test_handshake_requires_http_auth_does_not_spawn_and_is_not_cacheable(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True
            with patch.object(m.PTYSession, "start") as start:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://fixture") as c:
                    self.assertEqual((await c.post("/api/terminal/session")).status_code, 401)
                self.assertEqual(m.terminal_tokens.pending, {})
                async with api(m) as c:
                    response = await c.post("/api/terminal/session")
                    self.assertTrue((await c.get("/api/config")).json()["terminal_enabled"])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["expires_in_seconds"], 30)
            self.assertRegex(response.json()["token"], r"^[A-Za-z0-9_-]{43}$")
            start.assert_not_called()

    async def test_http_middleware_cannot_authorize_websocket_or_url_ticket(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True
            import base64
            basic = b"Basic " + base64.b64encode(b"h3:test-only-password")
            valid = m.terminal_tokens.issue()
            with patch.object(m.PTYSession, "start") as start:
                for protocols, query in (((), b""), ((TERMINAL_PROTOCOL,), b"token=" + valid.encode()),
                                          ((TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + "A" * 43), b"")):
                    events, _ = await websocket_exchange(m, protocols, headers=[(b"authorization", basic)], query=query)
                    self.assertFalse(any(event["type"] == "websocket.accept" for event in events))
            start.assert_not_called()
            self.assertTrue(m.terminal_tokens.consume(valid))

    async def test_expired_reused_and_cross_origin_tokens_fail_closed(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True
            now = [0]
            m.terminal_tokens = SessionTokens(clock=lambda: now[0])
            expired = m.terminal_tokens.issue()
            now[0] = 30
            reused = m.terminal_tokens.issue(); self.assertTrue(m.terminal_tokens.consume(reused))
            cross_origin = m.terminal_tokens.issue()
            with patch.object(m.PTYSession, "start") as start:
                for token, headers in ((expired, ()), (reused, ()), (cross_origin, [(b"origin", b"http://untrusted")])):
                    events, _ = await websocket_exchange(m, (TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token), headers=headers)
                    self.assertFalse(any(event["type"] == "websocket.accept" for event in events))
            start.assert_not_called()

    async def test_valid_http_ticket_runs_real_websocket_shell_and_is_consumed(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True
            m.WORKSPACE.mkdir()
            async with api(m) as c:
                token = (await c.post("/api/terminal/session")).json()["token"]
            sessions = []
            original = m.PTYSession

            def session(*args):
                owned = original(*args); sessions.append(owned); return owned

            with patch.object(m, "PTYSession", side_effect=session):
                events, transcript = await websocket_exchange(m, (TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token),
                    headers=[(b"origin", b"http://fixture")], command="pwd; printf '__WS_READY__\\n'\n", marker="\r\n__WS_READY__\r\n")
            self.assertIn({"type": "websocket.accept", "subprotocol": TERMINAL_PROTOCOL, "headers": []}, events)
            self.assertIn(str(m.WORKSPACE), transcript)
            self.assertEqual(m.terminal_sessions, set())
            self.assertTrue(sessions[0].closed)
            self.assertEqual(sessions[0].process.returncode, 0)
            self.assertFalse(Path(f"/proc/{sessions[0].process.pid}").exists())
            events, _ = await websocket_exchange(m, (TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token))
            self.assertFalse(any(event["type"] == "websocket.accept" for event in events))

    async def test_start_error_cleans_gated_child_and_releases_session(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True; m.WORKSPACE.mkdir()
            token = m.terminal_tokens.issue()
            terminal = importlib.import_module(m.__package__ + ".terminal")
            real_open = os.pidfd_open
            processes = []
            real_popen = terminal.subprocess.Popen

            def spawn(*args, **options):
                process = real_popen(*args, **options); processes.append(process); return process

            def capture(pid):
                if processes and pid == processes[0].pid:
                    raise OSError(errno.EMFILE, "test capture failure")
                return real_open(pid)

            with patch.object(terminal.subprocess, "Popen", side_effect=spawn), patch.object(terminal.os, "pidfd_open", side_effect=capture):
                await websocket_exchange(m, (TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token))
            self.assertEqual(m.terminal_sessions, set())
            self.assertIsNotNone(processes[0].returncode)
            self.assertFalse(Path(f"/proc/{processes[0].pid}").exists())

    async def test_output_transport_error_still_reaps_real_shell(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True; m.WORKSPACE.mkdir()
            token = m.terminal_tokens.issue()
            scope = {"type": "websocket", "path": "/api/terminal/ws", "scheme": "ws", "query_string": b"",
                     "headers": [(b"host", b"fixture")], "subprotocols": [TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token]}
            incoming = asyncio.Queue(); incoming.put_nowait({"type": "websocket.connect"})
            sessions = []
            original = m.PTYSession

            def session(*args):
                owned = original(*args); sessions.append(owned); return owned

            async def failed_send(event):
                if event["type"] == "websocket.send":
                    raise RuntimeError("simulated transport failure")

            with patch.object(m, "PTYSession", side_effect=session):
                await asyncio.wait_for(m.app(scope, incoming.get, failed_send), timeout=5)
            self.assertTrue(sessions[0].closed)
            self.assertFalse(Path(f"/proc/{sessions[0].process.pid}").exists())
            self.assertEqual(m.terminal_sessions, set())

    async def test_shell_pidfd_capture_failure_reaps_both_gated_processes(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True; m.WORKSPACE.mkdir()
            token = m.terminal_tokens.issue()
            terminal = importlib.import_module(m.__package__ + ".terminal")
            real_open = os.pidfd_open
            sessions, children = [], []
            original = m.PTYSession

            def session(*args):
                owned = original(*args); sessions.append(owned); return owned

            def capture(pid):
                # The supervisor is captured first; its child is still gated
                # and unreaped when the controller tries the second capture.
                if sessions and sessions[0].process is not None and pid != sessions[0].process.pid:
                    children.append(pid)
                    raise OSError(errno.EMFILE, "test shell capture failure")
                return real_open(pid)

            with patch.object(m, "PTYSession", side_effect=session), patch.object(terminal.os, "pidfd_open", side_effect=capture):
                await websocket_exchange(m, (TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token))
            self.assertEqual(len(children), 1)
            self.assertTrue(sessions[0].closed)
            self.assertFalse(Path(f"/proc/{children[0]}").exists())
            self.assertFalse(Path(f"/proc/{sessions[0].process.pid}").exists())
            self.assertEqual(m.terminal_sessions, set())

    async def test_handler_cancellation_during_shell_handshake_still_reaps(self):
        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True; m.WORKSPACE.mkdir()
            token = m.terminal_tokens.issue()
            incoming = asyncio.Queue(); incoming.put_nowait({"type": "websocket.connect"})
            scope = {"type": "websocket", "path": "/api/terminal/ws", "scheme": "ws", "query_string": b"",
                     "headers": [(b"host", b"fixture")], "subprotocols": [TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token]}
            started = asyncio.Event()
            sessions = []
            original = m.PTYSession

            def session(*args):
                owned = original(*args); sessions.append(owned); return owned

            async def blocked_ready(_session):
                started.set()
                await asyncio.Event().wait()

            async def send(_event):
                pass

            with patch.object(m, "PTYSession", side_effect=session), patch.object(original, "ready", blocked_ready):
                task = asyncio.create_task(m.app(scope, incoming.get, send))
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            self.assertTrue(sessions[0].closed)
            self.assertFalse(Path(f"/proc/{sessions[0].process.pid}").exists())
            self.assertEqual(m.terminal_sessions, set())


class TerminalCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.m.WORKSPACE.mkdir()
        self.session = self.m.PTYSession(self.m.WORKSPACE, self.m._require_restart_group_control)
        self.session.start()
        await self.session.ready()

    async def asyncTearDown(self):
        await self.session.close()
        self.fixture.__exit__(None, None, None)

    async def output_until(self, marker):
        output = b""
        async with asyncio.timeout(3):
            while marker not in output:
                chunk = await self.session.read()
                self.assertTrue(chunk, output.decode(errors="replace"))
                output += chunk
        return output.decode(errors="replace")

    async def child(self, *, separate_session=False, exit_shell=False):
        ready = self.m.WORKSPACE / "child.pid"
        code = ("import ctypes,os,signal,time;from pathlib import Path;"
                # Linux comm is bytes, not necessarily UTF-8. Cleanup must not
                # lose detached/background children on a decoding exception.
                + "ctypes.CDLL(None).prctl(15,b'\\xffaj-child',0,0,0);"
                + ("os.setsid();" if separate_session else "")
                + "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                + f"Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(60)")
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(code) + " &\n"
        await self.session.write(command.encode())
        async with asyncio.timeout(3):
            while not ready.exists():
                await asyncio.sleep(0.01)
        if exit_shell:
            await self.session.write(b"exit\n")
        return int(ready.read_text())

    def assert_reaped(self, child=None):
        self.assertTrue(self.session.closed)
        self.assertIsNotNone(self.session.process.returncode)
        self.assertFalse(Path(f"/proc/{self.session.process.pid}").exists())
        with self.assertRaises(ChildProcessError):
            os.waitpid(self.session.process.pid, os.WNOHANG)
        if child is not None:
            self.assertFalse(Path(f"/proc/{child}").exists(), "terminal descendant was not reaped")

    async def test_interactive_shell_working_directory_and_no_persistent_history(self):
        await self.session.write(b"pwd; declare -p HISTFILE HISTSIZE HISTFILESIZE; [[ $- != *m* ]] && printf '__NO_JOB_CONTROL__\\n'; printf '__DONE__\\n'\n")
        output = await self.output_until(b"\r\n__DONE__\r\n")
        self.assertIn(str(self.m.WORKSPACE), output)
        self.assertIn('HISTFILE="/dev/null"', output)
        self.assertIn('HISTSIZE="0"', output)
        self.assertIn('HISTFILESIZE="0"', output)
        self.assertIn("\r\n__NO_JOB_CONTROL__\r\n", output)
        self.assertFalse((self.m.WORKSPACE / ".bash_history").exists())

    async def test_ctrl_c_interrupts_foreground_and_shell_stays_interactive(self):
        await self.session.write(b"printf '__SHELL_READY__\\n'\n")
        await self.output_until(b"\r\n__SHELL_READY__\r\n")
        await self.session.write(b"sleep 30\n")
        await self.output_until(b"sleep 30\r\n")
        await asyncio.sleep(0.05)
        await self.session.write(b"\x03")
        await self.session.write(b"printf '__ALIVE__\\n'\n")
        await self.output_until(b"\r\n__ALIVE__\r\n")

    async def test_disconnect_reaps_background_child_ignoring_term(self):
        child = await self.child()
        await self.session.close()
        self.assert_reaped(child)

    async def test_disconnect_reaps_child_in_new_process_session(self):
        child = await self.child(separate_session=True)
        await self.session.close()
        self.assert_reaped(child)

    async def test_shell_exit_still_reaps_background_child(self):
        child = await self.child(exit_shell=True)
        await self.session.close()
        self.assert_reaped(child)

    async def test_repeated_cancellation_waits_for_cleanup_and_reaping(self):
        child = await self.child()
        task = asyncio.create_task(self.session.close())
        await self.session.closing.wait()
        task.cancel(); await asyncio.sleep(0.01); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_reaped(child)

    async def test_oversized_input_failure_can_always_clean_session(self):
        child = await self.child()
        with self.assertRaises(ValueError):
            await self.session.write(b"x" * (16 * 1024 + 1))
        await self.session.close()
        self.assert_reaped(child)

    async def test_descriptor_close_eio_does_not_skip_process_cleanup(self):
        child = await self.child(separate_session=True)
        terminal = importlib.import_module(self.m.__package__ + ".terminal")
        original_close, control = os.close, self.session.control
        injected = False

        def close(descriptor):
            nonlocal injected
            original_close(descriptor)
            if descriptor == control and not injected:
                injected = True
                raise OSError(errno.EIO, "test close error after Linux releases fd")

        with patch.object(terminal.os, "close", side_effect=close):
            with self.assertRaises(ExceptionGroup):
                await self.session.close()
        self.assertTrue(injected)
        self.assert_reaped(child)
        self.assertIsNone(self.session.control)
        self.assertIsNone(self.session.pidfd)
        self.assertIsNone(self.session.shell_pidfd)

    async def test_transient_group_signal_error_retries_original_capability(self):
        child = await self.child()
        terminal = importlib.import_module(self.m.__package__ + ".terminal")
        original_send = signal.pidfd_send_signal
        calls = []
        capability = self.session.shell_pidfd

        def send(descriptor, sig, siginfo=None, flags=0):
            if descriptor == capability and flags == 4:
                calls.append(descriptor)
                if len(calls) == 1:
                    raise OSError(errno.EIO, "test transient group signal failure")
            return original_send(descriptor, sig, siginfo, flags)

        with patch.object(terminal.signal, "pidfd_send_signal", side_effect=send):
            await self.session.close()
        self.assertEqual(calls, [capability, capability])
        self.assert_reaped(child)

    async def test_controller_shutdown_reaps_active_shell_and_invalidates_tickets(self):
        self.m.terminal_sessions.add(self.session)
        self.m.terminal_tokens.issue()
        self.m.prompt_worker_task = self.m.render_worker_task = self.m.vast_guard_task = self.m.recovery_task = None
        child = await self.child()
        await self.m.shutdown()
        self.assertEqual(self.m.terminal_sessions, set())
        self.assertEqual(self.m.terminal_tokens.pending, {})
        self.assertTrue(self.m.terminal_shutting_down)
        self.assert_reaped(child)


class TerminalTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_http_handshake_websocket_stream_disconnect_and_reuse(self):
        """Real loopback TCP/WebSocket transport, with no external services/actions."""
        import base64
        import uvicorn
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        with load_controller() as m:
            m.H3_ENABLE_TERMINAL = True; m.WORKSPACE.mkdir()
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0)); listener.listen(128); listener.setblocking(False)
            port = listener.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            websocket_url = f"ws://127.0.0.1:{port}/api/terminal/ws"
            server = uvicorn.Server(uvicorn.Config(m.app, loop="asyncio", log_config=None, access_log=False))
            with patch.object(m, "submissions_allowed", return_value=False):
                runner = asyncio.create_task(server.serve(sockets=[listener]))
                try:
                    async with asyncio.timeout(3):
                        while not server.started:
                            await asyncio.sleep(0.01)
                    async with httpx.AsyncClient(base_url=base, trust_env=False) as c:
                        self.assertEqual((await c.post("/api/terminal/session")).status_code, 401)
                        response = await c.post("/api/terminal/session", auth=("h3", "test-only-password"))
                    self.assertEqual(response.status_code, 200)
                    token = response.json()["token"]
                    basic = "Basic " + base64.b64encode(b"h3:test-only-password").decode()
                    with self.assertRaises(InvalidStatus):
                        async with connect(websocket_url, proxy=None, additional_headers={"Authorization": basic}):
                            self.fail("HTTP auth incorrectly authorized WebSocket")
                    async with connect(websocket_url, proxy=None, origin=base,
                                       subprotocols=[TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token]) as ws:
                        self.assertEqual(ws.subprotocol, TERMINAL_PROTOCOL)
                        await ws.send("pwd; printf '__TCP_READY__\\n'\n")
                        transcript = ""
                        async with asyncio.timeout(3):
                            while "\r\n__TCP_READY__\r\n" not in transcript:
                                transcript += await ws.recv()
                        self.assertIn(str(m.WORKSPACE), transcript)
                        session = next(iter(m.terminal_sessions))
                    async with asyncio.timeout(3):
                        while m.terminal_sessions:
                            await asyncio.sleep(0.01)
                    self.assertTrue(session.closed)
                    self.assertFalse(Path(f"/proc/{session.process.pid}").exists())
                    with self.assertRaises(InvalidStatus):
                        async with connect(websocket_url, proxy=None,
                                           subprotocols=[TERMINAL_PROTOCOL, TOKEN_PROTOCOL_PREFIX + token]):
                            self.fail("Reused ticket accepted")
                finally:
                    server.should_exit = True
                    try:
                        await asyncio.wait_for(runner, 5)
                    finally:
                        listener.close()
