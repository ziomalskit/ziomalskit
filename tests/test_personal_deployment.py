"""Personal deployment contracts, entirely synthetic CPU/filesystem fixtures."""
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from h3.scripts import model_acceptance as models, model_downloads as downloads
from h3.scripts import gpu_acceptance_operator as operator, install_timing as timing
from h3.scripts.provision_models import disk_plan
from tests.helpers import load_controller
from tests.hf_sdk_fixture import sdk_fixture
from tests.test_artifact_storage import artifact, BODY
from tests import test_gpu_acceptance_operator as operator_fixture
NOW = operator_fixture.NOW


class PersonalOperatorTests(unittest.TestCase):
    def setUp(self):
        operator_fixture.OperatorTests.setUp(self)
        personal = json.loads(operator.PERSONAL_TEMPLATE.read_text())
        for key in ('checkout_sha', 'root', 'approvals', 'hardware', 'billing'):
            personal[key] = copy.deepcopy(self.session[key])
        personal['storage'].update(self.session['storage'])
        personal['storage'].update(mode='ephemeral', volume_id=None,
            models_root=str(self.base/'ComfyUI/models'))
        personal['quote'].update(self.session['quote'])
        personal['quote'].update(volume_usd_per_hour=0, retention_hours=0, other_fees_usd=.1,
                                 download_bytes_cap=300*10**9)
        personal['limits'].update(self.session['limits'])
        personal['limits'].update(max_session_seconds=14400, max_render_seconds=1200,
                                  max_cleanup_seconds=600, cleanup_reserve_usd=1)
        self.session = personal
        self.observed.update(container_root_device=42, pidfd_group_control=True)
        environment = patch.dict(os.environ, {'WORKSPACE': str(self.base), 'COMFY_ROOT': str(self.base/'ComfyUI')})
        environment.start(); self.addCleanup(environment.stop)

    def inspect(self):
        return operator.inspect(self.session, observer=lambda _: copy.deepcopy(self.observed), now=NOW)

    def test_ephemeral_same_root_filesystem_needs_no_volume_or_fake_retention(self):
        self.assertEqual(operator.validate_session(self.session, now=NOW), [])
        result = self.inspect()
        self.assertEqual(result['status'], 'PREFLIGHT_READY', result)
        self.assertEqual(result['known_model_bytes'], 278009016136)
        self.assertEqual(result['minimum_free_bytes'], 280009016136)
        self.assertFalse(result['submissions'])

    def test_production_kernel_and_actual_group_probe_stay_required(self):
        self.observed['kernel'] = '6.8.0-142-generic'
        self.assertIn('KERNEL_PYTHON_MISMATCH', self.inspect()['blocked'])
        self.observed['kernel'] = '6.9.0'
        self.observed['pidfd_group_control'] = False
        self.assertIn('PIDFD_GROUP_CONTROL_UNAVAILABLE', self.inspect()['blocked'])

    def test_ephemeral_cannot_claim_retained_volume_or_use_linked_models(self):
        self.session['storage']['volume_id'] = '123'
        with self.assertRaises(ValueError): operator.validate_session(self.session, now=NOW)
        self.session['storage']['volume_id'] = None
        (self.base/'ComfyUI').symlink_to(self.base/'elsewhere', target_is_directory=True)
        with self.assertRaises(ValueError): operator.validate_session(self.session, now=NOW)

    def test_incomplete_quote_does_not_issue_go_or_hide_the_full_19_artifacts(self):
        self.session['quote']['gpu_usd_per_hour'] = None
        result = operator.plan(self.session, now=NOW)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['known_model_bytes'], 278009016136)

    def test_disk_reserves_and_all_fees_count_toward_budget(self):
        self.session['limits']['max_total_usd'] = 15
        self.assertIn('BUDGET_EXCEEDS_LIMIT', self.inspect()['blocked'])
        self.session['quote'].update(ingress_usd_per_gb=0, external_preparation_usd=0)
        result = self.inspect()
        self.assertEqual(result['cost_ceiling_usd'], '6.0')
        self.observed['free_bytes'] = 279009016136
        self.assertIn('INSUFFICIENT_DISK', self.inspect()['blocked'])

    def test_all_phases_must_fit_the_session_deadline(self):
        self.session['limits']['max_render_seconds'] = 14000
        with self.assertRaises(ValueError): operator.validate_session(self.session, now=NOW)


class PersonalFilesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name); self.root = self.base/'ComfyUI/models'
        self.evidence = self.base/'acceptance'; self.item = artifact()
        self.environment = {'WORKSPACE': str(self.base), 'COMFY_ROOT': str(self.root.parent),
                            'H3_MODEL_ACCEPTANCE_MODELS_ROOT': str(self.root)}
        _, self.approval = models.load_selection()

    def test_acceptance_and_production_use_one_actual_file_and_fresh_hashes(self):
        models.require_store(self.evidence, self.approval, create=True, environment=self.environment)
        with sdk_fixture(self.item, BODY) as sdk:
            models.download_models(self.evidence, [self.item], self.approval,
                                   environment=self.environment)
            path = self.root/self.item['destination']; inode = path.stat().st_ino
            self.assertFalse((self.evidence/'models').exists())
            sdk.hub.get_hf_file_metadata.side_effect = AssertionError('must reuse')
            self.assertTrue(downloads.ensure_download(self.root, self.item)['reused'])
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(models.plan(self.evidence, [self.item], self.approval,
                                          self.environment)['missing_download_bytes'], 0)
            path.write_bytes(b'x' + BODY[1:])
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                downloads.ensure_download(self.root, self.item)

    def test_http_failure_retains_bound_prefix_then_resumes_same_inode(self):
        with sdk_fixture(self.item, BODY) as sdk:
            def interrupted(url, stream, **kwargs):
                stream.write(BODY[:7]); raise OSError('CPU interrupted transfer')
            sdk.transport.http_get.side_effect = interrupted
            with self.assertRaises(ValueError): downloads.ensure_download(self.root, self.item)
            partial = self.root/'.downloads'/self.item['id']/'.payload.incomplete'
            inode = partial.stat().st_ino
            self.assertEqual(downloads.partial_bytes(self.root, self.item), 7)
            def resume(url, stream, **kwargs):
                self.assertEqual(kwargs['resume_size'], 7); stream.write(BODY[7:])
            sdk.transport.http_get.side_effect = resume
            downloads.ensure_download(self.root, self.item)
            self.assertFalse(partial.exists())
            self.assertEqual((self.root/self.item['destination']).stat().st_ino, inode)
            self.assertEqual(sdk.transport.http_get.call_args.kwargs['headers'], {})

    def test_changed_upstream_revision_or_size_fails_before_transfer(self):
        for key, value in (('commit_hash', '2'*40), ('size', len(BODY)+1)):
            with self.subTest(key=key), sdk_fixture(self.item, BODY) as sdk:
                setattr(sdk.metadata, key, value)
                with self.assertRaises(ValueError): downloads.ensure_download(self.root, self.item)
                sdk.transport.http_get.assert_not_called()
                self.assertFalse((self.root/self.item['destination']).exists())

    def test_partial_rebound_to_different_source_and_unsafe_payload_fail_closed(self):
        with sdk_fixture(self.item, BODY) as sdk:
            def stop(url, stream, **kwargs): stream.write(BODY[:3]); raise OSError()
            sdk.transport.http_get.side_effect = stop
            with self.assertRaises(ValueError): downloads.ensure_download(self.root, self.item)
            changed = {**self.item, 'revision': '2'*40}
            with self.assertRaisesRegex(ValueError, 'source'):
                downloads.ensure_download(self.root, changed)
            partial = self.root/'.downloads'/self.item['id']/'.payload.incomplete'
            partial.unlink(); partial.symlink_to(self.base/'outside')
            with self.assertRaises(ValueError): downloads.ensure_download(self.root, self.item)
            self.assertFalse((self.base/'outside').exists())

    def test_complete_retained_payload_is_published_offline_without_second_file(self):
        with sdk_fixture(self.item, BODY) as sdk:
            original = downloads.install_verified
            with patch.object(downloads, 'install_verified', side_effect=OSError('CPU crash before rename')):
                with self.assertRaises(OSError): downloads.ensure_download(self.root, self.item)
            sdk.hub.get_hf_file_metadata.side_effect = AssertionError('offline resume')
            downloads.ensure_download(self.root, self.item)
            self.assertEqual((self.root/self.item['destination']).read_bytes(), BODY)

    def test_xet_ordered_range_resumes_prefix_without_http_or_chunk_cache(self):
        from contextlib import nullcontext
        item = self.item
        with sdk_fixture(item, BODY) as sdk:
            sdk.metadata.xet_file_data = SimpleNamespace(refresh_route='https://huggingface.co/fixture/xet-read-token/revision', file_hash='f'*64)
            group = Mock()
            ranges = []
            def chunks(file_info, *, start, end):
                ranges.append((start, end))
                if start == 0:
                    yield BODY[:7]
                    raise OSError('CPU interrupted Xet stream')
                yield BODY[start:end]
            group.download_stream.side_effect = chunks
            session = Mock()
            session.new_download_stream_group.side_effect = lambda **kw: nullcontext(group)
            xet = types.ModuleType('hf_xet'); xet.XetFileInfo = Mock()
            helpers = types.ModuleType('huggingface_hub.utils._xet')
            helpers.get_xet_session = lambda: session
            helpers.refresh_xet_connection_info = Mock(return_value=SimpleNamespace(
                endpoint='https://cas.fixture', access_token='SHORT_LIVED_CPU_TOKEN', expiration_unix_epoch=100))
            helpers.xet_headers_without_auth = lambda h: {k: v for k, v in h.items() if k != 'authorization'}
            with patch.dict(sys.modules, {'hf_xet': xet, 'huggingface_hub.utils._xet': helpers}):
                with self.assertRaises(ValueError): downloads.ensure_download(self.root, item)
                self.assertEqual(downloads.partial_bytes(self.root, item), 7)
                downloads.ensure_download(self.root, item)
            self.assertEqual(ranges, [(0, len(BODY)), (7, len(BODY))])
            sdk.transport.http_get.assert_not_called()
            self.assertEqual(session.new_download_stream_group.call_args.kwargs['custom_headers'], {})
            self.assertEqual(os.environ['HF_XET_CHUNK_CACHE_SIZE_BYTES'], '0')
            self.assertEqual((self.root/item['destination']).read_bytes(), BODY)

    def test_larger_than_50gb_uses_xet_range_and_keeps_only_written_prefix(self):
        item = {**self.item, 'size_bytes': 66280487368}
        with sdk_fixture(item, BODY) as sdk:
            sdk.metadata.size = item['size_bytes']
            sdk.metadata.xet_file_data = SimpleNamespace()
            def interrupted(data, stream, size, token):
                self.assertEqual(size, 66280487368)
                stream.write(BODY[:7]); raise OSError('synthetic interruption, no large allocation')
            with patch.object(downloads, 'receive_xet', side_effect=interrupted), patch.object(downloads.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**12)):
                with self.assertRaises(ValueError): downloads.ensure_download(self.root, item)
            sdk.transport.http_get.assert_not_called()
            self.assertEqual(downloads.partial_bytes(self.root, item), 7)

    def test_cross_filesystem_publication_has_no_copy_fallback(self):
        from h3.app.file_publication import publish_noreplace
        with patch('h3.app.file_publication.os.fstat', side_effect=[SimpleNamespace(st_dev=1), SimpleNamespace(st_dev=2)]):
            with self.assertRaises(OSError): publish_noreplace(10, 'partial', 'final', 11)

    def test_personal_model_peak_excludes_duplicate_staging_but_counts_retained_bytes(self):
        manifest = {'schema_version': 2, 'status': 'production_pinned_gpu_pending', 'artifacts': [self.item]}
        self.assertEqual(disk_plan(manifest, self.root)['minimum_free_bytes'], len(BODY))
        with sdk_fixture(self.item, BODY) as sdk:
            def stop(url, stream, **kwargs): stream.write(BODY[:7]); raise OSError()
            sdk.transport.http_get.side_effect = stop
            with self.assertRaises(ValueError): downloads.ensure_download(self.root, self.item)
        plan = disk_plan(manifest, self.root)
        self.assertEqual(plan['minimum_free_bytes'], len(BODY)-7)
        self.assertEqual(plan['retained_partial_bytes'], 7)
        self.assertEqual(plan['download_staging_bytes'], 0)


class InstallationTimingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/'stages.jsonl'
        environment = patch.dict(os.environ, {'H3_INSTALL_TIMING_FILE': str(self.path), 'HF_TOKEN': 'CPU_SECRET_SENTINEL'})
        environment.start(); self.addCleanup(environment.stop)

    def test_monotonic_success_and_failure_show_last_completed_without_secrets(self):
        with patch('builtins.print'):
            with timing.Stage('download', artifact='fixture-model', size_bytes=123, clock=iter([10, 12]).__next__): pass
            with self.assertRaises(OSError):
                with timing.Stage('sha256', clock=iter([12, 15]).__next__): raise OSError('CPU_SECRET_SENTINEL')
        result = timing.summary(self.path, clock=lambda: 20)
        self.assertEqual(result['last_completed_stage'], 'download')
        self.assertEqual(result['failed_or_interrupted'][0]['elapsed_seconds'], 3)
        self.assertEqual(result['total_elapsed_seconds'], 5)
        self.assertNotIn('CPU_SECRET_SENTINEL', self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_interrupted_stage_and_untrusted_end_token_are_safe(self):
        timing.Stage('llama_build', clock=lambda: 10).__enter__()
        result = timing.summary(self.path, clock=lambda: 18)
        self.assertEqual(result['failed_or_interrupted'][0]['status'], 'interrupted')
        self.assertEqual(result['total_elapsed_seconds'], 8)
        token = {'id': 'a'*32, 'stage': 'download', 'status': 'started', 'start_monotonic': 10, 'secret': 'CPU_SECRET_SENTINEL'}
        with patch('builtins.print'):
            self.assertEqual(timing.main(['end', json.dumps(token), 'completed']), 1)
        self.assertNotIn('CPU_SECRET_SENTINEL', self.path.read_text())

    def test_symlink_or_public_report_is_refused(self):
        outside = self.path.parent/'outside'; outside.write_text('preserve')
        self.path.symlink_to(outside)
        with self.assertRaises(OSError): timing.Stage('download').__enter__()
        self.assertEqual(outside.read_text(), 'preserve')
        self.path.unlink(); self.path.touch(); self.path.chmod(0o644)
        with self.assertRaises(ValueError): timing.Stage('download').__enter__()


class EphemeralLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_keep_data_destroy_remains_unavailable_without_real_volume(self):
        with load_controller() as m:
            m.H3_PERSISTENT_ROOT = str(m.ROOT); m.H3_PERSISTENCE_MODE = 'ephemeral'
            self.assertFalse(m.persistent_storage_status()['safe_for_destroy_keep_data'])
            with patch.object(m, 'run_vast_cli', AsyncMock()) as provider, patch.dict(os.environ, {'H3_ALLOW_SUBMISSIONS': '1'}):
                with self.assertRaises(m.HTTPException) as raised:
                    await m.api_vast_action(m.VastActionRequest(action='destroy_after_queue_keep_data'))
                self.assertEqual(raised.exception.status_code, 409)
                provider.assert_not_awaited()
