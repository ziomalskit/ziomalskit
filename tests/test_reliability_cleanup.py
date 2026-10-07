"""Owned CLI/watcher children survive no cleanup error or repeated cancellation."""
from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
import signal
import sys
import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import load_controller


async def child(*, noisy=False):
    code = "import os,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint('ready',flush=True)\n"
    code += "while True:\n os.write(1,b'x'*65536)\n os.write(2,b'y'*65536)\n" if noisy else "time.sleep(60)\n"
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", code,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if await asyncio.wait_for(proc.stdout.readline(), 5) != b"ready\n":
        raise AssertionError("child did not reach TERM-ignore barrier")
    return proc


async def reap_if_needed(proc):
    if proc.returncode is None:
        proc.kill()
    await asyncio.wait_for(proc.communicate(), 5)


class SubprocessCleanupReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_term_wait_and_first_kill_errors_do_not_skip_final_kill_or_reap(self):
        with load_controller() as m:
            proc = await child()
            term_error = OSError(errno.EIO, "secondary TERM EIO")
            wait_error = OSError(errno.EIO, "secondary wait EIO")
            kill_error = OSError(errno.EIO, "secondary first KILL EIO")
            real_wait, real_kill = proc.wait, proc.kill
            waits, kills = 0, 0

            async def wait():
                nonlocal waits
                waits += 1
                if waits == 1:
                    raise wait_error
                return await real_wait()

            def kill():
                nonlocal kills
                kills += 1
                if kills == 1:
                    raise kill_error
                real_kill()

            try:
                with patch.object(proc, "terminate", side_effect=term_error), \
                     patch.object(proc, "wait", side_effect=wait), patch.object(proc, "kill", side_effect=kill):
                    with self.assertRaises(OSError) as raised:
                        await m._terminate_subprocess(proc, drain_streams=(proc.stdout, proc.stderr),
                                                      grace_seconds=.02, reap_seconds=.02)
                self.assertIs(raised.exception, term_error)
                self.assertTrue(any(repr(wait_error) in note for note in raised.exception.__notes__))
                self.assertTrue(any(repr(kill_error) in note for note in raised.exception.__notes__))
                self.assertEqual(kills, 2)
                self.assertEqual(proc.returncode, -signal.SIGKILL)
                self.assertFalse(Path(f"/proc/{proc.pid}").exists())
            finally:
                await reap_if_needed(proc)

    async def test_cli_primary_exception_remains_authoritative_after_cleanup_failure(self):
        with load_controller() as m:
            proc = await child()
            primary = ValueError("primary CLI communication failure")
            secondary = OSError(errno.EIO, "secondary terminate failure")
            real_cleanup = m._terminate_subprocess

            async def quick_cleanup(process, **options):
                return await real_cleanup(process, **options, grace_seconds=.02, reap_seconds=.2)

            try:
                with patch.object(m.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)), \
                     patch.object(proc, "communicate", AsyncMock(side_effect=primary)), \
                     patch.object(proc, "terminate", side_effect=secondary), \
                     patch.object(m, "_terminate_subprocess", side_effect=quick_cleanup):
                    with self.assertRaises(ValueError) as raised:
                        await m.run_cli_envelope("render", "run", "--print-prompt")
                self.assertIs(raised.exception, primary)
                self.assertTrue(any(repr(secondary) in note for note in primary.__notes__))
                self.assertEqual(proc.returncode, -signal.SIGKILL)
                self.assertFalse(Path(f"/proc/{proc.pid}").exists())
            finally:
                await reap_if_needed(proc)

    async def test_repeated_cli_cancellation_waits_for_term_ignoring_child_to_be_reaped(self):
        with load_controller() as m:
            proc = await child()
            communicate_started, cleanup_waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            real_wait, real_communicate = proc.wait, proc.communicate
            real_cleanup = m._terminate_subprocess
            cleanup_waits = 0

            async def communicate():
                communicate_started.set()
                return await real_communicate()

            async def wait():
                nonlocal cleanup_waits
                cleanup_waits += 1
                if cleanup_waits == 1:
                    cleanup_waiting.set()
                    await release.wait()
                return await real_wait()

            async def quick_cleanup(process, **options):
                return await real_cleanup(process, **options, grace_seconds=.2, reap_seconds=.2)

            try:
                with patch.object(m.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)), \
                     patch.object(proc, "communicate", side_effect=communicate), \
                     patch.object(proc, "wait", side_effect=wait), \
                     patch.object(m, "_terminate_subprocess", side_effect=quick_cleanup):
                    runner = asyncio.create_task(m.run_cli_envelope("render", "run", "--print-prompt"))
                    await asyncio.wait_for(communicate_started.wait(), 5)
                    runner.cancel("primary cancellation")
                    await asyncio.wait_for(cleanup_waiting.wait(), 5)
                    runner.cancel("secondary cancellation")
                    await asyncio.sleep(0)
                    self.assertFalse(runner.done())
                    self.assertIsNone(proc.returncode)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError) as raised:
                        await asyncio.wait_for(runner, 5)
                self.assertEqual(raised.exception.args, ("primary cancellation",))
                self.assertTrue(any("secondary cancellation" in note for note in raised.exception.__notes__))
                self.assertEqual(proc.returncode, -signal.SIGKILL)
                self.assertFalse(Path(f"/proc/{proc.pid}").exists())
            finally:
                release.set()
                await reap_if_needed(proc)

    async def test_full_stdout_and_stderr_buffers_do_not_prevent_reap(self):
        with load_controller() as m:
            proc = await child(noisy=True)
            try:
                async with asyncio.timeout(5):
                    while len(proc.stdout._buffer) <= proc.stdout._limit:
                        await asyncio.sleep(.01)
                await asyncio.wait_for(m._terminate_subprocess(
                    proc, drain_streams=(proc.stdout, proc.stderr), grace_seconds=.02, reap_seconds=.2), 5)
                self.assertEqual(proc.returncode, -signal.SIGKILL)
                self.assertFalse(Path(f"/proc/{proc.pid}").exists())
            finally:
                await reap_if_needed(proc)


if __name__ == "__main__":
    unittest.main()
