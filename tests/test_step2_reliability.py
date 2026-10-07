"""STEP 2 regressions: local processes, TCP, tmpfs, copies and real Git only."""
from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from h3.scripts import deployment, install_llama, llama_runtime, runtime_config
from h3.scripts import process_identity as identity
from tests.helpers import load_controller
from tests import test_service_ctl, test_fallback_nodes, test_deployment

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Process barrier timed out')
        time.sleep(.01)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return bool(poll.poll(0))


class StorageAndVastTests(unittest.IsolatedAsyncioTestCase):
    async def test_02_confirmed_cli_success_survives_secondary_cleanup_error(self):
        with load_controller() as m, tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'cli'
            test_deployment.executable(cli, '#!/bin/sh\nprintf "confirmed success\\n"\n')
            m.VAST_CLI = str(cli)
            send = signal.pidfd_send_signal
            def fail_term(fd, sig, *args):
                if sig == signal.SIGTERM:
                    raise OSError(errno.EIO, 'secondary cleanup TERM')
                return send(fd, sig, *args)
            with patch.object(signal, 'pidfd_send_signal', side_effect=fail_term):
                self.assertEqual(await m.run_vast_cli('stop', 'instance', 'local-test'), 'confirmed success')
            self.assertIn('Confirmed CLI success', m.vast_control['last_cli_cleanup_warning'])

    async def test_01_real_tmpfs_cannot_dispatch_destroy_even_with_matching_proof(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='aj-step2-') as tmp, load_controller() as m:
            root = Path(tmp)
            m.H3_PERSISTENT_ROOT, m.H3_PERSISTENCE_MODE = str(root), 'volume'
            for attr, name in [('STATE', 'state'), ('COMFY_MODELS_DIR', 'models'),
                               ('COMFY_OUTPUT_DIR', 'outputs'), ('COMFY_INPUT_DIR', 'inputs')]:
                path = root / name
                path.mkdir()
                setattr(m, attr, path)
            mount = m._mount_info(root)
            self.assertEqual(mount['fstype'], 'tmpfs')
            proof = root / 'proof.json'
            proof.write_text(json.dumps({'provider': 'vast-local-volume', 'volume_id': 1,
                'retained_on_instance_destroy': True, 'mount': mount, 'instance_id': '123',
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'root_identity': [root.stat().st_dev, root.stat().st_ino]}))
            with patch.dict(os.environ, {'CONTAINER_ID': '123', 'H3_PERSISTENT_VOLUME_PROOF': str(proof)}), \
                 patch.object(m, 'run_vast_cli', AsyncMock()) as cli:
                self.assertFalse(m.persistent_storage_status()['safe_for_destroy_keep_data'])
                await m.delayed_instance_action('destroy', delay=0, require_persistence=True)
                cli.assert_not_awaited()
                self.assertEqual(m.vast_control['plan'], 'blocked_unsafe_persistence')

    async def test_01_valid_attachment_and_mount_required_and_stale_proof_is_rejected(self):
        with load_controller() as m:
            root = m.ROOT / 'volume'
            root.mkdir()
            m.H3_PERSISTENT_ROOT, m.H3_PERSISTENCE_MODE = str(root), 'volume'
            for attr, name in [('STATE', 'state'), ('COMFY_MODELS_DIR', 'models'),
                               ('COMFY_OUTPUT_DIR', 'outputs'), ('COMFY_INPUT_DIR', 'inputs')]:
                path = root / name
                path.mkdir()
                setattr(m, attr, path)
            mount = {'source': '/dev/volume', 'target': str(root), 'fstype': 'ext4',
                     'fsroot': '/retained-volume', 'uuid': 'volume-uuid', 'maj:min': '8:1'}
            proof = {'provider': 'vast-local-volume', 'volume_id': 123, 'instance_id': '456',
                     'retained_on_instance_destroy': True, 'mount': mount,
                     'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     'root_identity': [root.stat().st_dev, root.stat().st_ino]}
            path = root / 'proof'
            path.write_text(json.dumps(proof))
            def mounted(p):
                return {'source': 'overlay', 'target': '/', 'fstype': 'overlay', 'fsroot': '/',
                        'maj:min': '0:1'} if p == Path('/') else mount
            with patch.dict(os.environ, {'CONTAINER_ID': '456', 'H3_PERSISTENT_VOLUME_PROOF': str(path)}), \
                 patch.object(m, '_mount_info', side_effect=mounted):
                self.assertTrue(m.persistent_storage_status()['safe_for_destroy_keep_data'])
                for key, value in [('instance_id', 'other'), ('boot_id', 'old-boot'),
                                   ('root_identity', [0, 1]), ('volume_id', 0)]:
                    path.write_text(json.dumps(dict(proof, **{key: value})))
                    self.assertFalse(m.persistent_storage_status()['safe_for_destroy_keep_data'])
                path.write_text('[]')
                self.assertFalse(m.persistent_storage_status()['safe_for_destroy_keep_data'])

    async def test_01_ext4_without_attachment_proof_fails_closed(self):
        with load_controller() as m:
            m.H3_PERSISTENT_ROOT, m.H3_PERSISTENCE_MODE = str(m.ROOT), 'volume'
            mount = {'source': '/dev/ephemeral', 'target': str(m.ROOT), 'fstype': 'ext4',
                     'fsroot': '/', 'uuid': 'disk', 'maj:min': '8:1'}
            with patch.object(m, '_mount_info', return_value=mount), \
                 patch.dict(os.environ, {'H3_PERSISTENT_VOLUME_PROOF': '/absent'}):
                self.assertFalse(m.persistent_storage_status()['safe_for_destroy_keep_data'])

    async def test_02_shutdown_reaps_cli_group_before_late_effect_and_repeated_cancel(self):
        with load_controller() as m, tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready, child_marker, release, effect = [root / name for name in ('ready', 'child', 'release', 'effect')]
            cli = root / 'cli'
            test_deployment.executable(cli, f'''#!{sys.executable}
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
def publish(path):
 tmp=path.with_name(path.name+'.'+str(os.getpid()));tmp.write_text(str(os.getpid()));os.replace(tmp,path)
child=os.fork()
publish(Path({str(child_marker)!r} if child==0 else {str(ready)!r}))
while not Path({str(release)!r}).exists():time.sleep(.01)
Path({str(effect)!r}).touch()
''')
            m.VAST_CLI = str(cli)
            for name in ('prompt_worker_task', 'render_worker_task', 'vast_guard_task', 'recovery_task'):
                setattr(m, name, asyncio.create_task(asyncio.sleep(60)))
            m.vast_control.update(plan='executing_stop', armed_generation=m.queue_persistence_epoch)
            with patch.dict(os.environ, {'CONTAINER_ID': 'local-test', 'CONTAINER_API_KEY': ''}):
                m.lifecycle_action_task = asyncio.create_task(m.delayed_instance_action('stop', delay=0))
                fds = []
                try:
                    async with asyncio.timeout(8):
                        while not (ready.exists() and child_marker.exists()):
                            await asyncio.sleep(.01)
                    for path in (ready, child_marker):
                        fds.append(os.pidfd_open(int(path.read_text())))
                    shutdown = asyncio.create_task(m.shutdown())
                    await asyncio.sleep(.05)
                    m.lifecycle_action_task.cancel()
                    m.lifecycle_action_task.cancel()
                    await asyncio.wait_for(shutdown, 8)
                    self.assertTrue(all(exited(fd) for fd in fds))
                    release.touch()
                    await asyncio.sleep(.1)
                    self.assertFalse(effect.exists())
                    self.assertEqual(m.vast_control['plan'], 'action_interrupted')
                finally:
                    for fd in fds:
                        try:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.close(fd)
                    await m.shutdown()


class ServiceStep2Tests(unittest.TestCase):
    def setUp(self):
        self.f = test_service_ctl.ServiceControlTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.environment['RENDER_PORT'] = str(port())
        self.f.environment['H3_READINESS_TIMEOUT'] = '3'
        self.f.environment['H3_SERVICE_TERM_TIMEOUT'] = '1'
        self.ready = self.f.base / 'ready'
        self.child = self.f.base / 'descendant'
        self.accepted = self.f.base / 'accepted'
        self.release = self.f.base / 'release'
        self.fds = []
        self.addCleanup(self.clean_owned)

    def clean_owned(self):
        for fd in self.fds:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(fd)

    def worker(self, mode='http'):
        python = Path(self.f.environment['PYTHON_BIN'])
        prefix = python.parent.parent
        test_deployment.executable(python, f'''#!{sys.executable}
import os,signal,socket,sys,time
from pathlib import Path
a=sys.argv[1:]
def publish(path):
 tmp=path.with_name(path.name+'.'+str(os.getpid()));tmp.write_text(str(os.getpid()));os.replace(tmp,path)
if a and a[0].endswith('/process_identity.py'):os.execv({sys.executable!r},[{sys.executable!r},*a])
if a and a[0]=='-c':print({str(prefix)!r});sys.exit(0)
mode={mode!r}
if mode=='descendant':
 child=os.fork()
 if child:
  publish(Path({str(self.ready)!r}))
  while True:time.sleep(.1)
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 publish(Path({str(self.child)!r}))
if mode=='delayed':
 publish(Path({str(self.ready)!r}))
 while not Path({str(self.release)!r}).exists():time.sleep(.01)
server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
server.bind(('127.0.0.1',int(a[a.index('--port')+1])));server.listen()
if mode!='descendant':publish(Path({str(self.ready)!r}))
while True:
 conn,_=server.accept();Path({str(self.accepted)!r}).touch()
 if mode=='hang':
  while True:time.sleep(.1)
 conn.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\nConnection: close\\r\\n\\r\\n{{}}');conn.close()
''')

    def pin(self, marker):
        wait_for(marker.exists)
        fd = os.pidfd_open(int(marker.read_text()))
        self.fds.append(fd)
        return fd

    def test_03_changed_port_and_root_restart_stops_original_then_owns_new(self):
        self.worker()
        self.f.finished(self.f.command('start', 'render'))
        old = self.pin(self.ready)
        self.ready.unlink()
        new_root = self.f.base / 'new-comfy'
        new_root.mkdir()
        self.f.environment.update(RENDER_PORT=str(port()), COMFY_ROOT=str(new_root))
        conflict = self.f.command('start', 'render')
        out, err = conflict.communicate(timeout=8)
        self.assertNotEqual(conflict.returncode, 0, out + err)
        self.assertFalse(exited(old))
        self.f.finished(self.f.command('restart', 'render'))
        new = self.pin(self.ready)
        self.assertTrue(exited(old))
        self.assertFalse(exited(new))
        record = json.loads((self.f.panel / 'state/pids/render.pid').read_text())
        self.assertEqual(record['pid'], int(self.ready.read_text()))
        self.assertEqual(record['configuration']['port'], int(self.f.environment['RENDER_PORT']))
        self.f.finished(self.f.command('stop', 'render'))

    def test_03_step1_record_remains_owned_after_configuration_change(self):
        self.worker()
        self.f.finished(self.f.command('start', 'render'))
        old = self.pin(self.ready)
        record_path = self.f.panel / 'state/pids/render.pid'
        record = json.loads(record_path.read_text())
        record.pop('identity')
        record.pop('configuration')
        record_path.write_text(json.dumps(record))
        self.f.environment['RENDER_PORT'] = str(port())
        self.f.finished(self.f.command('stop', 'render'))
        self.assertTrue(exited(old))
        self.assertFalse(record_path.exists())

    def test_03_interpreter_symlink_update_does_not_lose_committed_ownership(self):
        self.worker()
        self.f.finished(self.f.command('start', 'render'))
        self.pin(self.ready)
        record_path = self.f.panel / 'state/pids/render.pid'
        from types import SimpleNamespace
        args = SimpleNamespace(pid_file=record_path, service='render', python='/new/python',
            comfy_root='/new/comfy', panel_root='/new/panel', port=1)
        original = Path.resolve
        interpreter = Path(self.f.environment['COMFY_PYTHON'])
        def changed(path, *a, **kw):
            if path == interpreter:
                return Path('/different/interpreter')
            return original(path, *a, **kw)
        with patch.object(Path, 'resolve', changed):
            pid, fd = identity.checked_pidfd(args)
            os.close(fd)
        self.assertEqual(pid, int(self.ready.read_text()))
        self.f.finished(self.f.command('stop', 'render'))

    def test_03_panel_root_change_imports_step1_record_into_stable_default_store(self):
        self.worker()
        self.f.finished(self.f.command('start', 'render'))
        old = self.pin(self.ready)
        old_record = self.f.panel / 'state/pids/render.pid'
        record = json.loads(old_record.read_text())
        record.pop('identity')
        record.pop('configuration')
        old_record.write_text(json.dumps(record))
        self.ready.unlink()
        new_panel, new_comfy = self.f.base / 'new-panel', self.f.base / 'new-comfy'
        shutil.copytree(self.f.panel / 'scripts', new_panel / 'scripts')
        new_comfy.mkdir()
        self.f.environment.pop('PID_DIR')
        self.f.environment.update(PANEL_ROOT=str(new_panel), COMFY_ROOT=str(new_comfy), RENDER_PORT=str(port()))
        self.f.finished(self.f.command('restart', 'render'))
        new = self.pin(self.ready)
        self.assertTrue(exited(old))
        self.assertFalse(exited(new))
        stable = self.f.base / '.h3-service-pids/render.pid'
        self.assertEqual(json.loads(stable.read_text())['pid'], int(self.ready.read_text()))
        self.f.finished(self.f.command('stop', 'render'))
        self.assertFalse(stable.exists())
        self.f.finished(self.f.command('status', 'render'))
        self.assertFalse(stable.exists(), 'old STEP 1 record was imported again after stop')

    def test_04_hanging_http_has_bounded_failure_and_releases_lock(self):
        self.worker('hang')
        start = self.f.command('start', 'render')
        self.pin(self.ready)
        wait_for(self.accepted.exists)
        out, err = start.communicate(timeout=8)
        self.assertNotEqual(start.returncode, 0, out + err)
        self.f.assert_lock_released()
        self.f.finished(self.f.command('status', 'render'))
        self.f.finished(self.f.command('stop', 'render'))

    def test_04_killed_shell_leaves_no_curl_or_helper_holding_flock(self):
        self.worker('hang')
        start = self.f.command('start', 'render')
        self.pin(self.ready)
        wait_for(self.accepted.exists)
        start.kill()
        start.communicate(timeout=5)
        self.f.assert_lock_released()
        self.f.finished(self.f.command('stop', 'render'))

    def delayed_helper(self, operation):
        helper = self.f.panel / 'scripts/process_identity.py'
        barrier = self.f.base / 'helper-barrier'
        helper.write_text(f'''import os,sys,time
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from h3.scripts import process_identity as m
real=m.{operation}
def pause(*a,**kw):
 result=real(*a,**kw) if {operation!r}=='stop_group' else None
 p=Path({str(barrier)!r});tmp=p.with_suffix('.tmp');tmp.write_text(str(os.getpid()));os.replace(tmp,p)
 while not Path({str(self.release)!r}).exists():time.sleep(.01)
 return result if {operation!r}=='stop_group' else real(*a,**kw)
m.{operation}=pause
raise SystemExit(m.main())
''')
        return barrier

    def test_04_parent_death_before_commit_cannot_publish_or_start_a_late_worker(self):
        self.worker()
        barrier = self.delayed_helper('record_process')
        start = self.f.command('start', 'render')
        helper = self.pin(barrier)
        start.kill()
        start.wait(timeout=5)
        wait_for(lambda: exited(helper))
        self.assertFalse(self.ready.exists(), 'worker executed before durable COMMIT')
        self.f.assert_lock_released()
        self.release.touch()
        self.f.finished(self.f.command('start', 'render'))
        self.pin(self.ready)
        self.f.finished(self.f.command('stop', 'render'))

    def test_04_orphaned_stop_helper_cannot_delete_a_later_launch_record(self):
        self.worker()
        self.f.finished(self.f.command('start', 'render'))
        self.pin(self.ready)
        self.ready.unlink()
        barrier = self.delayed_helper('stop_group')
        stop = self.f.command('stop', 'render')
        helper = self.pin(barrier)
        try:
            stop.kill()
            stop.wait(timeout=5)
            self.f.assert_lock_released()
            pending = self.f.command('start', 'render')
            out, err = pending.communicate(timeout=8)
            self.assertNotEqual(pending.returncode, 0, out + err)
            self.assertFalse(self.ready.exists(), 'new launch raced an unfinished stop mutation')
            self.release.touch()
            wait_for(lambda: exited(helper))
            self.f.finished(self.f.command('start', 'render'))
            new = self.pin(self.ready)
            record = self.f.panel / 'state/pids/render.pid'
            self.assertEqual(json.loads(record.read_text())['pid'], int(self.ready.read_text()))
            self.f.finished(self.f.command('stop', 'render'))
            self.assertTrue(exited(new))
        finally:
            self.release.touch()

    def test_05_stop_pins_group_through_leader_exit_and_kills_descendant(self):
        self.worker('descendant')
        foreign = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        self.addCleanup(lambda: (foreign.kill(), foreign.wait()) if foreign.poll() is None else None)
        self.f.finished(self.f.command('start', 'render'))
        leader, descendant = self.pin(self.ready), self.pin(self.child)
        self.f.finished(self.f.command('stop', 'render'))
        self.assertTrue(exited(leader))
        self.assertTrue(exited(descendant))
        self.assertFalse((self.f.panel / 'state/pids/render.pid').exists())
        self.assertIsNone(foreign.poll())

    def test_05_dead_leader_with_live_descendant_is_unknown_never_absent(self):
        self.worker('descendant')
        self.f.finished(self.f.command('start', 'render'))
        leader, descendant = self.pin(self.ready), self.pin(self.child)
        signal.pidfd_send_signal(leader, signal.SIGTERM)
        wait_for(lambda: exited(leader))
        record = self.f.panel / 'state/pids/render.pid'
        original = record.read_bytes()
        try:
            for action in ('status', 'start', 'stop'):
                proc = self.f.command(action, 'render')
                out, err = proc.communicate(timeout=8)
                self.assertNotEqual(proc.returncode, 0, out + err)
                self.assertEqual(record.read_bytes(), original)
                self.assertFalse(exited(descendant))
        finally:
            signal.pidfd_send_signal(leader, signal.SIGKILL, None, identity.PIDFD_SIGNAL_PROCESS_GROUP)
            wait_for(lambda: exited(descendant))

    def test_05_proc_scan_cannot_prematurely_confirm_group_exit(self):
        self.worker('descendant')
        self.f.finished(self.f.command('start', 'render'))
        leader, descendant = self.pin(self.ready), self.pin(self.child)
        original = identity.group_members
        calls = 0
        def missed_fork(pid):
            nonlocal calls
            calls += 1
            return [] if calls == 1 else original(pid)
        with patch.object(identity, 'group_members', side_effect=missed_fork):
            identity.stop_group(int(self.ready.read_text()), leader)
        self.assertTrue(exited(leader))
        self.assertTrue(exited(descendant))

    def test_06_foreign_listener_after_free_port_never_reports_started(self):
        self.worker('delayed')
        start = self.f.command('start', 'render')
        self.pin(self.ready)
        foreign = subprocess.Popen([sys.executable, '-c', '''from http.server import HTTPServer,BaseHTTPRequestHandler
import sys
class H(BaseHTTPRequestHandler):
 def do_GET(self):self.send_response(200);self.end_headers()
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
''', self.f.environment['RENDER_PORT']])
        self.addCleanup(lambda: (foreign.kill(), foreign.wait()) if foreign.poll() is None else None)
        out, err = start.communicate(timeout=8)
        self.assertNotEqual(start.returncode, 0, out + err)
        self.assertNotIn('render started', out)
        self.assertIsNone(foreign.poll())
        self.f.finished(self.f.command('stop', 'render'))


class InstallStep2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aj-step2-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_07_actual_python310_is_rejected_before_launch(self):
        available = Path('/tmp/aj-step2-python310/cpython-3.10.21-linux-x86_64-gnu/bin/python')
        python = str(available) if available.is_file() else shutil.which('python3.10')
        if python is None:
            # Exercise the actual version expression without an installed 3.10.
            body = (ROOT / 'h3/scripts/python_env.sh').read_text()
            self.assertIn('sys.version_info >= (3, 11)', body)
            self.assertIn('Python >=3.11 required', body)
            return
        env = dict(os.environ, COMFY_PYTHON=python, PYTHON_BIN=python)
        result = subprocess.run(['bash', '-c', 'source "$1"; h3_select_python', 'gate',
            str(ROOT / 'h3/scripts/python_env.sh')], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python >=3.11 required', result.stderr)
        optimized = subprocess.run(['bash', '-c', 'source "$1"; h3_select_python', 'gate',
            str(ROOT / 'h3/scripts/python_env.sh')], env=dict(env, PYTHONOPTIMIZE='1'),
            capture_output=True, text=True)
        self.assertNotEqual(optimized.returncode, 0)
        self.assertIn('Python >=3.11 required', optimized.stderr)
        result = subprocess.run([python, '-B', str(ROOT / 'h3/scripts/process_identity.py'),
            'launch', '--pid-file', str(self.root / 'worker.pid'), '--service', 'render',
            '--python', python, '--comfy-root', str(self.root), '--panel-root', str(self.root),
            '--port', str(port()), '--log-file', str(self.root / 'worker.log'), '--', python,
            'main.py', '--port', '8188'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'worker.pid').exists())
        self.assertFalse((self.root / 'worker.log').exists())

    def deployment(self):
        source, active = self.root / 'source', self.root / 'active'
        shutil.copytree(ROOT / 'h3', source, ignore=shutil.ignore_patterns('state', '__pycache__', 'runtime.env'))
        shutil.copytree(source, active)
        (active / 'state').mkdir()
        (active / 'state/queue.json').write_text('original state')
        (active / 'runtime.env').write_text('private original credentials')
        return source, active

    def test_08_partial_copy_eio_preserves_active_code_state_and_environment(self):
        source, active = self.deployment()
        original = (active / 'app/main.py').read_bytes()
        real = shutil.copyfile
        def failing(src, dst, **kwargs):
            if os.fspath(src).endswith('/app/main.py'):
                Path(dst).write_bytes(b'partial')
                raise OSError(errno.EIO, 'partial stage copy')
            return real(src, dst, **kwargs)
        with patch.object(shutil, 'copyfile', side_effect=failing):
            with self.assertRaisesRegex(OSError, 'partial stage copy'):
                deployment.deploy(source, active)
        self.assertEqual((active / 'app/main.py').read_bytes(), original)
        self.assertEqual((active / 'state/queue.json').read_text(), 'original state')
        self.assertEqual((active / 'runtime.env').read_text(), 'private original credentials')
        deployment.validate(active)

    def test_08_atomic_swap_preserves_concurrent_state_writes_and_lock_inode(self):
        source, active = self.deployment()
        state_inode = (active / 'state').stat().st_ino
        (active / 'app/main.py').write_text('# old release')
        marker, release = self.root / 'ready', self.root / 'release'
        writer = subprocess.Popen([sys.executable, '-c', '''import os,sys,time
from pathlib import Path
state,ready,release=map(Path,sys.argv[1:])
ready.touch()
i=0
while not release.exists():
 tmp=state/'.writing';tmp.write_text(str(i));os.replace(tmp,state/'queue.json');i+=1
release.write_text(str(i))
''', str(active / 'state'), str(marker), str(release)])
        try:
            wait_for(marker.exists)
            deployment.deploy(source, active)
            self.assertEqual((active / 'state').stat().st_ino, state_inode)
            self.assertEqual((active / 'app/main.py').read_bytes(), (source / 'app/main.py').read_bytes())
            release.touch()
            self.assertEqual(writer.wait(timeout=5), 0)
            self.assertEqual(int((active / 'state/queue.json').read_text()) + 1, int(release.read_text()))
            self.assertEqual((active / 'runtime.env').read_text(), 'private original credentials')
            deployment.deploy(source, active)  # A second exchange preserves the same inode too.
            self.assertEqual((active / 'state').stat().st_ino, state_inode)
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait()

    def test_09_partial_binary_copy_preserves_old_executable_and_metadata(self):
        binary, vendor, signature = [self.root / name for name in ('new', 'llama-cli', 'signature')]
        test_deployment.executable(binary, '#!/bin/sh\nprintf "60eeeb6\\n"\n')
        test_deployment.executable(vendor, '#!/bin/sh\nprintf "old valid executable\\n"\n')
        old = vendor.read_bytes()
        vendor.with_suffix('.build.json').write_text('old metadata')
        def copy_failure(*args, **kwargs):
            Path(args[0][-1]).write_bytes(b'partial')
            raise OSError(errno.EIO, 'partial candidate copy')
        with patch.object(install_llama.subprocess, 'run', side_effect=copy_failure):
            with self.assertRaisesRegex(OSError, 'partial candidate copy'):
                install_llama.install(binary, signature, vendor, 'b10472', runtime_config.LLAMA_COMMIT, '13.0')
        self.assertEqual(vendor.read_bytes(), old)
        self.assertEqual(vendor.with_suffix('.build.json').read_text(), 'old metadata')
        self.assertEqual(subprocess.check_output([str(vendor)], text=True).strip(), 'old valid executable')

    def test_09_crash_between_binary_and_sidecar_keeps_verifiable_metadata(self):
        f = test_deployment.DeploymentTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        node, vendor = f.llama_node()
        source = self.root / 'new'
        source.write_bytes(vendor.read_bytes() + b'# new build\n')
        real = runtime_config.atomic_private_text
        def fail_sidecar(path, text):
            if path == vendor.with_suffix('.build.json'):
                raise OSError(errno.EIO, 'after binary commit')
            return real(path, text)
        with patch.object(install_llama, 'atomic_private_text', side_effect=fail_sidecar):
            with self.assertRaisesRegex(OSError, 'after binary commit'):
                install_llama.install(source, self.root / 'signature', vendor, 'b10472', runtime_config.LLAMA_COMMIT, '13.0')
        self.assertEqual(llama_runtime.verify_llama(node), vendor)

    def test_10_interrupted_fetch_reruns_and_legacy_no_checkout_recovers(self):
        f = test_fallback_nodes.FallbackNodeTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        tools = self.root / 'bin'
        marker = self.root / 'fetch-failed'
        test_deployment.executable(tools / 'git', f'''#!{sys.executable}
import os,sys
from pathlib import Path
if 'fetch' in sys.argv and not Path({str(marker)!r}).exists():
 Path({str(marker)!r}).touch();sys.exit(7)
os.execv('/usr/bin/git',['git',*sys.argv[1:]])
''')
        f.environment['PATH'] = str(tools) + ':' + f.environment['PATH']
        first = f.install()
        self.assertEqual(first.returncode, 7, first.stderr)
        self.assertFalse(f.node(f.specs[0]).exists())
        target = f.node(f.specs[0])
        subprocess.run(['/usr/bin/git', 'clone', '--no-checkout', f.specs[0]['repo'], str(target)], check=True, capture_output=True)
        self.assertTrue(f.git(target, 'status', '--porcelain').strip())
        second = f.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        for spec in f.specs:
            self.assertEqual(f.git(f.node(spec), 'rev-parse', 'HEAD').strip(), spec['revision'])
        (target / 'user-file').write_text('retain')
        third = f.install()
        self.assertNotEqual(third.returncode, 0)
        self.assertEqual((target / 'user-file').read_text(), 'retain')

    def test_11_two_real_dev_starters_publish_one_owned_panel_then_stop(self):
        repo = self.root / 'repo'
        (repo / 'scripts').mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/dev_panel.py', repo / 'scripts/dev_panel.py')
        shutil.copytree(ROOT / 'h3', repo / 'h3', ignore=shutil.ignore_patterns('state', 'runtime.env', '__pycache__'))
        command = [sys.executable, '-B', str(repo / 'scripts/dev_panel.py')]
        number = str(port())
        processes = [subprocess.Popen(command + ['start', '--port', number], text=True,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        fd = None
        try:
            outputs = []
            for proc in processes:
                out, err = proc.communicate(timeout=25)
                outputs.append(out)
                self.assertEqual(proc.returncode, 0, out + err)
            self.assertEqual(sum('panel started;' in output for output in outputs), 1)
            self.assertEqual(sum('already running and responsive' in output for output in outputs), 1)
            record = json.loads((repo / '.local/panel.pid').read_text())
            fd = os.pidfd_open(record['pid'])
            self.assertFalse(exited(fd))
            check = subprocess.run(command + ['check', '--port', number], text=True, capture_output=True, timeout=8)
            self.assertEqual(check.returncode, 0, check.stderr)
            # The panel did not inherit the control lock's inode.
            lock_inode = (repo / '.local/panel.lock').stat().st_ino
            for entry in Path(f'/proc/{record["pid"]}/fd').iterdir():
                try:
                    self.assertNotEqual(entry.stat().st_ino, lock_inode)
                except FileNotFoundError:
                    pass
            stop = subprocess.run(command + ['stop'], text=True, capture_output=True, timeout=10)
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertTrue(exited(fd))
            self.assertFalse((repo / '.local/panel.pid').exists())
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=5)
            if fd is not None:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
