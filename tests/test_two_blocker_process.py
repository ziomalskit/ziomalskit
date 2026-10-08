"""Actual service stop owners retain pidfds across outer restart cancellation."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

from h3.scripts import process_identity as identity
from tests.helpers import load_controller
from tests import test_step2_reliability as step2

exited = step2.exited


async def until(predicate, seconds=8):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.01)


class ServiceStopCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def cancel_during_grace(self, *, repeated=False, first_kill_error=False, retain_owner=False,
                                  scan_error=False):
        fixture = step2.ServiceStep2Tests()
        fixture.setUp()
        foreign = runner = None
        owner_fd = None
        spawned = []
        try:
            fixture.f.environment['H3_SERVICE_TERM_TIMEOUT'] = '10'
            fixture.worker('descendant')
            owner_marker = fixture.f.base / 'stop-owner'
            term_marker = fixture.f.base / 'service-term'
            failed_marker = fixture.f.base / 'failed-kill'
            scan_marker = fixture.f.base / 'failed-scan'
            removed_marker = fixture.f.base / 'confirmed-unlink'
            allow_kill = fixture.f.base / 'allow-owner-kill'
            record = fixture.f.panel / 'state/pids/render.pid'
            script = fixture.f.panel / 'scripts/process_identity.py'
            # Fault/observability hooks exist only in this disposable CLI copy.
            injection = f'''
if 'stop' in sys.argv:
    _send = signal.pidfd_send_signal
    _scan_fault = False
    def observed_send(fd, sig, *args):
        global _scan_fault
        if sig == signal.SIGKILL and ({retain_owner!r} and not Path({str(allow_kill)!r}).exists() or {first_kill_error!r} and not Path({str(failed_marker)!r}).exists()):
            Path({str(failed_marker)!r}).touch()
            if {scan_error!r}:
                _scan_fault = True
                raise ProcessLookupError(3, 'transient service KILL lookup failure')
            raise OSError(5, 'one-shot service KILL EIO' if {first_kill_error!r} else 'indeterminate signalling')
        result = _send(fd, sig, *args)
        if sig == signal.SIGTERM:
            Path({str(term_marker)!r}).touch()
        return result
    signal.pidfd_send_signal = observed_send
    _members = group_members
    def observed_members(pid):
        global _scan_fault
        if _scan_fault:
            _scan_fault = False
            Path({str(scan_marker)!r}).touch()
            raise OSError(5, 'transient group membership scan failure')
        return _members(pid)
    group_members = observed_members
    _stop = stop_group
    def observed_stop(pid, descriptor, **kwargs):
        Path({str(owner_marker)!r}).write_text(str(os.getpid()))
        return _stop(pid, descriptor, **kwargs)
    stop_group = observed_stop
    _unlink = Path.unlink
    def observed_unlink(path, *args, **kwargs):
        if path == Path({str(record)!r}) and path.exists():
            service_pid = json.loads(path.read_text())['pid']
            assert not group_members(service_pid), 'PID record removed before service group exit'
            Path({str(removed_marker)!r}).touch()
        return _unlink(path, *args, **kwargs)
    Path.unlink = observed_unlink
    os.killpg = lambda *_args: (_ for _ in ()).throw(AssertionError('numeric killpg forbidden'))
'''
            script.write_text(script.read_text().replace('\nif __name__ == "__main__":', injection + '\nif __name__ == "__main__":'))
            await asyncio.to_thread(fixture.f.finished, fixture.f.command('start', 'render'))
            leader, descendant = fixture.pin(fixture.ready), fixture.pin(fixture.child)
            original_record = record.read_bytes()
            foreign = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
            foreign_fd = os.pidfd_open(foreign.pid)
            self.addCleanup(os.close, foreign_fd)
            popen = subprocess.Popen
            def capture(*args, **kwargs):
                child = popen(*args, **kwargs)
                spawned.append(child)
                return child
            with load_controller() as m, patch.dict(os.environ, fixture.f.environment), \
                 patch.object(m.subprocess, 'Popen', side_effect=capture), \
                 patch.object(m.os, 'killpg', side_effect=AssertionError('numeric signalling forbidden')):
                m.SERVICE_CTL = str(fixture.f.panel / 'scripts/service_ctl.sh')
                runner = asyncio.create_task(m.restart_local_service('render'))
                await until(lambda: term_marker.exists() and exited(leader))
                self.assertFalse(exited(descendant))
                self.assertEqual(record.read_bytes(), original_record)
                owner_fd = os.pidfd_open(int(owner_marker.read_text()))
                runner.cancel()
                if repeated:
                    # Cancel again while shielded helper cleanup is active.
                    await asyncio.sleep(0)
                    runner.cancel()
                    runner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(runner, 8)
                self.assertIsNotNone(spawned[0].returncode)
                self.assertFalse(Path(f'/proc/{spawned[0].pid}').exists(), 'outer shell was not reaped')
                self.assertFalse(exited(foreign_fd))
                self.assertIsNone(foreign.poll())
                if retain_owner:
                    self.assertTrue(failed_marker.exists())
                    self.assertFalse(exited(owner_fd), 'sole pidfd owner was abandoned')
                    self.assertFalse(exited(descendant))
                    self.assertEqual(record.read_bytes(), original_record)
                    check = fixture.f.command('status', 'render')
                    out, err = await asyncio.to_thread(check.communicate, timeout=5)
                    self.assertNotEqual(check.returncode, 0, out + err)
                    allow_kill.touch()
                    await until(lambda: exited(descendant) and exited(owner_fd))
                else:
                    self.assertTrue(exited(descendant), 'service descendant survived cancellation cleanup')
                    self.assertTrue(exited(owner_fd), 'stop owner remained live after confirmed cleanup')
                self.assertEqual(identity.group_members(int(fixture.ready.read_text())), [])
                if first_kill_error:
                    self.assertTrue(failed_marker.exists())
                    if scan_error:
                        self.assertTrue(scan_marker.exists())
                elif not retain_owner:
                    self.assertTrue(removed_marker.exists())
                    self.assertFalse(record.exists())
                # Stale records after a recovered signalling warning may be
                # removed only now that the service group is definitely empty.
                status = fixture.f.command('status', 'render')
                await asyncio.to_thread(fixture.f.finished, status)
                await asyncio.to_thread(fixture.f.finished, fixture.f.command('stop', 'render'))
                self.assertFalse(record.exists())
                # Cancellation did not start a replacement: retain the existing
                # unavailable barrier until an explicit successful restart.
                self.assertEqual(m.service_maintenance, {'render': 'unavailable'})
                fixture.f.environment['H3_SERVICE_TERM_TIMEOUT'] = '1'
                fixture.ready.unlink(); fixture.child.unlink()
                with patch.dict(os.environ, fixture.f.environment):
                    await m.restart_local_service('render')
                    new_leader, new_child = fixture.pin(fixture.ready), fixture.pin(fixture.child)
                    self.assertEqual(m.service_maintenance, {})
                    await asyncio.to_thread(fixture.f.finished, fixture.f.command('stop', 'render'))
                    self.assertTrue(exited(new_leader) and exited(new_child))
                self.assertIsNone(foreign.poll())
        finally:
            if 'allow_kill' in locals():
                allow_kill.touch()
            if runner is not None and not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            for fd in fixture.fds:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL, None, identity.PIDFD_SIGNAL_PROCESS_GROUP)
                except ProcessLookupError:
                    pass
            if owner_fd is not None:
                await until(lambda: exited(owner_fd))
                os.close(owner_fd)
            if foreign is not None:
                if foreign.poll() is None:
                    foreign.kill()
                foreign.wait(timeout=5)
            fixture.doCleanups()

    async def test_cancel_real_restart_during_service_term_grace_kills_owned_descendant(self):
        await self.cancel_during_grace()

    async def test_repeated_cancellation_joins_cooperative_service_stop(self):
        await self.cancel_during_grace(repeated=True)

    async def test_first_service_group_kill_error_retries_same_pidfd(self):
        for scan_error in (False, True):
            with self.subTest(scan_error=scan_error):
                await self.cancel_during_grace(first_kill_error=True, scan_error=scan_error)

    async def test_indeterminate_cleanup_retains_isolated_owner_until_group_can_exit(self):
        await self.cancel_during_grace(retain_owner=True)

    async def test_genuine_unknown_identity_preserves_record_and_never_signals_foreign(self):
        fixture = step2.ServiceStep2Tests()
        fixture.setUp()
        foreign = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
        try:
            fixture.worker('descendant')
            await asyncio.to_thread(fixture.f.finished, fixture.f.command('start', 'render'))
            leader, child = fixture.pin(fixture.ready), fixture.pin(fixture.child)
            record = fixture.f.panel / 'state/pids/render.pid'
            original = record.read_bytes()
            signal.pidfd_send_signal(leader, signal.SIGTERM)
            await until(lambda: exited(leader))
            # Already-dead leader lacks a newly capturable ownership capability.
            # It must not launch an owner or signal the remaining process.
            for action in ('status', 'start', 'stop'):
                command = fixture.f.command(action, 'render')
                out, err = await asyncio.to_thread(command.communicate, timeout=5)
                self.assertNotEqual(command.returncode, 0, out + err)
                self.assertEqual(record.read_bytes(), original)
                self.assertFalse(exited(child))
                self.assertIsNone(foreign.poll())
        finally:
            for fd in fixture.fds:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL, None, identity.PIDFD_SIGNAL_PROCESS_GROUP)
                except ProcessLookupError:
                    pass
            if foreign.poll() is None:
                foreign.kill()
            foreign.wait(timeout=5)
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
