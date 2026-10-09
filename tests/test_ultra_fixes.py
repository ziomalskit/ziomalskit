"""Adversarial CPU proofs for the nine independent Ultra findings.

Real filesystem/child processes; pinned PromptQueue source excerpt; no models,
ComfyUI inference, provider control, network downloads, or GPU actions.
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import httpx
from fastapi import HTTPException
from tests.helpers import load_controller
from tests.fixtures.pinned_comfy_prompt_queue import PromptQueue
from tests.test_artifact_storage import artifact, BODY
from tests.test_temporal_contract import CANONICAL, FINAL
from h3.scripts.model_downloads import ensure_download

ROOT = Path(__file__).resolve().parents[1]


def owned_module(filename):
    # Pure modules load without importing CUDA/ComfyUI package __init__.
    package = types.ModuleType('_ultra_owned')
    package.__path__ = [str(ROOT / 'h3/custom_nodes/aj_production')]
    sys.modules[package.__name__] = package
    name = package.__name__ + '.' + filename
    spec = importlib.util.spec_from_file_location(name, Path(package.__path__[0]) / (filename + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journal_module = owned_module('execution_journal')
compiler = owned_module('compiler')


def queue_fixture(directory):
    queue = PromptQueue(types.SimpleNamespace(queue_updated=lambda: None))
    return queue, journal_module.ExecutionJournal(queue, directory)


def completion(content=FINAL, **detail):
    return {'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}],
            '__verbose': {'stop': True, 'stop_type': 'eos', 'truncated': False, **detail}}


class JournalTests(unittest.TestCase):
    def test_original_bound_completion_before_hook_is_durable_at_restart_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PromptQueue(types.SimpleNamespace(queue_updated=lambda: None))
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []));_, index = queue.get()
            cached_done = queue.task_done
            journal = journal_module.ExecutionJournal(queue, directory)
            cached_done(index, {'outputs': {'video': {'filename': 'cached-paid.mp4'}}}, queue.ExecutionStatus('success', True, []))
            self.assertEqual(journal.read(identity), {})
            self.assertTrue(journal.quiesce({identity})['ready'])
            _, restarted = queue_fixture(directory)
            self.assertEqual(restarted.read(identity)['outputs']['video']['filename'], 'cached-paid.mp4')

    def test_retained_journal_ancestor_symlink_creates_nothing_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);outside = root / 'outside';outside.mkdir()
            (root / 'link').symlink_to(outside, target_is_directory=True)
            queue = PromptQueue(types.SimpleNamespace(queue_updated=lambda: None))
            with self.assertRaises(OSError):journal_module.ExecutionJournal(queue, root / 'link/new-journal')
            self.assertEqual(list(outside.iterdir()), [])

    def test_pinned_prompt_queue_fixture_preserves_authoritative_excerpt_identity(self):
        import hashlib
        path = ROOT / 'tests/fixtures/pinned_comfy_prompt_queue.py'
        receipt = json.loads(path.with_suffix('.PROVENANCE.json').read_text())
        self.assertEqual(receipt['revision'], '6b747c0428c343e1417219641db93a4fb7cb69ae')
        self.assertEqual(receipt['fixture_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(receipt['source_sha256'], 'c9ef8ea11b8cb7aa80d05f670ca457211869d27623991b66d0014fb9b33fb246')

    def test_pending_deletion_receipt_survives_worker_restart_without_native_history(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, journal = queue_fixture(directory)
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []))
            receipt = journal.cancel(identity)
            self.assertEqual(receipt, {'protocol': 'aj-terminal-v1', 'prompt_id': identity, 'state': 'pending_deleted'})
            self.assertEqual(queue.get_current_queue(), ([], []))
            self.assertEqual(queue.get_history(identity), {})
            _, restarted = queue_fixture(directory)
            self.assertEqual(restarted.read(identity)['aj_cancel'], receipt)

    def test_running_cancellation_cannot_forge_pending_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, journal = queue_fixture(directory)
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []));queue.get()
            self.assertEqual(journal.cancel(identity)['state'], 'running_signalled')
            self.assertEqual(journal.read(identity), {})
            self.assertFalse(journal.quiesce({identity})['ready'])
            self.assertTrue(queue.currently_running)

    def test_failed_result_fsync_retains_running_ownership_and_refuses_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, journal = queue_fixture(directory)
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []));_, index = queue.get()
            with patch.object(journal_module.os, 'fsync', side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):
                    queue.task_done(index, {'outputs': {}}, queue.ExecutionStatus('success', True, []))
            self.assertTrue(queue.currently_running)
            self.assertEqual(queue.history, {})
            self.assertFalse(journal.quiesce({identity})['ready'])

    def test_quiesce_fences_new_admission_and_refuses_unowned_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, journal = queue_fixture(directory)
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []));queue.get()
            with self.assertRaisesRegex(ValueError, 'unowned'):
                journal.quiesce(set())
            self.assertFalse(journal.quiesced)
            self.assertFalse(journal.quiesce({identity})['ready'])
            with self.assertRaisesRegex(RuntimeError, 'quiesced'):
                queue.put((1, str(uuid.uuid4()), {}, {}, []))
            self.assertIsNone(queue.get(timeout=.001))

    def test_pending_tombstone_write_failure_cannot_delete_work(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, journal = queue_fixture(directory)
            identity = str(uuid.uuid4());queue.put((0, identity, {}, {}, []))
            with patch.object(journal, 'write', side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):journal.cancel(identity)
            self.assertEqual(queue.queue[0][1], identity)


class StagingTests(unittest.TestCase):
    def test_nested_retained_symlink_never_reaches_sdk_or_changes_outside_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'store';outside = Path(directory) / 'outside';outside.mkdir()
            sentinel = outside / 'model.gguf';sentinel.write_bytes(b'OUTSIDE SENTINEL')
            item = artifact();item['repository_path'] = 'nested/model.gguf'
            stage = root / '.downloads' / item['id'];stage.mkdir(parents=True)
            (stage / 'nested').symlink_to(outside, target_is_directory=True)
            def unsafe_sdk(item, staging):
                (staging / item['repository_path']).write_bytes(BODY)
                return staging / item['repository_path']
            sdk = Mock(side_effect=unsafe_sdk)
            with self.assertRaises(ValueError):ensure_download(root, item, downloader=sdk)
            sdk.assert_not_called()
            self.assertEqual(sentinel.read_bytes(), b'OUTSIDE SENTINEL')

    def test_fifo_and_hardlink_staging_are_rejected_before_writer(self):
        for kind in ('fifo', 'hardlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'store';item = artifact()
                stage = root / '.downloads' / item['id'];stage.mkdir(parents=True)
                entry = stage / 'nested'
                if kind == 'fifo':os.mkfifo(entry)
                else:
                    outside = Path(directory) / 'outside';outside.write_bytes(b'sentinel');os.link(outside, entry)
                sdk = Mock()
                with self.assertRaises(ValueError):ensure_download(root, item, downloader=sdk)
                sdk.assert_not_called()


class CompletionTests(unittest.TestCase):
    def test_six_headers_with_visibly_truncated_final_section_and_no_receipt_are_rejected(self):
        text = FINAL[:FINAL.index('non_diegetic_music:')] + 'non_diegetic_music: The music swells as the'
        body = completion(text);body.pop('__verbose')
        with self.assertRaises(ValueError):compiler.completion_response(body)

    def test_length_context_stream_runtime_and_truncation_finish_paths_are_rejected(self):
        for mode in ('length', 'context', 'stream_error', 'runtime_error', 'truncated', 'stop_word', 'missing_finish'):
            with self.subTest(mode=mode):
                body = completion(FINAL + ' The unfinished')
                if mode == 'length':body['choices'][0]['finish_reason'] = 'length'
                if mode == 'context':body['__verbose']['stop_type'] = 'limit'
                if mode == 'stream_error':body['error'] = {'message': 'stream failed'}
                if mode == 'runtime_error':body = {'error': {'message': 'runtime failed'}}
                if mode == 'truncated':body['__verbose']['truncated'] = True
                if mode == 'stop_word':body['__verbose']['stop_type'] = 'word'
                if mode == 'missing_finish':body['choices'][0].pop('finish_reason')
                with self.assertRaises(ValueError):compiler.completion_response(body)

    def test_natural_eos_keeps_final_and_reasoning_channels_separate(self):
        body = completion();body['choices'][0]['message']['reasoning_content'] = 'private reasoning'
        self.assertEqual(compiler.completion_response(body), (FINAL, 'private reasoning', ''))

    def test_completion_runtime_requires_pinned_build_receipt_and_exact_binary_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'llama-server'
            binary.write_text('#!/bin/sh\necho "b10472 60eeeb6 CPU fixture"\n');binary.chmod(0o700)
            receipt = {'commit': compiler.REVISION, 'tag': 'b10472', 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'cuda': '13.0', 'arch': '120'}
            binary.with_suffix('.build.json').write_text(json.dumps(receipt))
            with patch.dict(os.environ, {'H3_CUDA_VERSION': '13.0'}):
                self.assertEqual(compiler.verified_server(binary.with_name('llama-cli')), binary)
                binary.write_text('#!/bin/sh\necho "tampered"\n')
                with self.assertRaises(ValueError):compiler.verified_server(binary.with_name('llama-cli'))


    def test_real_local_json_runtime_is_reaped_and_truncated_transport_rejected(self):
        # A tiny actual CPU child implements only the local pinned protocol
        # shape; it never loads models. Test process/HTTP ownership, not inference.
        script = '''import http.server,json,sys
payload=json.loads(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{}')
 def do_POST(self):
  self.rfile.read(int(self.headers['Content-Length']))
  data=json.dumps(payload).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
http.server.HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
'''
        real_spawn = subprocess.Popen
        for body, succeeds in ((completion(), True), (completion(truncated=True), False)):
            children = []
            def spawn(command, **kwargs):
                process = real_spawn([sys.executable, '-u', '-c', script, json.dumps(body), *command[1:]], **kwargs)
                children.append(process);return process
            memory = types.ModuleType('comfy.model_management');memory.processing_interrupted = lambda: False
            comfy = types.ModuleType('comfy');comfy.model_management = memory
            with patch.dict(sys.modules, {'comfy': comfy, 'comfy.model_management': memory}), \
                 patch.object(compiler, 'verified_server', return_value=Path('/CPUFixture/llama-server')), \
                 patch.object(compiler.subprocess, 'Popen', side_effect=spawn):
                if succeeds:
                    self.assertEqual(compiler.run_completion(['/fixture/llama-cli', '--single-turn', '-f', '/unused'],
                        [{'role': 'user', 'content': 'fixture'}], {'max_tokens': 12352, 'temperature': .8, 'top_p': .95,
                        'top_k': 64, 'repeat_penalty': 1.05, 'timeout_seconds': 5})[0], FINAL)
                else:
                    with self.assertRaises(ValueError):compiler.run_completion(['/fixture/llama-cli'], [],
                        {'max_tokens': 12352, 'temperature': .8, 'top_p': .95, 'top_k': 64, 'repeat_penalty': 1.05, 'timeout_seconds': 5})
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)
            self.assertFalse(Path('/proc/' + str(children[0].pid)).exists())


class ControllerUltraTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller(production_probes=True)
        self.m = self.fixture.__enter__();self.addCleanup(self.fixture.__exit__, None, None, None)

    def job(self, identity, cancel=False):
        job = {'id': uuid.uuid4().hex, 'status': 'recovery_render', 'active_service': 'render', 'render_prompt_id': identity,
               'render_submission_state': 'accepted', 'render_submission_attempted': True, 'cancel_requested': cancel,
               'context': {'prompt': 'fixture', 'soft_timeout_minutes': 1, 'hard_restart_after_seconds': 30}}
        self.m.queue.append(job);return job

    async def test_watchdog_running_snapshot_then_success_then_restart_retains_paid_output_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = str(uuid.uuid4());queue, journal = queue_fixture(directory)
            queue.put((0, identity, {}, {}, []));_, index = queue.get()
            job = self.job(identity)
            job.update(render_watchdog_prompt_id=identity, render_watchdog_last_progress_at=time.time(),
                       render_timeout_prompt_id=identity, render_hard_timeout_deadline=time.time() - 1,
                       render_timeout_cancel_state='uncertain')
            self.m.save_state()
            worker = {'queue': queue, 'journal': journal};events = []
            def handler(request):
                if request.url.path.startswith('/history/'):
                    return httpx.Response(200, json=worker['queue'].get_history(identity))
                if request.url.path.startswith('/aj/results/'):
                    return httpx.Response(200, json=worker['journal'].read(identity))
                if request.url.path == '/queue':
                    running, pending = queue.get_current_queue();events.append('snapshot_running')
                    queue.task_done(index, {'outputs': {'save': {'videos': [{'filename': 'paid.mp4', 'subfolder': 'H3', 'type': 'output'}]}}},
                                    queue.ExecutionStatus('success', True, []))
                    events.append('paid_success_after_snapshot')
                    return httpx.Response(200, json={'queue_running': running, 'queue_pending': pending})
                if request.url.path == '/aj/quiesce':
                    body = json.loads(request.content);result = worker['journal'].quiesce(set(body['owned_ids']))
                    self.assertTrue(result['ready']);events.append('durable_barrier');return httpx.Response(200, json=result)
                raise AssertionError('unexpected request')
            async def restart(*args, **kwargs):
                events.append('restart');worker['queue'], worker['journal'] = queue_fixture(directory)
                self.assertEqual(worker['queue'].history, {});return 0, b'', b''
            real_client = httpx.AsyncClient;real_spawn = asyncio.create_subprocess_exec;children = []
            async def watcher(*args, **kwargs):
                process = await real_spawn(sys.executable, '-c', 'import time;time.sleep(30)',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                children.append(process);return process
            with patch.object(self.m.httpx, 'AsyncClient', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)), \
                 patch.object(self.m, '_run_owned_restart', side_effect=restart) as restart_call, \
                 patch.object(self.m.asyncio, 'create_subprocess_exec', side_effect=watcher), \
                 patch.object(self.m, '_dispatch_prepared_workflow', AsyncMock()) as dispatch:
                result = await self.m.watch_prompt(job, identity, 'render')
                await self.m._finish_watched_job(job, 'render', result)
            self.assertEqual(events, ['snapshot_running', 'paid_success_after_snapshot', 'durable_barrier', 'restart'])
            self.assertEqual(job['status'], 'completed')
            self.assertIn('paid.mp4', job['video_outputs'][0]);self.assertEqual(job['render_prompt_id'], identity)
            durable = json.loads(self.m.QUEUE_FILE.read_text())['queue'][0]
            self.assertEqual(durable['video_outputs'], job['video_outputs'])
            restart_call.assert_awaited_once();dispatch.assert_not_awaited()
            self.assertTrue(all(process.returncode is not None for process in children))

    async def test_confirmed_pending_receipt_finishes_cancelled_without_history_or_remote_calls(self):
        identity = str(uuid.uuid4());job = self.job(identity, cancel=True)
        self.m._record_cancel_confirmation(job, 'render', identity,
            {'protocol': 'aj-terminal-v1', 'prompt_id': identity, 'state': 'pending_deleted'})
        self.m.queue.clear();self.m.load_state();job = self.m.queue[0]
        with patch.object(self.m, 'fetch_history', AsyncMock(side_effect=AssertionError('not needed'))) as history:
            await self.m._recover_one_job(job, 'render')
        self.assertEqual(job['status'], 'cancelled');history.assert_not_awaited()
        self.assertEqual(job['render_prompt_id'], identity)

    async def test_lost_pending_cancel_reply_recovers_from_durable_worker_tombstone(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = str(uuid.uuid4());queue, journal = queue_fixture(directory)
            queue.put((0, identity, {}, {}, []));job = self.job(identity, cancel=True)
            real_client = httpx.AsyncClient
            def handler(request):
                if request.url.path.startswith('/aj/cancel/'):
                    journal.cancel(identity)
                    raise httpx.ReadTimeout('reply lost')
                if request.url.path.startswith('/history/'):return httpx.Response(200, json={})
                if request.url.path.startswith('/aj/results/'):return httpx.Response(200, json=journal.read(identity))
                raise AssertionError('unexpected request')
            with patch.object(self.m.httpx, 'AsyncClient', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)), \
                 patch.object(self.m, 'wait_service_ready', AsyncMock(return_value=True)), \
                 patch.object(self.m, '_dispatch_prepared_workflow', AsyncMock()) as dispatch:
                with self.assertRaises(httpx.ReadTimeout):await self.m.cancel_prompt('render', identity)
                await self.m._recover_one_job(job, 'render')
            self.assertEqual(job['status'], 'cancelled');dispatch.assert_not_awaited()


    async def test_running_cancel_receipt_remains_uncertain_and_never_resubmits(self):
        identity = str(uuid.uuid4());job = self.job(identity, cancel=True)
        self.m._record_cancel_confirmation(job, 'render', identity,
            {'protocol': 'aj-terminal-v1', 'prompt_id': identity, 'state': 'running_signalled'})
        with patch.object(self.m, 'wait_service_ready', AsyncMock(return_value=True)), \
             patch.object(self.m, 'fetch_history', AsyncMock(return_value={})), \
             patch.object(self.m, 'fetch_service_queue', AsyncMock(return_value={'queue_running': [], 'queue_pending': []})), \
             patch.object(self.m, '_dispatch_prepared_workflow', AsyncMock()) as dispatch:
            await self.m._recover_one_job(job, 'render')
        self.assertEqual(job['status'], 'recovery_render');self.assertTrue(self.m.service_recovering('render'))
        dispatch.assert_not_awaited();self.assertEqual(job['render_prompt_id'], identity)

    async def test_restart_without_durable_barrier_never_launches_control_command(self):
        async def unavailable(request):return httpx.Response(404)
        real_client = httpx.AsyncClient
        with patch.object(self.m.httpx, 'AsyncClient', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(unavailable), **kwargs)), \
             patch.object(self.m, '_run_owned_restart', AsyncMock()) as command:
            with self.assertRaises(httpx.HTTPStatusError):await self.m.restart_comfy('render')
        command.assert_not_awaited()

    async def test_bad_profile_is_quarantined_without_hiding_completed_mp4_or_uuid(self):
        identity = str(uuid.uuid4());old = self.job(identity)
        old.update(profile='h3_full', context={'profile': 'h3_full'})
        completed = {'id': 'paid-completed', 'status': 'completed', 'context': {'prompt': 'fixture'},
                     'video_outputs': ['/api/proxy/render/view?filename=paid.mp4&type=output']}
        self.m.queue.append(completed);self.m.save_state();self.m.queue.clear();self.m.load_state()
        catalogue = await self.m.jobs();by_id = {job['id']: job for job in catalogue['jobs']}
        self.assertEqual(by_id['paid-completed']['status'], 'completed')
        self.assertIn('paid.mp4', by_id['paid-completed']['video_outputs'][0])
        self.assertEqual(by_id[old['id']]['status'], 'reconciliation_required')
        self.assertEqual(by_id[old['id']]['render_prompt_id'], identity)
        self.assertNotIn('profile_identity', by_id[old['id']]['context'])
        self.assertTrue(self.m.lifecycle_dispatch_blocked());self.assertTrue(self.m.service_recovering('render'))
        self.assertIsNone(self.m.state_load_error)

    async def test_terminal_runtime_opt_in_is_contained_before_any_child_launch(self):
        with load_controller(terminal_opt_in=True) as controller, \
             patch.object(controller, 'PTYSession', side_effect=AssertionError('must never launch')) as spawn:
            self.assertFalse(controller.H3_ENABLE_TERMINAL)
            with self.assertRaises(HTTPException) as error:await controller.api_terminal_session()
            self.assertEqual(error.exception.status_code, 403);spawn.assert_not_called()
            self.assertFalse((await controller.config())['terminal_enabled'])

    async def test_provider_secret_uses_scoped_environment_and_failure_never_leaks_to_state_diagnostics_or_logs(self):
        secret = 'PROVIDER_SENTINEL_ULTRA_ABC_123'
        # Actual argv-equivalent child launch, no provider executable or network.
        original = self.m._run_owned_restart;captured = []
        async def child(command, **kwargs):
            captured.extend(command)
            self.assertNotIn(secret, repr(command));self.assertNotIn('--api-key', command)
            self.assertEqual(kwargs['environment']['VAST_API_KEY'], secret)
            return await original([sys.executable, '-c', "import os,sys;sys.stderr.write(os.environ['VAST_API_KEY']);sys.exit(7)"], **kwargs)
        with patch.dict(os.environ, {'CONTAINER_API_KEY': '  ' + secret + '  '}), patch.object(self.m, '_run_owned_restart', side_effect=child):
            with self.assertRaises(RuntimeError) as error:await self.m.run_vast_cli('show', 'instance', '0')
            self.assertNotIn(secret, str(error.exception))
            self.m._lifecycle_failure('echoed argv and stderr: ' + secret)
            self.m.vast_control['extra'] = secret;self.m.save_vast_control()
            self.assertNotIn(secret, self.m.VAST_CONTROL_FILE.read_text())
            self.assertNotIn(secret, json.dumps(self.m.state_diagnostics()))
            self.m.WORKSPACE.mkdir();(self.m.WORKSPACE / 'h3-mobile.log').write_text('echo: ' + secret)
            self.assertNotIn(secret, json.dumps(await self.m.api_logs('panel', 20)))
            diagnostic_module = sys.modules[self.m.__package__ + '.diagnostics'] if self.m.__package__ + '.diagnostics' in sys.modules else __import__(self.m.__package__ + '.diagnostics', fromlist=['*'])
            with patch.object(diagnostic_module, 'service_health', AsyncMock(return_value={'status': 'FAIL', 'detail': secret})), \
                 patch.object(diagnostic_module, 'local_probe', AsyncMock(return_value={'status': 'FAIL', 'error': secret})), \
                 patch.object(diagnostic_module, 'checks_for', return_value=[{'status': 'FAIL', 'detail': secret}]):
                self.assertNotIn(secret, json.dumps(await self.m.api_diagnostics()))
        self.assertNotIn(secret, repr(captured))
