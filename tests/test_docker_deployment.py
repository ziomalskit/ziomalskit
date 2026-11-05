"""Bounded offline Docker/bootstrap transactions; never CUDA, HTTP or weights."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from h3.docker import instance as docker
from h3.scripts import install_llama, runtime_config

ROOT = Path(__file__).resolve().parents[1]


class DockerDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aj-docker-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.seed = self.base / 'seed'
        self.seed.mkdir()
        (self.seed / 'main.py').write_text('# inert immutable source')
        (self.seed / 'models').mkdir()
        for key, value in [('ROOT', self.root), ('SEED', self.seed), ('IMAGE', self.base / 'image.json'), ('BOOT', self.base / 'boot-id')]:
            patcher = patch.object(docker, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        docker.IMAGE.write_text(json.dumps({'revision': 'a' * 40}))
        docker.BOOT.write_text('boot-1')

    def bootstrap(self):
        docker.bootstrap()

    def test_bootstrap_with_mounted_workspace_links_code_and_preserves_partial_store(self):
        self.bootstrap()
        model = self.root / 'ComfyUI/models/.downloads/partial'
        model.parent.mkdir(); model.write_bytes(b'prefix')
        inode = model.stat().st_ino
        self.bootstrap()
        self.assertEqual(model.read_bytes(), b'prefix')
        self.assertEqual(model.stat().st_ino, inode)
        self.assertTrue((self.root / 'ComfyUI/main.py').is_symlink())
        self.assertFalse((self.root / 'ComfyUI/models').is_symlink())
        self.assertEqual(list((self.seed / 'models').iterdir()), [])
        self.assertFalse((self.root / 'H3_VAST_MOBILE/runtime.env').exists())
        docker.IMAGE.write_text(json.dumps({'revision': 'b' * 40}))
        with self.assertRaisesRegex(ValueError, 'another image'):
            self.bootstrap()
        self.assertEqual(model.read_bytes(), b'prefix')

    def test_bootstrap_preserves_foreign_files_and_refuses_symlink_store(self):
        comfy = self.root / 'ComfyUI'; comfy.mkdir(parents=True)
        foreign = comfy / 'main.py'; foreign.write_text('private user source')
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            self.bootstrap()
        self.assertEqual(foreign.read_text(), 'private user source')
        foreign.unlink()
        (comfy / 'models').symlink_to(self.seed / 'models')
        with self.assertRaisesRegex(ValueError, 'real instance directories'):
            self.bootstrap()

    def test_service_gate_rejects_missing_stale_boot_and_changed_models(self):
        self.bootstrap()
        with patch.object(docker, 'model_signatures', return_value={'fixture': {'ino': 7}}):
            with self.assertRaises(FileNotFoundError):
                docker.service_check()
            docker.ready_path().write_text(json.dumps({'identity': docker.identity(), 'models': docker.model_signatures()}))
            docker.service_check()
            docker.BOOT.write_text('boot-2')
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                docker.service_check()
            docker.BOOT.write_text('boot-1')
            with patch.object(docker, 'model_signatures', return_value={'fixture': {'ino': 8}}):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    docker.service_check()

    def test_health_failure_never_prompts_for_token_or_downloads(self):
        with patch.object(docker, 'bootstrap'), patch.object(docker, 'health', side_effect=ValueError('host failed')), \
             patch.object(docker, 'write_environment') as config, patch.object(docker.getpass, 'getpass') as prompt, \
             patch.object(docker, 'run') as command:
            for action in ('init', 'models'):
                with self.assertRaisesRegex(ValueError, 'host failed'):
                    docker.execute(action)
            config.assert_not_called(); prompt.assert_not_called(); command.assert_not_called()

    def test_init_is_model_free_and_start_cannot_skip_models(self):
        with patch.object(docker, 'bootstrap'), patch.object(docker, 'health') as health, \
             patch.object(docker, 'write_environment'), patch.object(docker, 'run') as command, \
             patch.object(docker.getpass, 'getpass') as prompt:
            docker.execute('init')
            health.assert_called_once(); command.assert_not_called(); prompt.assert_not_called()
            with self.assertRaises(FileNotFoundError):
                docker.execute('start')
            command.assert_not_called()

    def test_download_credentials_scoped_to_child_and_failure_leaves_no_ready_receipt(self):
        self.bootstrap()
        secret = 'FAKE_CPU_ONLY_TOKEN_NOT_A_CREDENTIAL'
        calls = []
        def child(args, **kwargs):
            calls.append((args, kwargs))
            raise subprocess.CalledProcessError(2, args)
        with patch.object(docker, 'health'), patch.object(docker, 'write_environment'), \
             patch.object(docker.getpass, 'getpass', return_value=secret), \
             patch.object(docker.subprocess, 'run', side_effect=child), contextlib.redirect_stdout(io.StringIO()) as logs:
            with self.assertRaises(subprocess.CalledProcessError):
                docker.execute('models')
        self.assertFalse(docker.ready_path().exists())
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertNotIn(secret, ' '.join(args))
        self.assertEqual(kwargs['env']['HF_TOKEN'], secret)
        self.assertNotIn('HF_TOKEN', docker.environment())
        self.assertNotIn(secret, logs.getvalue())
        self.assertFalse(any(secret.encode() in p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()))

    def test_worker_environment_drops_provider_tokens_and_forces_safe_paths(self):
        with patch.dict(os.environ, {'HF_TOKEN': 'fake', 'VAST_API_KEY': 'fake', 'AWS_SECRET_ACCESS_KEY': 'fake',
                                    'H3_ALLOW_SUBMISSIONS': '1', 'COMFY_ROOT': '/wrong', 'H3_PANEL_PASSWORD': 'fake'}):
            env = docker.environment()
        for key in ('HF_TOKEN', 'VAST_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'H3_PANEL_PASSWORD'):
            self.assertNotIn(key, env)
        self.assertEqual(env['H3_ALLOW_SUBMISSIONS'], '0')
        self.assertEqual(env['COMFY_ROOT'], str(self.root / 'ComfyUI'))
        self.assertEqual(env['VAST_CLI'], '/bin/false')
        self.assertIn('/usr/local/cuda/bin', env['PATH'].split(':'))
        self.assertEqual(env['CUDA_HOME'], '/usr/local/cuda')
        with patch.object(docker, 'bootstrap'), patch.object(docker, 'service_check'),              patch.object(docker, 'write_environment'), patch.object(docker, 'run') as command:
            docker.execute('start')
        actions = [list(map(str, call.args[0])) for call in command.call_args_list]
        self.assertEqual(actions[0][-2:], ['start', 'all'])
        self.assertEqual(actions[1][-2:], ['restart', 'panel'])
        self.assertTrue(actions[2][-1].endswith('/preflight.sh'))
        self.assertTrue(actions[3][-1].endswith('/smoke_test.sh'))

    def test_service_script_image_gate_precedes_any_worker_launch(self):
        marker = self.base / 'image-marker'; marker.touch()
        script = (ROOT / 'h3/scripts/service_ctl.sh').read_text().replace('/etc/aj-image.json', str(marker))
        script = script.replace('/opt/aj/venv/bin/python /opt/aj/ziomalskit/h3/docker/instance.py service-check',
                                "echo 'inert gate rejected'; exit 19")
        path = self.base / 'service_ctl.sh'; path.write_text(script)
        for action in ('start', 'restart'):
            result = subprocess.run(['bash', str(path), action, 'all'], env={'PATH': os.environ['PATH']}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertEqual(result.stdout.strip(), 'inert gate rejected')

    def test_cgroup_current_and_all_visible_ancestors_limit_ram_cpu(self):
        root = self.base / 'cgroup'; child = root / 'allocation/job'; child.mkdir(parents=True)
        (root / 'cgroup.controllers').touch()
        proc = self.base / 'proc'; (proc / 'self').mkdir(parents=True)
        (proc / 'self/cgroup').write_text('0::/allocation/job\n')
        (proc / 'self/mountinfo').write_text(f'1 2 0:4 / {root} rw - cgroup2 cgroup rw\n')
        (proc / 'meminfo').write_text('MemAvailable: 200000000 kB\n')
        for directory, ram, quota in ((root, 'max', 'max 100000'), (root / 'allocation', str(100 * 1024**3), '600000 100000'), (child, str(120 * 1024**3), '800000 100000')):
            for name, text in {'memory.max': ram, 'memory.high': 'max', 'memory.current': str(10 * 1024**3), 'cpu.max': quota}.items():
                (directory / name).write_text(text)
        with patch.object(docker.os, 'sched_getaffinity', return_value=set(range(16))):
            ram, cpu, rows = docker.cgroup_limits(root, proc)
        self.assertEqual(ram, 90 * 1024**3); self.assertEqual(cpu, 6); self.assertEqual(len(rows), 3)
        (root / 'cgroup.controllers').unlink()
        with self.assertRaisesRegex(ValueError, 'v2'):
            docker.cgroup_limits(root, proc)

    def test_early_resource_gpu_and_driver_failures_prevent_kernel(self):
        self.bootstrap()
        disk = {'minimum_free_bytes': 100, 'free_bytes': docker.RESERVE + 101}
        mount = json.dumps({'filesystems': [{'options': 'rw', 'target': str(self.root)}]})
        good_gpu = 'GPU-fixture, NVIDIA RTX PRO 6000 Blackwell, 580.65.06, 97887, 12.0'
        for ram, cpu, free, gpu in [(1, 8, docker.RESERVE + 101, good_gpu),
                (100 * 1024**3, 7, docker.RESERVE + 101, good_gpu),
                (100 * 1024**3, 8, 100, good_gpu),
                (100 * 1024**3, 8, docker.RESERVE + 101, good_gpu.replace('580.65.06', '570.1')),
                (100 * 1024**3, 8, docker.RESERVE + 101, good_gpu.replace('12.0', '9.0'))]:
            with self.subTest(ram=ram, cpu=cpu, free=free, gpu=gpu), \
                 patch.object(docker, 'cgroup_limits', return_value=(ram, cpu, [])), \
                 patch.object(docker, 'disk_plan', return_value=dict(disk, free_bytes=free)), \
                 patch.object(docker, 'output', side_effect=[mount, gpu]), \
                 patch.object(docker.sage, 'install') as kernel, patch.object(docker, 'run') as command, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    docker.health()
                kernel.assert_not_called(); command.assert_not_called()

    def test_native_image_publication_checksums_without_executing_driver_linked_binary(self):
        source = self.base / 'llama-cli'; source.write_bytes(b'inert native binary')
        vendor = self.base / 'vendor/llama-cli'; vendor.parent.mkdir()
        signature = self.base / 'signature.json'
        with patch.object(install_llama.subprocess, 'check_output', side_effect=AssertionError('CPU image must not execute CUDA binary')):
            install_llama.install(source, signature, vendor, 'b10472', runtime_config.LLAMA_COMMIT, '13.0', build_only=True)
        receipt = json.loads(signature.read_text())
        self.assertEqual(receipt['runtime_execution'], 'PENDING')
        self.assertEqual(receipt['sha256'], hashlib.sha256(vendor.read_bytes()).hexdigest())
        self.assertEqual(vendor.read_bytes(), source.read_bytes())

    def test_cpu_plan_has_no_mutations_and_honest_gpu_status(self):
        with patch.object(docker, 'bootstrap') as bootstrap, patch.object(docker, 'health') as health, \
             patch.object(docker, 'run') as command, contextlib.redirect_stdout(io.StringIO()) as logs:
            docker.execute('plan')
        bootstrap.assert_not_called(); health.assert_not_called(); command.assert_not_called()
        value = json.loads(logs.getvalue())
        self.assertEqual(value['gpu_smoke'], 'PENDING')
        self.assertEqual(value['disk']['known_stack_bytes'], 278009016136)
        self.assertFalse(self.root.exists())

    def test_preserved_production_files_match_exact_requested_base(self):
        checksums = json.loads((ROOT / 'tests/fixtures/docker-preserved-sha256.json').read_text())
        for name, expected in checksums.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)
        canonical = 'h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json'
        self.assertEqual(checksums[canonical], '276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222')

    def test_pinned_build_bootstrap_and_allowlisted_context(self):
        dockerfile = (ROOT / 'h3/docker/Dockerfile').read_text()
        self.assertIn('@sha256:435220c0fef35cbf712e11999f8670a83835ef3cdd18564e5e8122f83078c88c', dockerfile)
        self.assertNotIn('EXPOSE ', '\n'.join(s for s in dockerfile.splitlines() if not s.startswith('#')))
        builder = (ROOT / 'h3/docker/build.sh').read_text()
        self.assertIn('git archive HEAD h3', builder)
        self.assertNotIn('docker push', builder)
        image = (ROOT / 'h3/docker/build-image.sh').read_text()
        self.assertLess(image.index('apt-get install'), image.index('scripts/provision.sh'))
        self.assertIn('python3.12-venv', image)
        self.assertIn('--image-build', image)
        self.assertIn('H3_INSTALL_VAST_CLI=0', image)
        for name in ('build.sh', 'build-image.sh', 'entrypoint.sh', 'aj-docker'):
            subprocess.run(['bash', '-n', str(ROOT / 'h3/docker' / name)], check=True)
        ignore = (ROOT / 'h3/docker/Dockerfile.dockerignore').read_text()
        self.assertTrue(ignore.startswith('**\n!h3/\n'))
        for excluded in ('runtime.env', '.env', '*.gguf', '*.safetensors'):
            self.assertIn(excluded, ignore)
