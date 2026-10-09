"""Opt-in PTY sessions authenticated with short-lived one-use HTTP tickets."""
from __future__ import annotations

import asyncio
import codecs
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import re
import secrets
import signal
import struct
import subprocess
import sys
import termios
import time
from urllib.parse import urlsplit

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

TERMINAL_PROTOCOL = "h3-terminal"
TOKEN_PROTOCOL_PREFIX = "h3-session."
TOKEN_TTL_SECONDS = 30
MAX_INPUT_BYTES = 16 * 1024
PIDFD_SIGNAL_PROCESS_GROUP = 4


class SessionTokens:
    def __init__(self, *, clock=time.monotonic, ttl=TOKEN_TTL_SECONDS, max_pending=32):
        self.clock, self.ttl, self.max_pending = clock, ttl, max_pending
        self.pending: dict[str, float] = {}

    def _prune(self):
        now = self.clock()
        self.pending = {key: expiry for key, expiry in self.pending.items() if expiry > now}

    def issue(self) -> str:
        self._prune()
        if len(self.pending) >= self.max_pending:
            raise RuntimeError("Too many pending terminal sessions")
        token = secrets.token_urlsafe(32)
        self.pending[hashlib.sha256(token.encode()).hexdigest()] = self.clock() + self.ttl
        return token

    def consume(self, token: str) -> bool:
        self._prune()
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return False
        # No await separates lookup and removal: even simultaneous upgrades
        # cannot use the same ticket twice. Tokens never survive a restart.
        expiry = self.pending.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        return expiry is not None and expiry > self.clock()


def websocket_ticket(websocket: WebSocket) -> str | None:
    protocols = websocket.scope.get("subprotocols", [])
    if (len(protocols) != 2 or protocols[0] != TERMINAL_PROTOCOL
            or not protocols[1].startswith(TOKEN_PROTOCOL_PREFIX)):
        return None
    origin = websocket.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or parsed.netloc != websocket.headers.get("host"):
            return None
    return protocols[1][len(TOKEN_PROTOCOL_PREFIX):]


class PTYSession:
    def __init__(self, workspace: Path, require_group_control):
        self.workspace, self.require_group_control = workspace, require_group_control
        self.process = None
        self.master = self.control = self.pidfd = self.reply = self.shell_pidfd = None
        self.closing = asyncio.Event()
        self.closed = False
        self.close_task = None

    def start(self):
        self.require_group_control()
        slave = control_read = reply_write = None
        try:
            self.master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            control_read, self.control = os.pipe2(os.O_CLOEXEC)
            self.reply, reply_write = os.pipe2(os.O_CLOEXEC)
            environment = dict(os.environ, TERM="dumb", HISTFILE=os.devnull, HISTSIZE="0", HISTFILESIZE="0",
                               PS1="aj:\\w\\$ ", PROMPT_COMMAND="set +m; set +o history; unset PROMPT_COMMAND")
            for name in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"):
                environment.pop(name, None)
            # Popen stays unreaped until synchronous pidfd capture. The private
            # gate prevents even Bash starting before ownership is established.
            self.process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("terminal_shell.py")),
                                             str(control_read), str(reply_write), str(os.getpid())],
                                            stdin=slave, stdout=slave, stderr=slave, cwd=self.workspace,
                                            env=environment, pass_fds=(control_read, reply_write), start_new_session=True)
            self.pidfd = os.pidfd_open(self.process.pid)
            os.set_blocking(self.master, False)
            os.set_blocking(self.reply, False)
            os.write(self.control, b"G")
        finally:
            primary = sys.exception()
            errors = []
            for descriptor in (slave, control_read, reply_write):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError as error:
                        errors.append(error)
            if primary is not None:
                for error in errors:
                    primary.add_note(f"Terminal descriptor cleanup failed: {type(error).__name__}")
            elif errors:
                raise ExceptionGroup("Terminal descriptor cleanup failed", errors)

    async def ready(self):
        body = b""
        async with asyncio.timeout(2):
            while not body.endswith(b"\n"):
                chunk = await self._read_fd(self.reply, 32)
                if not chunk or len(body) + len(chunk) > 32:
                    raise RuntimeError("Invalid shell ownership handshake")
                body += chunk
        pid = int(body)
        if pid <= 0:
            raise RuntimeError("Invalid shell ownership handshake")
        # The helper has not reaped or released this exact gated child. There
        # is no await between capture and authorizing Bash to start.
        self.shell_pidfd = os.pidfd_open(pid)
        os.write(self.control, b"A")
        descriptor, self.reply = self.reply, None
        os.close(descriptor)

    async def read(self) -> bytes:
        return await self._read_fd(self.master, 4096)

    async def _read_fd(self, descriptor: int, limit: int) -> bytes:
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def ready():
            if future.done():
                return
            try:
                future.set_result(os.read(descriptor, limit))
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno in {errno.EIO, errno.EBADF}:
                    future.set_result(b"")
                else:
                    future.set_exception(error)

        loop.add_reader(descriptor, ready)
        try:
            return await future
        finally:
            loop.remove_reader(descriptor)

    async def write(self, body: bytes):
        if len(body) > MAX_INPUT_BYTES:
            raise ValueError("Terminal input too large")
        descriptor = self.master
        loop = asyncio.get_running_loop()
        while body and not self.closing.is_set():
            try:
                count = os.write(descriptor, body)
                body = body[count:]
            except BlockingIOError:
                future = loop.create_future()

                def ready():
                    if not future.done():
                        future.set_result(None)

                loop.add_writer(descriptor, ready)
                try:
                    await future
                finally:
                    loop.remove_writer(descriptor)

    async def _cleanup(self):
        errors = []

        def close_descriptor(name):
            descriptor = getattr(self, name)
            if descriptor is not None:
                # On Linux close releases the fd even on EIO. Never retry its
                # number, which could already identify an unrelated resource.
                setattr(self, name, None)
                try:
                    os.close(descriptor)
                except OSError as error:
                    errors.append(error)

        close_descriptor("control")
        process = self.process
        uncertain = False
        if process is not None:
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if self.pidfd is not None:
                        signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
                    else:
                        process.terminate()  # Gate was never released; no descendants.
                except ProcessLookupError:
                    pass
                except OSError as error:
                    errors.append(error)
                try:
                    await asyncio.to_thread(process.wait, timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                except OSError as error:
                    errors.append(error)
            except OSError as error:
                errors.append(error)
            # Always kill the original group after helper completion as well.
            # A crashed supervisor must not leave its shell/commands running.
            for capability in (self.shell_pidfd, self.pidfd):
                if capability is None:
                    continue
                for attempt in range(2):
                    try:
                        signal.pidfd_send_signal(capability, signal.SIGKILL, None, PIDFD_SIGNAL_PROCESS_GROUP)
                        break
                    except ProcessLookupError:
                        break
                    except OSError as error:
                        if attempt:
                            uncertain = True
                            errors.append(error)
            if self.pidfd is None and process.returncode is None:
                try:
                    process.kill()  # Unreleased supervisor gate, no descendants.
                except ProcessLookupError:
                    pass
                except OSError as error:
                    errors.append(error)
            try:
                await asyncio.to_thread(process.wait, timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                uncertain = True
                errors.append(error)
        for name in ("master", "reply"):
            close_descriptor(name)
        if not uncertain:
            for name in ("pidfd", "shell_pidfd"):
                close_descriptor(name)
            self.closed = True
        if errors:
            raise ExceptionGroup("Terminal cleanup failed", errors)

    async def close(self):
        self.closing.set()
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._cleanup())
        cancellation = None
        while not self.close_task.done():
            try:
                await asyncio.shield(self.close_task)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except BaseException:
                break
        try:
            self.close_task.result()
        except BaseException:
            # Retain the capability/session on cleanup uncertainty for a retry,
            # rather than losing its only owner and allowing another shell.
            self.close_task = None
            raise
        if cancellation is not None:
            raise cancellation


async def bridge_terminal(websocket: WebSocket, session: PTYSession):
    async def output():
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            body = await session.read()
            text = decoder.decode(body, final=not body)
            if text:
                await asyncio.wait_for(websocket.send_text(text), timeout=5)
            if not body:
                return

    async def input_stream():
        while True:
            text = await websocket.receive_text()
            await session.write(text.encode("utf-8"))

    tasks = [asyncio.create_task(output()), asyncio.create_task(input_stream()),
             asyncio.create_task(session.closing.wait())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        join = asyncio.gather(*tasks, return_exceptions=True)
        # Repeated cancellation cannot leave an fd reader/writer registered.
        while not join.done():
            try:
                await asyncio.shield(join)
            except asyncio.CancelledError:
                continue
