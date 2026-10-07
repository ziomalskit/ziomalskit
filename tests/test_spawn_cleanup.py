"""Real launcher children must not survive failed initial pidfd capture."""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

from h3.scripts import process_identity

ROOT = Path(__file__).resolve().parents[1]


class SpawnCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aj-spawn-cleanup-")
        self.root = Path(self.temporary.name)
        self.args = argparse.Namespace(pid=None, pid_file=self.root / "render.pid", service="render",
            python=sys.executable, comfy_root=str(self.root), panel_root=str(self.root), port=8188,
            log_file=self.root / "worker.log")
        self.foreign = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
        self.children = []
        self.real_popen = subprocess.Popen
        self.real_send = subprocess.Popen.send_signal
        self.signals = []

    def tearDown(self):
        for child in [*self.children, self.foreign]:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        self.temporary.cleanup()

    def command(self, ignore_term=False):
        ready = self.root / "ready"
        ready.unlink(missing_ok=True)
        (self.root / "main.py").write_text("import signal,time\nfrom pathlib import Path\n" +
            ("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else "") +
            f"Path({str(ready)!r}).touch()\ntime.sleep(60)\n")
        return [sys.executable, "main.py", "--port", "8188"]

    def spawn(self, *args, **kwargs):
        child = self.real_popen(*args, **kwargs)
        self.children.append(child)
        deadline = time.monotonic() + 5
        while not (self.root / "ready").exists():
            if time.monotonic() >= deadline:
                self.fail("owned child did not install its signal handler")
            time.sleep(.01)
        return child

    def send(self, child, sig):
        self.signals.append((child.pid, sig))
        return self.real_send(child, sig)

    def assert_reaped(self, child):
        self.assertIsNotNone(child.returncode)
        self.assertFalse(Path(f"/proc/{child.pid}").exists())
        with self.assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        self.assertFalse(self.args.pid_file.exists())
        self.assertEqual(list(self.root.glob(".service-pid-*")), [])
        self.assertIsNone(self.foreign.poll())

    def assert_primary_failure(self, primary, secondary, command):
        try:
            process_identity.launch_process(self.args, command)
        except BaseException as raised:
            self.assertIs(raised, primary)
            self.assertIs(type(raised), type(primary))
            self.assertEqual(raised.errno, primary.errno)
            self.assertTrue(any(repr(secondary) in note for note in raised.__notes__))
            # Bare re-raise retains the registration frame without adding a
            # second record_process frame at the cleanup/re-raise location.
            frames = traceback.extract_tb(raised.__traceback__)
            self.assertEqual(sum(frame.name == "record_process" for frame in frames), 1)
        else:
            self.fail("registration failure was not raised")

    def failed_capture(self, ignore_term):
        error = OSError(errno.EMFILE, "initial pidfd capture failed")
        # A stale record must never supply the process to terminate.
        self.args.pid_file.write_text(json.dumps({"pid": self.foreign.pid, "service": "render"}))
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "pidfd_open", side_effect=error) as capture, \
             patch.object(process_identity, "terminate_owned_process") as pidfd_cleanup, \
             patch.object(self.real_popen, "send_signal", lambda child, sig: self.send(child, sig)):
            with self.assertRaises(OSError) as raised:
                process_identity.launch_process(self.args, self.command(ignore_term))
        self.assertIs(raised.exception, error)
        child = self.children[-1]
        capture.assert_called_once_with(child.pid)
        pidfd_cleanup.assert_not_called()
        expected = [signal.SIGTERM, signal.SIGKILL] if ignore_term else [signal.SIGTERM]
        self.assertEqual(self.signals, [(child.pid, sig) for sig in expected])
        self.assertEqual(child.returncode, -expected[-1])
        self.assert_reaped(child)

    def test_initial_emfile_terminates_and_reaps_only_the_launchers_child(self):
        self.failed_capture(False)

    def test_initial_emfile_escalates_ignored_term_to_kill_and_reaps(self):
        self.failed_capture(True)

    def test_initial_emfile_reaps_already_exited_child_without_signalling(self):
        error = OSError(errno.EMFILE, "capture after fast exit")
        def spawn(*args, **kwargs):
            child = self.real_popen(*args, **kwargs)
            self.children.append(child)
            return child
        def capture(pid):
            deadline = time.monotonic() + 5
            while True:
                raw = Path(f"/proc/{pid}/stat").read_text()
                if raw.rsplit(")", 1)[1].split()[0] == "Z":
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            raise error
        with patch.object(process_identity.subprocess, "Popen", side_effect=spawn), \
             patch.object(os, "pidfd_open", side_effect=capture), \
             patch.object(self.real_popen, "send_signal", lambda child, sig: self.send(child, sig)):
            with self.assertRaises(OSError) as raised:
                process_identity.launch_process(self.args, [sys.executable, "-c", "pass"])
        self.assertIs(raised.exception, error)
        self.assertEqual(self.signals, [])
        self.assert_reaped(self.children[-1])

    def test_post_capture_failure_keeps_original_pidfd_cleanup_without_double_signal(self):
        real_capture = os.pidfd_open
        real_pidfd_send = signal.pidfd_send_signal
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                opened, sent = [], []
                error = OSError(errno.EIO, "registration file fsync failed")
                def capture(pid):
                    descriptor = real_capture(pid)
                    opened.append((pid, descriptor))
                    return descriptor
                def send(descriptor, sig, *args):
                    sent.append((descriptor, sig))
                    return real_pidfd_send(descriptor, sig, *args)
                with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
                     patch.object(os, "pidfd_open", side_effect=capture), \
                     patch.object(os, "fsync", side_effect=error), \
                     patch.object(signal, "pidfd_send_signal", side_effect=send), \
                     patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
                    with self.assertRaises(OSError) as raised:
                        process_identity.launch_process(self.args, self.command(ignore_term))
                self.assertIs(raised.exception, error)
                child = self.children[-1]
                self.assertEqual(len(opened), 1)
                self.assertEqual(opened[0][0], child.pid)
                destructive = [(fd, sig) for fd, sig in sent if sig in (signal.SIGTERM, signal.SIGKILL)]
                expected = [signal.SIGTERM, signal.SIGKILL] if ignore_term else [signal.SIGTERM]
                self.assertEqual(destructive, [(opened[0][1], sig) for sig in expected])
                self.assert_reaped(child)

    def test_launcher_cleans_child_when_lower_pidfd_cleanup_leaves_it_alive(self):
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                self.signals.clear()
                error = OSError(errno.EIO, "registration failed after capture")
                with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
                     patch.object(os, "fsync", side_effect=error), \
                     patch.object(process_identity, "terminate_owned_process", return_value=None) as lower_cleanup, \
                     patch.object(self.real_popen, "send_signal", lambda child, sig: self.send(child, sig)):
                    with self.assertRaises(OSError) as raised:
                        process_identity.launch_process(self.args, self.command(ignore_term))
                self.assertIs(raised.exception, error)
                lower_cleanup.assert_called_once()
                child = self.children[-1]
                expected = [signal.SIGTERM, signal.SIGKILL] if ignore_term else [signal.SIGTERM]
                self.assertEqual(self.signals, [(child.pid, sig) for sig in expected])
                self.assert_reaped(child)

    def test_launcher_retries_only_its_own_temporary_artifact_cleanup(self):
        other = self.root / ".service-pid-other-service"
        other.write_text("unrelated registration")
        primary = OSError(errno.EIO, "primary registration fsync failed")
        secondary = OSError(errno.EIO, "secondary temporary unlink failed once")
        real_unlink = os.unlink
        attempted = []
        def unlink(path, *args, **kwargs):
            if Path(path).name.startswith(".service-pid-") and Path(path) != other and not attempted:
                attempted.append(Path(path))
                raise secondary
            return real_unlink(path, *args, **kwargs)
        try:
            with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
                 patch.object(os, "fsync", side_effect=primary), \
                 patch.object(os, "unlink", side_effect=unlink), \
                 patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
                self.assert_primary_failure(primary, secondary, self.command())
            self.assertEqual(len(attempted), 1)
            self.assertFalse(attempted[0].exists())
            self.assertEqual(other.read_text(), "unrelated registration")
            other.unlink()
            self.assert_reaped(self.children[-1])
        finally:
            other.unlink(missing_ok=True)

    def test_registration_fsync_error_survives_pidfd_cleanup_error_and_fallback_reaps(self):
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                self.signals.clear()
                primary = OSError(errno.EIO, "primary registration fsync failed")
                secondary = OSError(errno.EBADF, "secondary pidfd termination failed")
                with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
                     patch.object(os, "fsync", side_effect=primary), \
                     patch.object(process_identity, "terminate_owned_process", side_effect=secondary) as lower_cleanup, \
                     patch.object(self.real_popen, "send_signal", lambda child, sig: self.send(child, sig)):
                    self.assert_primary_failure(primary, secondary, self.command(ignore_term))
                lower_cleanup.assert_called_once()
                child = self.children[-1]
                expected = [signal.SIGTERM, signal.SIGKILL] if ignore_term else [signal.SIGTERM]
                self.assertEqual(self.signals, [(child.pid, sig) for sig in expected])
                self.assert_reaped(child)

    def test_registration_error_survives_launcher_fallback_cleanup_error(self):
        primary = OSError(errno.EIO, "primary registration fsync failed")
        secondary = OSError(errno.EIO, "secondary fallback wait failed after reaping")
        real_cleanup = process_identity._terminate_launched_child
        def cleanup(child):
            real_cleanup(child)
            raise secondary
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "fsync", side_effect=primary), \
             patch.object(process_identity, "terminate_owned_process", return_value=None), \
             patch.object(process_identity, "_terminate_launched_child", side_effect=cleanup):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assert_reaped(self.children[-1])

    def test_registration_fsync_error_survives_pidfile_unlink_error(self):
        primary = OSError(errno.EIO, "primary registration fsync failed")
        secondary = OSError(errno.EACCES, "secondary pidfile unlink failed once")
        real_unlink = os.unlink
        attempts = []
        self.args.pid_file.write_text(json.dumps({"pid": self.foreign.pid, "service": "render"}))
        def unlink(path, *args, **kwargs):
            if Path(path) == self.args.pid_file:
                attempts.append(Path(path))
                if len(attempts) == 1:
                    raise secondary
            return real_unlink(path, *args, **kwargs)
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "fsync", side_effect=primary), patch.object(os, "unlink", side_effect=unlink), \
             patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assertEqual(len(attempts), 2)
        self.assert_reaped(self.children[-1])

    def test_registration_fsync_error_survives_pidfd_close_error(self):
        primary = OSError(errno.EIO, "primary registration fsync failed")
        secondary = OSError(errno.EBADF, "secondary pidfd close failed")
        real_capture, real_close = os.pidfd_open, os.close
        descriptors, closed = [], []
        def capture(pid):
            descriptor = real_capture(pid)
            descriptors.append(descriptor)
            return descriptor
        def close(fd):
            real_close(fd)
            if fd in descriptors:
                closed.append(fd)
                raise secondary
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "pidfd_open", side_effect=capture), patch.object(os, "fsync", side_effect=primary), \
             patch.object(os, "close", side_effect=close), \
             patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assertEqual(closed, descriptors)
        self.assert_reaped(self.children[-1])

    def test_registration_directory_fsync_error_survives_directory_close_error(self):
        primary = OSError(errno.EIO, "primary directory fsync failed")
        secondary = OSError(errno.EBADF, "secondary directory close failed")
        real_sync, real_close = os.fsync, os.close
        directory_fds = []
        def sync(fd):
            if os.readlink(f"/proc/self/fd/{fd}") == str(self.root):
                directory_fds.append(fd)
                raise primary
            return real_sync(fd)
        def close(fd):
            real_close(fd)
            if fd in directory_fds:
                raise secondary
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "fsync", side_effect=sync), patch.object(os, "close", side_effect=close), \
             patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assertEqual(len(directory_fds), 1)
        self.assert_reaped(self.children[-1])

    def test_registration_file_fsync_error_survives_file_close_error(self):
        primary = OSError(errno.EIO, "primary registration fsync failed")
        secondary = OSError(errno.EIO, "secondary registration stream close failed")
        real_fdopen = os.fdopen
        closed = []
        class Stream:
            def __init__(self, handle):
                self.handle = handle
            def __getattr__(self, name):
                return getattr(self.handle, name)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
            def close(self):
                self.handle.close()
                closed.append(True)
                raise secondary
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "fdopen", side_effect=lambda *args, **kwargs: Stream(real_fdopen(*args, **kwargs))), \
             patch.object(os, "fsync", side_effect=primary), \
             patch.object(self.real_popen, "send_signal", side_effect=AssertionError("Popen must not double-signal")):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assertEqual(closed, [True])
        self.assert_reaped(self.children[-1])

    def test_cleanup_failures_do_not_skip_later_cleanup_steps(self):
        primary = OSError(errno.EIO, "primary registration fsync failed")
        errors = [OSError(errno.EBADF, "pidfd termination"), OSError(errno.EACCES, "pidfile unlink"),
                  OSError(errno.EIO, "temp unlink"), OSError(errno.EBADF, "pidfd close")]
        real_capture, real_close, real_unlink = os.pidfd_open, os.close, os.unlink
        descriptors, attempts, closed = [], [], []
        def capture(pid):
            fd = real_capture(pid)
            descriptors.append(fd)
            return fd
        def unlink(path, *args, **kwargs):
            path = Path(path)
            if path == self.args.pid_file or path.name.startswith(".service-pid-"):
                if path not in attempts:
                    attempts.append(path)
                    raise errors[1 if path == self.args.pid_file else 2]
            return real_unlink(path, *args, **kwargs)
        def close(fd):
            real_close(fd)
            if fd in descriptors:
                closed.append(fd)
                raise errors[3]
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "fsync", side_effect=primary), patch.object(os, "pidfd_open", side_effect=capture), \
             patch.object(process_identity, "terminate_owned_process", side_effect=errors[0]), \
             patch.object(os, "unlink", side_effect=unlink), patch.object(os, "close", side_effect=close):
            self.assert_primary_failure(primary, errors[0], self.command())
        self.assertEqual(len(attempts), 2)
        self.assertEqual(closed, descriptors)
        for error in errors:
            self.assertTrue(any(repr(error) in note for note in primary.__notes__))
        self.assert_reaped(self.children[-1])

    def test_successful_registration_still_records_live_owned_child(self):
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(process_identity, "terminate_owned_process") as lower_cleanup, \
             patch.object(self.real_popen, "send_signal", side_effect=AssertionError("successful registration must not signal")):
            process_identity.launch_process(self.args, self.command())
        lower_cleanup.assert_not_called()
        child = self.children[-1]
        self.assertIsNone(child.poll())
        pid, descriptor = process_identity.checked_pidfd(self.args)
        try:
            self.assertEqual(pid, child.pid)
            self.assertEqual(json.loads(self.args.pid_file.read_text())["pid"], child.pid)
        finally:
            os.close(descriptor)
        self.assertEqual(list(self.root.glob(".service-pid-*")), [])
        self.assertIsNone(self.foreign.poll())

    def test_successful_registration_exposes_cleanup_failure_without_orphan(self):
        primary = OSError(errno.EIO, "first cleanup failure after successful registration")
        secondary = OSError(errno.EBADF, "second cleanup failure closing pidfd")
        real_capture, real_close = os.pidfd_open, os.close
        descriptors = []
        def capture(pid):
            fd = real_capture(pid)
            descriptors.append(fd)
            return fd
        def close(fd):
            real_close(fd)
            if fd in descriptors:
                raise secondary
        real_unlink = os.unlink
        attempts = []
        def unlink(path, *args, **kwargs):
            if Path(path).name.startswith(".service-pid-") and not attempts:
                attempts.append(Path(path))
                raise primary
            return real_unlink(path, *args, **kwargs)
        with patch.object(process_identity.subprocess, "Popen", side_effect=self.spawn), \
             patch.object(os, "pidfd_open", side_effect=capture), patch.object(os, "close", side_effect=close), \
             patch.object(os, "unlink", side_effect=unlink):
            self.assert_primary_failure(primary, secondary, self.command())
        self.assertEqual(len(attempts), 1)
        self.assert_reaped(self.children[-1])

    def test_initial_capture_failure_exits_cli_failure_after_reaping_child(self):
        # A real helper must exit, rather than merely return from a mocked
        # launcher. It reports ECHILD after Popen cleanup has reaped its child.
        code = r'''
import errno,json,os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from h3.scripts import process_identity as identity
root=Path(sys.argv[1]);ignore=sys.argv[2]=='ignore';foreign=int(sys.argv[3])
real_spawn=subprocess.Popen;real_capture=os.pidfd_open;children=[]
ready=root/'ready';ready.unlink(missing_ok=True)
(root/'main.py').write_text('import signal,time\nfrom pathlib import Path\n'+
 ('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore else '')+
 f'Path({str(ready)!r}).touch()\ntime.sleep(60)\n')
def spawn(*a,**kw):
 child=real_spawn(*a,**kw);children.append(child)
 deadline=time.monotonic()+5
 while not ready.exists():
  assert time.monotonic()<deadline
  time.sleep(.01)
 return child
def capture(pid):
 if pid==os.getpid():return real_capture(pid)
 raise OSError(errno.EMFILE,'initial child pidfd capture')
sys.argv=['identity','launch','--pid-file',str(root/'render.pid'),'--service','render',
 '--python',sys.executable,'--comfy-root',str(root),'--panel-root',str(root),'--port','8188',
 '--log-file',str(root/'worker.log'),'--',sys.executable,'main.py','--port','8188']
(root/'render.pid').write_text(json.dumps({'pid':foreign,'service':'render'}))
with patch.object(identity.subprocess,'Popen',side_effect=spawn),patch.object(os,'pidfd_open',side_effect=capture):
 result=identity.main()
child=children[0]
assert child.returncode is not None
try:os.waitpid(child.pid,os.WNOHANG)
except ChildProcessError:reaped=True
else:reaped=False
(root/'result.json').write_text(json.dumps({'pid':child.pid,'returncode':child.returncode,'reaped':reaped}))
raise SystemExit(result)
'''
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                helper = subprocess.run([sys.executable, "-c", code, str(self.root),
                    "ignore" if ignore_term else "normal", str(self.foreign.pid)], cwd=ROOT,
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(helper.returncode, 1, helper.stdout + helper.stderr)
                result = json.loads((self.root / "result.json").read_text())
                self.assertTrue(result["reaped"])
                self.assertEqual(result["returncode"], -signal.SIGKILL if ignore_term else -signal.SIGTERM)
                self.assertFalse(Path(f"/proc/{result['pid']}").exists())
                self.assertFalse(self.args.pid_file.exists())
                self.assertEqual(list(self.root.glob(".service-pid-*")), [])
                self.assertIsNone(self.foreign.poll())


if __name__ == "__main__":
    unittest.main()
