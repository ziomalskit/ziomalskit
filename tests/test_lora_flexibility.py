"""Used-resource LoRA boundaries: real frozen jobs, tiny CPU artifacts only."""
import copy
import hashlib
import importlib
import json
import sys
import unittest
import uuid
from unittest.mock import patch

from tests import test_manual_prompt_controller as manual
from tests import test_manual_render_adapter as render
from tests import test_production_controller as production
from tests.helpers import load_controller
from h3.scripts.enroll_external_loras import enroll


class LoraFlexibilityTests(unittest.IsolatedAsyncioTestCase):
    setUp = production.ProductionControllerTests.setUp
    manual_request = manual.ManualPromptControllerTests.manual_request
    job = manual.ManualPromptControllerTests.job
    fixture_manifest = render.ManualRenderProvenanceTests.fixture_manifest
    graph = render.ManualRenderAdapterTests.graph
    cli = render.ManualRenderAdapterTests.cli
    transport = production.ProductionControllerTests.transport

    def cpu_invariants(self):
        from h3.scripts.preflight import cpu_invariants
        # The CLI imports `app` relative to its panel root. Keep those modules
        # and sys.path confined to this temporary controller generation.
        with patch.dict(sys.modules), patch.object(sys, 'path', list(sys.path)):
            for name in list(sys.modules):
                if name == 'app' or name.startswith('app.'):
                    del sys.modules[name]
            return cpu_invariants(self.m.ROOT, allow_pending=True)

    def edit_registry(self, edit):
        path = self.m.CONFIG / 'lora_registry.json'
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))

    def file(self, identifier):
        path = self.m.COMFY_MODELS_DIR / 'loras' / self.m.LORA_REGISTRY[identifier]['filename']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Synthetic CPU LoRA: ' + identifier.encode())
        return path

    def trust_hm_fixture(self):
        path = self.file('hm-breasts-v2')
        registry = self.m.registry_json(self.m.LORA_REGISTRY)
        registry['hm-breasts-v2'].update(size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.m.LORA_REGISTRY = self.m.immutable_registry(registry)

    async def test_h3_defaults_need_neither_external_file_nor_enrollment(self):
        self.fixture_manifest()
        self.trust_hm_fixture()
        job = await self.job()
        selected = {row['id']: row for row in job['context']['loras']}
        for identifier, strength in [('mysticxxx-v4', .6), ('movement-v1', .5)]:
            self.assertFalse(selected[identifier]['enabled'])
            self.assertEqual(selected[identifier]['strength'], strength)
            self.assertFalse((self.m.COMFY_MODELS_DIR / 'loras' / self.m.LORA_REGISTRY[identifier]['filename']).exists())
        self.assertFalse((self.m.COMFY_MODELS_DIR / '.external_loras.json').exists())
        await self.m.verify_production_files(job, 'render')
        graph = await self.graph(job)
        self.assertEqual(graph['6164']['inputs']['lora_1']['lora'], 'HMBreastsV2.safetensors')
        self.assertFalse(graph['6164']['inputs']['lora_2']['on'])

    async def test_added_unused_registry_entry_does_not_quarantine_saved_job(self):
        job = await self.job(profile='10eros_full')
        frozen = copy.deepcopy(job['context'])
        def add(data):
            row = copy.deepcopy(next(row for row in data['loras'] if row['id'] == 'movement-v1'))
            row.update(id='new-optional', filename='new_optional.safetensors')
            data['loras'].append(row)
        self.edit_registry(add)
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            recovered = restarted.find_job(job['id'])
            self.assertEqual(recovered['status'], 'render_queued_auto')
            self.assertEqual(recovered['context'], frozen)
            self.assertIn('new-optional', restarted.LORA_REGISTRY)
            restarted.patch_workflow(recovered, restarted.production_workflow(recovered),
                                     approved_prompt=restarted.final_prompt_for_render(recovered))

    async def test_explicit_external_enable_verifies_and_rejects_replaced_bytes(self):
        self.fixture_manifest()
        path = self.file('movement-v1')
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['movement-v1'])
        job = await self.job(profile='10eros_full', loras=[dict(id='movement-v1', enabled=True, strength=.4)])
        await self.m.verify_production_files(job, 'render')
        self.assertEqual((await self.graph(job))['6164']['inputs']['lora_1']['strength'], .4)
        path.write_bytes(path.read_bytes()[::-1])
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')

    async def test_active_missing_unenrolled_symlink_and_wrong_receipt_are_rejected(self):
        self.fixture_manifest()
        path = self.file('movement-v1')
        job = await self.job(profile='10eros_full', loras=[dict(id='movement-v1', enabled=True, strength=.5)])
        with self.assertRaises(self.m.SubmissionRejected):  # Present, unenrolled.
            await self.m.verify_production_files(job, 'render')
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['movement-v1'])
        body = path.read_bytes()
        path.unlink()
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')
        other = path.with_suffix('.bin'); other.write_bytes(body); path.symlink_to(other)
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')
        path.unlink(); path.write_bytes(body)
        record = self.m.COMFY_MODELS_DIR / '.external_loras.json'
        data = json.loads(record.read_text()); data['artifacts']['movement-v1']['sha256'] = '0' * 64
        record.write_text(json.dumps(data))
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')

    async def test_settings_between_jobs_need_no_restart_provision_or_full_model_hash(self):
        self.fixture_manifest()
        self.file('movement-v1')
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['movement-v1'])
        artifacts = importlib.import_module(self.m.__package__ + '.artifacts')
        previous = None
        for enabled, strength, second, seed in [(True, .3, False, 11), (True, -.2, True, 22), (False, .5, False, 33)]:
            with self.subTest(enabled=enabled, strength=strength, second=second):
                job = await self.job(profile='10eros_full', request_id=uuid.uuid4(), seed=seed,
                    duration_seconds=10, loras=[dict(id='movement-v1', enabled=enabled, strength=strength)],
                    render_settings=dict(second_pass_enabled=second, first_steps=28))
                frozen = copy.deepcopy(job['context'])
                with patch.object(artifacts, 'verify_file', wraps=artifacts.verify_file) as verify:
                    await self.m.verify_production_files(job, 'render')
                self.assertEqual([c.args[1] for c in verify.call_args_list],
                    ['loras/' + self.m.LORA_REGISTRY['movement-v1']['filename']] if enabled else [])
                self.assertEqual(job['context'], frozen)
                if previous:
                    self.assertNotEqual(job['context']['lora_registry_identity'], previous['context']['lora_registry_identity'])
                previous = job
        self.assertFalse((self.m.COMFY_MODELS_DIR / 'LLM').exists())
        self.assertFalse((self.m.COMFY_MODELS_DIR / '.hf-cache').exists())

    async def test_changed_defaults_only_affect_new_jobs_after_reload(self):
        job = await self.job()
        frozen = copy.deepcopy(job['context'])
        def defaults(data):
            next(r for r in data['loras'] if r['id'] == 'hm-breasts-v2')['defaults']['h3_full'] = dict(enabled=False, strength=.2)
            next(r for r in data['loras'] if r['id'] == 'movement-v1')['defaults']['h3_full'] = dict(enabled=True, strength=.9)
        self.edit_registry(defaults)
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            recovered = restarted.find_job(job['id'])
            self.assertEqual(recovered['status'], 'render_queued_auto')
            self.assertEqual(recovered['context'], frozen)
            adapter = restarted.job_workflow_adapter(recovered)
            workflow = json.loads(restarted.patch_workflow(recovered, restarted.production_workflow(recovered),
                approved_prompt=restarted.final_prompt_for_render(recovered)).read_text())
            rows = adapter.node(workflow, 'lora_stack')['widgets_values_named']
            self.assertEqual(rows['lora_1']['lora'], 'HMBreastsV2.safetensors')
            self.assertEqual(rows['lora_1']['strength'], 1.0)
            new = restarted.workflow_context('h3_full')['loras']
            self.assertEqual([r for r in new if r['enabled']], [dict(id='movement-v1', enabled=True, strength=.9)])

    async def test_unused_removed_entries_and_metadata_do_not_invalidate_frozen_rows(self):
        job = await self.job(profile='10eros_full', render_settings=dict(second_pass_enabled=False,
            second_pass_loras=[dict(id='movement-v1', enabled=True, strength=.7)]))
        frozen = copy.deepcopy(job['context'])
        self.edit_registry(lambda data: data.update(loras=[r for r in data['loras'] if r['id'] != 'movement-v1']))
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            recovered = restarted.find_job(job['id'])
            self.assertEqual(recovered['status'], 'render_queued_auto')
            self.assertEqual(recovered['context'], frozen)
            restarted.patch_workflow(recovered, restarted.production_workflow(recovered),
                approved_prompt=restarted.final_prompt_for_render(recovered))

    async def test_new_adapter_can_be_enrolled_without_changing_existing_receipts(self):
        job = await self.job(profile='10eros_full')
        self.file('movement-v1')
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['movement-v1'])
        record = self.m.COMFY_MODELS_DIR / '.external_loras.json'
        original = json.loads(record.read_text())['artifacts']['movement-v1']
        def add(data):
            row = copy.deepcopy(next(r for r in data['loras'] if r['id'] == 'movement-v1'))
            row.update(id='added-external', filename='added_external.safetensors')
            data['loras'].append(row)
        self.edit_registry(add)
        new = self.m.COMFY_MODELS_DIR / 'loras/added_external.safetensors'
        new.write_bytes(b'New separately enrolled CPU adapter')
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['added-external'])
        self.assertEqual(json.loads(record.read_text())['artifacts']['movement-v1'], original)
        with self.assertRaises(ValueError):
            enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / 'lora_registry.json', ['movement-v1'])
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            self.assertEqual(restarted.find_job(job['id'])['status'], 'render_queued_auto')
            self.assertEqual([r for r in restarted.workflow_context('10eros_full', loras=[
                dict(id='added-external', enabled=True, strength=.2)])['loras'] if r['enabled']],
                [dict(id='added-external', enabled=True, strength=.2)])

    async def test_active_provenance_change_and_selection_tampering_still_fence_jobs(self):
        self.file('movement-v1')
        job = await self.job(profile='10eros_full', loras=[dict(id='movement-v1', enabled=True, strength=.4)])
        for mutate in (lambda c: c['loras'][-1].update(enabled=True),
                       lambda c: next(r for r in c['loras'] if r['id'] == 'movement-v1').update(strength=.6),
                       lambda c: c['loras'].reverse(),
                       lambda c: c['loras'].append(copy.deepcopy(c['loras'][0]))):
            broken = copy.deepcopy(job); mutate(broken['context'])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.m.job_profile(broken)
        self.edit_registry(lambda data: next(r for r in data['loras'] if r['id'] == 'movement-v1').update(filename='replacement.safetensors'))
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            self.assertEqual(restarted.find_job(job['id'])['status'], 'reconciliation_required')

    async def test_active_source_hash_native_and_checkpoint_fences_remain(self):
        self.fixture_manifest()
        job = await self.job(profile='10eros_full')
        await self.m.verify_production_files(job, 'render')
        model = self.m.COMFY_MODELS_DIR / 'diffusion_models' / self.m.PRODUCTION['profiles']['10eros_full']['checkpoint']
        model.write_bytes(model.read_bytes()[::-1])
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')
        native = self.m.COMFY_ROOT / 'custom_nodes/aj_production/temporal.py'
        native.write_text(native.read_text() + '\n# foreign implementation\n')
        with self.assertRaises(self.m.SubmissionRejected):
            self.m.verify_production_native(job)

    async def test_other_profile_compatibility_and_presentation_do_not_change_job(self):
        job = await self.job()
        def metadata(data):
            row = next(r for r in data['loras'] if r['id'] == 'hm-breasts-v2')
            row['compatibility']['10eros_full'] = 'incompatible'
            row.update(display_name='Updated label', notes='Operator annotation', metadata_evidence=['new documentation'])
        self.edit_registry(metadata)
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            self.assertEqual(restarted.job_profile(job), 'h3_full')

    async def test_active_trusted_hash_change_is_not_adopted_after_restart(self):
        job = await self.job()
        self.edit_registry(lambda data: next(r for r in data['loras'] if r['id'] == 'hm-breasts-v2').update(sha256='a' * 64))
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            self.assertEqual(restarted.find_job(job['id'])['status'], 'reconciliation_required')

    async def test_new_defaults_cannot_overflow_old_frozen_job_stack(self):
        job = await self.job(profile='10eros_full')
        def add(data):
            original = next(r for r in data['loras'] if r['id'] == 'movement-v1')
            for index in range(18):
                row = copy.deepcopy(original)
                row.update(id=f'added-{index}', filename=f'added_{index}.safetensors')
                row['defaults']['10eros_full']['enabled'] = True
                data['loras'].append(row)
        self.edit_registry(add)
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            recovered = restarted.find_job(job['id'])
            self.assertEqual(recovered['status'], 'render_queued_auto')
            restarted.patch_workflow(recovered, restarted.production_workflow(recovered),
                approved_prompt=restarted.final_prompt_for_render(recovered))

    async def test_new_pinned_optional_registry_resource_needs_no_global_manifest_entry(self):
        def add(data):
            row = copy.deepcopy(next(r for r in data['loras'] if r['id'] == 'hm-breasts-v2'))
            row.update(id='additional-trusted', filename='additional_trusted.safetensors')
            for value in row['defaults'].values():
                value['enabled'] = False
            data['loras'].append(row)
        self.edit_registry(add)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            result = self.cpu_invariants()
        self.assertEqual(result['baseline'], 'verified_v20')
        # An active existing destination cannot acquire conflicting provenance.
        self.edit_registry(lambda data: next(r for r in data['loras'] if r['id'] == 'hm-breasts-v2').update(sha256='b' * 64))
        with self.assertRaisesRegex(ValueError, 'provenance differs'):
            self.cpu_invariants()

    async def test_fixed_conditioning_bridge_still_requires_manifest_provenance(self):
        self.edit_registry(lambda data: data['conditioning_bridge'].update(filename='unregistered_bridge.safetensors'))
        with self.assertRaisesRegex(ValueError, 'provenance differs'):
            self.cpu_invariants()

