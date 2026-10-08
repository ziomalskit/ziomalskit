"""Real-process ownership checks and pre-exec launch crash boundaries."""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from h3.scripts import process_identity as identity
from tests import test_service_ctl as service_fixtures

ROOT = Path(__file__).resolve().parents[1]


class ProcessReliabilityTests(unittest.TestCase):
    def test_unapproved_interpreter_prefix_cannot_match_the_committed_command(self):
        with tempfile.TemporaryDirectory(prefix="aj-command-pin-") as tmp:
            root = Path(tmp)
            wrapper = root / "selected-python"
            wrapper.write_text(f"#!{sys.executable}\nimport time\nprint('ready', flush=True)\ntime.sleep(60)\n")
            wrapper.chmod(0o755)
            command = [str(wrapper), "main.py", "--port", "8188"]
            child = subprocess.Popen(["unapproved-python-prefix", *command], executable=sys.executable,
                                     cwd=root, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline(), "ready\n")
                snapshot = identity.process_snapshot(child.pid)
                target = identity.expected_launch_argv(command)
                self.assertEqual(target, [sys.executable, *command])
                record = {key: snapshot[key] for key in ("pid", "start_time", "boot_id")}
                record.update(service="render", launch_command=command, target_argv=target,
                              command_sha256=identity.command_digest(target), gate_command_sha256="0" * 64)
                args = argparse.Namespace(pid_file=root / "render.pid", service="render", python=str(wrapper),
                                          comfy_root=str(root), panel_root=str(root), port=8188)
                args.pid_file.write_text(json.dumps(record))
                with patch.object(signal, "pidfd_send_signal") as send:
                    with self.assertRaises(identity.StaleIdentity):
                        identity.checked_pidfd(args, allow_gate=True)
                send.assert_not_called()
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.communicate(timeout=5)

    def test_single_eio_start_and_stop_preserve_the_original_owned_worker(self):
        fixture = service_fixtures.ServiceControlTests()
        fixture.setUp()
        try:
            fixture.environment["PYTHONDONTWRITEBYTECODE"] = "1"
            fixture.finished(fixture.command("start", "render"))
            record = fixture.panel / "state/pids/render.pid"
            original = record.read_text()
            pid = json.loads(original)["pid"]
            marker = fixture.base / "inject-one-eio"
            helper = fixture.panel / "scripts/process_identity.py"
            helper.write_text(f'''import errno,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from h3.scripts import process_identity
read=Path.read_text
marker=Path({str(marker)!r})
def fail_once(path,*args,**kwargs):
 if path==Path({str(record)!r}) and marker.exists():
  marker.unlink()
  raise OSError(errno.EIO,'single PID record read EIO')
 return read(path,*args,**kwargs)
Path.read_text=fail_once
raise SystemExit(process_identity.main())
''')
            for action in ("start", "stop", "restart", "status"):
                with self.subTest(action=action):
                    marker.touch()
                    command = fixture.command(action, "render")
                    out, err = command.communicate(timeout=10)
                    self.assertNotEqual(command.returncode, 0, out + err)
                    self.assertEqual(record.read_text(), original)
                    self.assertEqual(len(fixture.events()), 1)
                    os.kill(pid, 0)
                    check = fixture.command("status", "render")
                    out, err = check.communicate(timeout=10)
                    self.assertEqual(check.returncode, 0, out + err)
                    self.assertIn(f"RUNNING pid={pid}", out)
            fixture.finished(fixture.command("stop", "render"))
        finally:
            fixture.tearDown()

    def test_launcher_cleanup_errors_still_kill_and_reap_only_its_child(self):
        with tempfile.TemporaryDirectory(prefix="aj-launch-cleanup-") as tmp:
            root = Path(tmp)
            args = argparse.Namespace(pid_file=root / "render.pid", service="render", python=sys.executable,
                                      comfy_root=str(root), panel_root=str(root), port=8188,
                                      log_file=root / "worker.log")
            primary = OSError(errno.EMFILE, "initial capture failed")
            term_error = OSError(errno.EIO, "TERM failed")
            wait_error = OSError(errno.EIO, "wait failed")
            kill_error = OSError(errno.EIO, "first KILL failed")
            real_spawn = subprocess.Popen
            children = []
            foreign = real_spawn([sys.executable, "-c", "import time;time.sleep(60)"])

            def spawning(*argv, **options):
                options["preexec_fn"] = lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)
                child = real_spawn(*argv, **options)
                children.append(child)
                return child

            def capture(_pid):
                child = children[0]
                real_wait, real_kill = child.wait, child.kill
                waits, kills = 0, 0

                def waiting(*argv, **options):
                    nonlocal waits
                    waits += 1
                    if waits <= 2:
                        raise wait_error
                    return real_wait(*argv, **options)

                def killing():
                    nonlocal kills
                    kills += 1
                    if kills == 1:
                        raise kill_error
                    real_kill()

                child.terminate = lambda: (_ for _ in ()).throw(term_error)
                child.wait, child.kill = waiting, killing
                raise primary

            try:
                with patch.object(identity.subprocess, "Popen", side_effect=spawning), \
                        patch.object(os, "pidfd_open", side_effect=capture):
                    with self.assertRaises(OSError) as raised:
                        identity.launch_process(args, [sys.executable, "main.py", "--port", "8188"])
                self.assertIs(raised.exception, primary)
                for secondary in (term_error, wait_error, kill_error):
                    self.assertTrue(any(repr(secondary) in note for note in primary.__notes__))
                self.assertEqual(children[0].returncode, -signal.SIGKILL)
                self.assertFalse(Path(f"/proc/{children[0].pid}").exists())
                self.assertIsNone(foreign.poll())
                self.assertFalse(args.pid_file.exists())
            finally:
                for child in [*children, foreign]:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)

    def test_unowned_listener_cannot_supply_false_start_readiness(self):
        fixture = service_fixtures.ServiceControlTests()
        fixture.setUp()
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                fixture.environment["RENDER_PORT"] = str(listener.getsockname()[1])
                record = fixture.panel / "state/pids/render.pid"
                record.parent.mkdir(parents=True)
                record.write_text("invalid old PID record")
                command = fixture.command("start", "render")
                out, err = command.communicate(timeout=10)
                self.assertNotEqual(command.returncode, 0, out + err)
                self.assertEqual(record.read_text(), "invalid old PID record")
                self.assertEqual(fixture.events(), [])
                self.assertIn("unused render port", err)
        finally:
            fixture.tearDown()

    def test_pidfd_resource_and_proc_errors_are_indeterminate(self):
        fixture = service_fixtures.ServiceControlTests()
        fixture.setUp()
        try:
            fixture.finished(fixture.command("start", "render"))
            args = argparse.Namespace(pid_file=fixture.panel / "state/pids/render.pid", service="render",
                                      python=fixture.environment["COMFY_PYTHON"], comfy_root=str(fixture.comfy),
                                      panel_root=str(fixture.panel), port=8188)
            original = args.pid_file.read_text()
            for error in (OSError(errno.EMFILE, "pidfd EMFILE"), OSError(errno.ENFILE, "pidfd ENFILE")):
                with self.subTest(error=error.errno), patch.object(os, "pidfd_open", side_effect=error):
                    with self.assertRaises(OSError) as raised:
                        identity.checked_pidfd(args)
                    self.assertIs(raised.exception, error)
            with patch.object(identity, "process_snapshot", side_effect=FileNotFoundError("transient /proc")), \
                    patch.object(signal, "pidfd_send_signal") as send:
                with self.assertRaises(identity.IndeterminateIdentity):
                    identity.checked_pidfd(args)
                send.assert_not_called()
            self.assertEqual(args.pid_file.read_text(), original)
            pid, descriptor = identity.checked_pidfd(args)
            os.close(descriptor)
            self.assertEqual(pid, json.loads(original)["pid"])
        finally:
            fixture.tearDown()

    def test_log_close_eio_reaps_gate_even_when_term_is_ignored(self):
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term), tempfile.TemporaryDirectory(prefix="aj-log-gate-") as tmp:
                root = Path(tmp)
                ready = root / "service-ran"
                (root / "main.py").write_text(f"from pathlib import Path\nPath({str(ready)!r}).touch()\nimport time\ntime.sleep(60)\n")
                args = argparse.Namespace(pid_file=root / "render.pid", service="render", python=sys.executable,
                    comfy_root=str(root), panel_root=str(root), port=8188, log_file=root / "worker.log")
                real_open, real_spawn = Path.open, subprocess.Popen
                primary = OSError(errno.EIO, "log close EIO")
                children = []
                foreign = real_spawn([sys.executable, "-c", "import time;time.sleep(60)"])
                class FailingLog:
                    def __init__(self, handle):
                        self.handle = handle
                    def fileno(self):
                        return self.handle.fileno()
                    def close(self):
                        self.handle.close()
                        raise primary
                def opening(path, *a, **kw):
                    handle = real_open(path, *a, **kw)
                    return FailingLog(handle) if path == args.log_file else handle
                def spawning(*a, **kw):
                    if ignore_term:
                        kw["preexec_fn"] = lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    child = real_spawn(*a, **kw)
                    children.append(child)
                    return child
                try:
                    with patch.object(Path, "open", opening), patch.object(identity.subprocess, "Popen", spawning):
                        with self.assertRaises(OSError) as raised:
                            identity.launch_process(args, [sys.executable, "main.py", "--port", "8188"])
                    self.assertIs(raised.exception, primary)
                    self.assertEqual(children[0].returncode, -signal.SIGKILL if ignore_term else -signal.SIGTERM)
                    self.assertFalse(Path(f"/proc/{children[0].pid}").exists())
                    self.assertFalse(ready.exists())
                    self.assertFalse(args.pid_file.exists())
                    self.assertIsNone(foreign.poll())
                finally:
                    for child in [*children, foreign]:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=5)

    def test_sigkill_launch_windows_never_run_the_unowned_service(self):
        # Adopt only this test's known grandchildren, so a killed helper's gate
        # can be reaped deterministically without relying on container PID 1.
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        try:
            for window in ("after_spawn", "directory_fsync", "before_commit", "successful"):
                with self.subTest(window=window), tempfile.TemporaryDirectory(prefix="aj-kill-gate-") as tmp:
                    root = Path(tmp)
                    marker, ready = root / "child.pid", root / "service-ran"
                    (root / "main.py").write_text(f"from pathlib import Path\nPath({str(ready)!r}).touch()\nimport time\ntime.sleep(60)\n")
                    code = r'''
import argparse,json,os,signal,stat,subprocess,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from h3.scripts import process_identity as identity
root=Path(sys.argv[2]);window=sys.argv[3]
args=argparse.Namespace(pid_file=root/'render.pid',service='render',python=sys.executable,
 comfy_root=str(root),panel_root=str(root),port=8188,log_file=root/'worker.log')
spawn=subprocess.Popen;fsync=os.fsync;write=os.write
def launched(*a,**kw):
 child=spawn(*a,**kw);(root/'child.pid').write_text(str(child.pid))
 if window=='after_spawn':os.kill(os.getpid(),signal.SIGKILL)
 return child
def synced(fd):
 if window=='directory_fsync' and stat.S_ISDIR(os.fstat(fd).st_mode):os.kill(os.getpid(),signal.SIGKILL)
 return fsync(fd)
def authorized(fd,data):
 if window=='before_commit' and data==b'C':os.kill(os.getpid(),signal.SIGKILL)
 return write(fd,data)
with patch.object(identity.subprocess,'Popen',launched),patch.object(os,'fsync',synced),patch.object(os,'write',authorized):
 identity.launch_process(args,[sys.executable,'main.py','--port','8188'])
'''
                    child_pid = None
                    try:
                        helper = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT), str(root), window],
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=10)
                        child_pid = int(marker.read_text())
                        deadline = time.monotonic() + 5
                        if window == "successful":
                            self.assertEqual(helper.returncode, 0, helper.stderr)
                            while not ready.exists():
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(.01)
                            args = argparse.Namespace(pid_file=root / "render.pid", service="render", python=sys.executable,
                                comfy_root=str(root), panel_root=str(root), port=8188)
                            pid, descriptor = identity.checked_pidfd(args)
                            self.assertEqual(pid, child_pid)
                            try:
                                identity.terminate_owned_process(descriptor)
                            finally:
                                os.close(descriptor)
                        else:
                            self.assertEqual(helper.returncode, -signal.SIGKILL, helper.stderr)
                            while True:
                                try:
                                    identity.process_snapshot(child_pid)
                                except ProcessLookupError:
                                    break
                                except identity.IndeterminateIdentity:
                                    pass
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(.01)
                            self.assertFalse(ready.exists())
                        waited, _ = os.waitpid(child_pid, 0)
                        self.assertEqual(waited, child_pid)
                        child_pid = None
                    finally:
                        if child_pid is not None:
                            # This PID is our adopted unreaped child, never a
                            # PID inferred from a possibly stale service file.
                            try:
                                os.kill(child_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            os.waitpid(child_pid, 0)
        finally:
            self.assertEqual(libc.prctl(36, previous.value, 0, 0, 0), 0)


if __name__ == "__main__":
    unittest.main()
