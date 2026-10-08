"""Restart ownership, real flock cancellation, and descendant cleanup."""
from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
import shlex
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.helpers import load_controller
from tests import test_service_ctl


def live_group_members(group: int) -> list[int]:
    members = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
                members.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return members


async def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("restart test barrier was not reached")
        await asyncio.sleep(.01)


class RestartReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_removes_queued_restart_before_stop_all_releases_flock(self):
        for local_api in (False, True):
            with self.subTest(local_api=local_api):
                fixture = test_service_ctl.ServiceControlTests()
                fixture.setUp()
                release = fixture.base / "release-stop"
                stopping = fixture.base / "stop-holds-lock"
                queued = fixture.base / "restart-entered"
                try:
                    # Keep stop all inside stop_one(render), holding its real
                    # service_ctl flock until controller cleanup has finished.
                    interpreter = Path(fixture.environment["PYTHON_BIN"])
                    source = interpreter.read_text().replace(
                        " record('stop');sys.exit(0)",
                        " record('stop')\n if service=='render':\n"
                        f"  Path({str(stopping)!r}).touch()\n"
                        f"  while not Path({str(release)!r}).exists():time.sleep(.01)\n"
                        " sys.exit(0)")
                    interpreter.write_text(source)
                    await asyncio.to_thread(fixture.finished, fixture.command("start", "all"))
                    stop = fixture.command("stop", "all")
                    await until(stopping.exists)
                    with load_controller() as m, patch.dict(os.environ, fixture.environment):
                        control = fixture.panel / "scripts/service_ctl.sh"
                        if local_api:
                            wrapper = fixture.base / "queued-service-ctl.sh"
                            wrapper.write_text("#!/bin/bash\ntouch " + shlex.quote(str(queued)) +
                                               "\nexec bash " + shlex.quote(str(control)) + ' "$@"\n')
                            m.SERVICE_CTL = str(wrapper)
                            restart = m.restart_local_service("render")
                        else:
                            m.RENDER_RESTART_CMD = "touch " + shlex.quote(str(queued)) + "; exec bash " + shlex.quote(str(control)) + " restart render"
                            restart = m.restart_comfy("render")
                        spawned = []
                        real_popen = subprocess.Popen
                        def capture(*args, **kwargs):
                            child = real_popen(*args, **kwargs)
                            spawned.append(child)
                            return child
                        with patch.object(m.subprocess, "Popen", side_effect=capture), \
                             patch.object(m.os, "killpg", side_effect=AssertionError("numeric PGID signalling is forbidden")):
                            runner = asyncio.create_task(restart)
                            await until(queued.exists)
                            group = spawned[0].pid
                            await until(lambda: len(live_group_members(group)) >= 2)
                            for name in ("prompt_worker_task", "render_worker_task", "vast_guard_task", "recovery_task", "lifecycle_action_task"):
                                setattr(m, name, None)
                            m.render_worker_task = runner
                            await asyncio.wait_for(m.shutdown(), 10)
                            self.assertTrue(runner.cancelled())
                            self.assertIsNotNone(spawned[0].returncode)
                            self.assertEqual(live_group_members(group), [])
                        release.touch()
                        await asyncio.to_thread(fixture.finished, stop)
                        fixture.assert_lock_released()
                        self.assertEqual([(row["event"], row["service"]) for row in fixture.events()],
                                         [(event, service) for event in ("start", "stop") for service in ("render", "prompt", "panel")])
                        self.assertFalse((fixture.panel / "state/pids/render.pid").exists())
                finally:
                    release.touch()
                    await asyncio.to_thread(fixture.tearDown)

    async def test_reaped_leader_and_term_ignoring_descendant_use_original_pidfd_group(self):
        with load_controller() as m, tempfile.TemporaryDirectory(prefix="aj-restart-descendant-") as temporary:
            root = Path(temporary)
            ready = root / "ready"
            descendant = root / "descendant"
            code = ("import os,signal,time\nfrom pathlib import Path\n"
                    "if os.fork():os._exit(0)\n"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                    f"Path({str(descendant)!r}).write_text(str(os.getpid()))\n"
                    f"Path({str(ready)!r}).touch()\n"
                    "time.sleep(60)\n")
            spawned = []
            descendant_fd = None
            real_popen = subprocess.Popen
            foreign = real_popen([sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True)
            def capture(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                spawned.append(child)
                return child
            try:
                with patch.object(m.subprocess, "Popen", side_effect=capture), \
                     patch.object(m.os, "killpg", side_effect=AssertionError("must not resolve a numeric replacement PGID")):
                    runner = asyncio.create_task(m._run_owned_restart([sys.executable, "-c", code]))
                    await until(ready.exists)
                    descendant_fd = os.pidfd_open(int(descendant.read_text()))
                    self.assertEqual(await asyncio.to_thread(spawned[0].wait, timeout=5), 0)
                    runner.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await runner
                    self.assertEqual(live_group_members(spawned[0].pid), [])
                    exited = select.poll()
                    exited.register(descendant_fd, select.POLLIN)
                    self.assertTrue(exited.poll(0), "the original descendant must have exited")
                    self.assertIsNone(foreign.poll())
            finally:
                if descendant_fd is not None:
                    os.close(descendant_fd)
                foreign.kill()
                foreign.wait(timeout=5)

    async def test_cleanup_errors_and_repeated_cancellation_preserve_primary_and_still_kill(self):
        with load_controller() as m, tempfile.TemporaryDirectory(prefix="aj-restart-primary-") as temporary:
            ready = Path(temporary) / "ready"
            code = ("import signal,time\nfrom pathlib import Path\n"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                    f"Path({str(ready)!r}).touch()\ntime.sleep(60)\n")
            entered, release = asyncio.Event(), asyncio.Event()
            real_cleanup = m._cleanup_owned_restart
            real_send = signal.pidfd_send_signal
            failed_term = OSError(errno.EIO, "secondary TERM failure")
            failed_kill = OSError(errno.EIO, "secondary first KILL failure")
            signals = []
            async def cleanup(*args):
                entered.set()
                await release.wait()
                return await real_cleanup(*args)
            def send(fd, sig, *args):
                signals.append(sig)
                if sig == signal.SIGTERM:
                    raise failed_term
                if sig == signal.SIGKILL and signals.count(signal.SIGKILL) == 1:
                    raise failed_kill
                return real_send(fd, sig, *args)
            with patch.object(m, "_cleanup_owned_restart", side_effect=cleanup), \
                 patch.object(m.signal, "pidfd_send_signal", side_effect=send):
                runner = asyncio.create_task(m._run_owned_restart([sys.executable, "-c", code]))
                await until(ready.exists)
                runner.cancel()
                await entered.wait()
                runner.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await asyncio.wait_for(runner, 10)
                self.assertTrue(any(repr(failed_term) in note for note in raised.exception.__notes__))
                self.assertTrue(any(repr(failed_kill) in note for note in raised.exception.__notes__))
                self.assertEqual(signals.count(signal.SIGKILL), 2)

    async def test_unsupported_group_signalling_fails_before_any_spawn(self):
        with load_controller() as m, \
             patch.object(m.signal, "pidfd_send_signal", side_effect=OSError(errno.EINVAL, "unsupported group flag")), \
             patch.object(m.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "kernel >= 6.9"):
                await m.restart_local_service("render")
            spawn.assert_not_called()

    async def test_pidfd_capture_failure_reaps_gate_without_executing_command(self):
        with load_controller() as m, tempfile.TemporaryDirectory(prefix="aj-restart-capture-") as temporary:
            effect = Path(temporary) / "effect"
            real_open, real_popen = os.pidfd_open, subprocess.Popen
            spawned = []
            def capture(pid):
                if pid == os.getpid():
                    return real_open(pid)
                raise OSError(errno.EMFILE, "child pidfd capture failed")
            def spawn(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                spawned.append(child)
                return child
            with patch.object(m.os, "pidfd_open", side_effect=capture), \
                 patch.object(m.subprocess, "Popen", side_effect=spawn):
                with self.assertRaises(OSError) as raised:
                    await m._run_owned_restart(["touch", str(effect)])
                self.assertEqual(raised.exception.errno, errno.EMFILE)
                self.assertIsNotNone(spawned[0].returncode)
                self.assertFalse(Path(f"/proc/{spawned[0].pid}").exists())
                self.assertFalse(effect.exists())

    async def test_success_and_nonzero_restart_results_remain_reported(self):
        with load_controller() as m:
            self.assertEqual(await m._run_owned_restart([sys.executable, "-c", "print('done')"]), (0, b"done\n", b""))
            m.RENDER_RESTART_CMD = "exit 7"
            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                await m.restart_comfy("render")


if __name__ == "__main__":
    unittest.main()
