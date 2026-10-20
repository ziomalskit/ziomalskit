"""Ordinary pidfds on real local processes; no GPU or kernel-version claims."""
import asyncio
import errno
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from h3.scripts import process_identity as identity
from tests.helpers import load_controller


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('process fixture did not reach barrier')
        time.sleep(.01)


def dead(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return bool(poll.poll(0))


class ProcessCompatibilityTests(unittest.TestCase):
    def test_missing_proc_capability_remains_blocking(self):
        with patch.object(identity, 'group_stat', side_effect=PermissionError('proc unavailable')):
            with self.assertRaises(PermissionError):
                identity.require_group_control()

    def test_captured_descendant_survives_leader_reaping_then_is_cleaned_without_group_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'child'
            code = ("import os,signal,time\nfrom pathlib import Path\n"
                    "if os.fork()==0:\n"
                    " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                    f" Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                    " time.sleep(60)\n"
                    "else:time.sleep(60)\n")
            leader = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
            fd = os.pidfd_open(leader.pid)
            child_fd = group = None
            foreign = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
            try:
                wait_for(ready.exists)
                child_fd = os.pidfd_open(int(ready.read_text()))
                send = signal.pidfd_send_signal
                def ordinary(descriptor, sig, *args):
                    if len(args) > 1 and args[1]:
                        raise OSError(errno.EINVAL, 'group flags unavailable')
                    return send(descriptor, sig, *args)
                with patch.object(signal, 'pidfd_send_signal', side_effect=ordinary), \
                     patch.object(os, 'killpg', side_effect=AssertionError('numeric PGID forbidden')):
                    identity.require_group_control()
                    group = identity.OwnedGroup(leader.pid, fd)
                    group.send(signal.SIGTERM)
                    leader.wait(timeout=5)
                    self.assertFalse(dead(child_fd))
                    group.send(signal.SIGKILL)
                    wait_for(lambda: group.empty())
                    self.assertTrue(dead(child_fd))
                    self.assertIsNone(foreign.poll())
            finally:
                for descriptor in (child_fd, fd):
                    if descriptor is not None:
                        try:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.close(descriptor)
                if group is not None:
                    group.close()
                leader.wait(timeout=5)
                foreign.kill(); foreign.wait(timeout=5)

    def test_last_anchor_reaped_during_scan_without_new_members_is_not_uncertain(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
        fd = os.pidfd_open(proc.pid)
        group = identity.OwnedGroup(proc.pid, fd)
        try:
            with patch.object(group, '_anchored', side_effect=[True, False]), \
                 patch.object(identity, 'group_members', return_value=[]):
                group.refresh()
            self.assertIsNone(proc.poll())
        finally:
            group.close(); os.close(fd)
            proc.kill(); proc.wait(timeout=5)

    def test_new_member_capture_still_requires_an_anchor_after_the_scan(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
        fd = os.pidfd_open(proc.pid)
        group = identity.OwnedGroup(proc.pid, fd)
        extra = os.pidfd_open(proc.pid)
        group.members.clear()  # Simulate an as-yet-uncaptured enumerated member.
        closed = []
        try:
            with patch.object(group, '_anchored', side_effect=[True, False]), \
                 patch.object(identity, 'group_members', return_value=[Path(f'/proc/{proc.pid}')]), \
                 patch.object(os, 'pidfd_open', return_value=extra), \
                 patch.object(os, 'close', side_effect=lambda item: closed.append(item)):
                with self.assertRaises(identity.IndeterminateIdentity):
                    group.refresh()
            self.assertEqual(closed, [extra], 'uncommitted capability must be released')
            self.assertEqual(group.members, {})
            self.assertIsNone(proc.poll())
        finally:
            group.close(); os.close(extra); os.close(fd)
            proc.kill(); proc.wait(timeout=5)

    def test_reused_numeric_group_with_no_matching_anchor_is_uncertain_and_never_signalled(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
        fd = os.pidfd_open(proc.pid)
        group = identity.OwnedGroup(proc.pid, fd)
        try:
            start = group.members[proc.pid][1]
            with patch.object(identity, 'group_stat', return_value=(start + 1, proc.pid, proc.pid, 'S')), \
                 patch.object(identity, 'group_members', return_value=[Path(f'/proc/{proc.pid}')]), \
                 patch.object(signal, 'pidfd_send_signal') as send:
                with self.assertRaises(identity.IndeterminateIdentity):
                    group.send(signal.SIGKILL)
                send.assert_not_called()
            self.assertIsNone(proc.poll())
        finally:
            group.close(); os.close(fd)
            proc.kill(); proc.wait(timeout=5)


class CommandResultCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_vast_result_survives_secondary_reap_error_without_retry(self):
        with load_controller() as controller:
            controller.VAST_CLI = sys.executable
            popen = subprocess.Popen
            processes = []
            def spawn(*args, **kwargs):
                proc = popen(*args, **kwargs)
                processes.append(proc)
                original_wait = proc.wait
                attempted = False
                def wait(*args, **kwargs):
                    nonlocal attempted
                    if not attempted:
                        attempted = True
                        raise OSError(errno.EIO, 'secondary reap failure')
                    return original_wait(*args, **kwargs)
                proc.wait = wait
                return proc
            try:
                with patch.object(controller.subprocess, 'Popen', side_effect=spawn):
                    result = await controller.run_vast_cli('-c', "print('confirmed success')")
                self.assertEqual(result, 'confirmed success')
                self.assertEqual(len(processes), 1, 'confirmed action must not be executed again')
                self.assertEqual(processes[0].returncode, 0, 'the owned child must still be reaped')
                self.assertIn('Confirmed CLI success', controller.vast_control['last_cli_cleanup_warning'])
            finally:
                for proc in processes:
                    proc.wait(timeout=5)


class OptionalTerminalCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_terminal_does_not_probe_optional_group_capability(self):
        with load_controller() as controller, patch.object(controller, 'require_terminal_group_control') as probe:
            with self.assertRaises(controller.HTTPException) as result:
                await controller.api_terminal_session()
            self.assertEqual(result.exception.status_code, 403)
            probe.assert_not_called()
            controller._require_restart_group_control()

    async def test_enabled_terminal_reports_unsupported_capability_without_launch(self):
        with load_controller(terminal_opt_in=True) as controller, \
             patch.object(controller, 'require_terminal_group_control', side_effect=RuntimeError('unsupported')), \
             patch.object(controller.subprocess, 'Popen') as spawn:
            # Exercise the guard independently; production terminal containment stays disabled.
            controller.H3_ENABLE_TERMINAL = True
            with self.assertRaises(controller.HTTPException) as result:
                await controller.api_terminal_session()
            self.assertEqual(result.exception.status_code, 503)
            self.assertEqual(controller.terminal_tokens.pending, {})
            spawn.assert_not_called()
