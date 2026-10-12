"""Extended controls use existing durable publication/recovery and HTTP gates."""
import copy
import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from tests import test_production_controller as fixture
from tests.test_temporal_contract import CANONICAL, FINAL
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert

SETTINGS = {'target_mpx': .6, 'first_steps': 32, 'latent_upscaler_enabled': True,
    'latent_upscaler_mpx': 1.6, 'second_pass_enabled': True, 'second_steps': 7,
    'second_pass_loras': [{'id': 'hm-breasts-v2', 'enabled': True, 'strength': .75}]}


class RenderFeatureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ProductionControllerTests.setUp
    batch = fixture.ProductionControllerTests.batch
    transport = fixture.ProductionControllerTests.transport

    async def job(self, settings=None, profile='h3_full'):
        start = len(self.m.queue)
        self.req = self.req.model_copy(update={'request_id': uuid.uuid4(),
            'render_settings': self.m.RenderSettings.model_validate(SETTINGS if settings is None else settings)})
        await self.batch(profile)
        return self.m.queue[start]

    async def test_all_extended_fields_freeze_exactly_in_every_job_and_batch(self):
        job = await self.job()
        snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        for entry in [*snapshot['queue'], *snapshot['batches']]:
            self.assertEqual(entry['context']['render_settings'], SETTINGS)
            self.assertEqual(entry['context']['workflow_render_identity'], job['context']['workflow_render_identity'])
            self.assertEqual(entry['context']['profile_identity']['memory_policy'], self.m.profile_identity('h3_full')['memory_policy'])

    async def test_recovered_on_upscaler_second_pass_and_loras_are_exact(self):
        job = await self.job()
        job.update(status='render_running', render_prompt_id='12345678-1234-4234-8234-123456789abc')
        self.m.save_state(); before = copy.deepcopy(job['context'])
        self.m.queue.clear(); self.m.batches.clear(); self.m.load_state()
        recovered = self.m.find_job(job['id'])
        self.assertEqual(recovered['status'], 'recovery_render')
        self.assertEqual(recovered['context'], before)
        w = json.loads(self.m.patch_workflow(recovered, self.m.production_workflow(recovered), approved_prompt=FINAL).read_text())
        a = self.m.job_workflow_adapter(recovered)
        self.assertEqual(a.node(w, 'latent_upscaler')['mode'], 0)
        self.assertEqual(a.node(w, 'latent_upscaler')['widgets_values_named']['mode.megapixels'], 1.6)
        self.assertEqual(a.node(w, 'second_pass_enable')['mode'], 0)
        self.assertEqual(a.node(w, 'second_lora_stack')['widgets_values_named']['lora_1']['strength'], .75)

    async def test_recovered_off_features_keep_disabled_values_and_apply_no_branch(self):
        settings = {**SETTINGS, 'latent_upscaler_enabled': False, 'second_pass_enabled': False}
        job = await self.job(settings)
        self.m.load_state(); recovered = self.m.find_job(job['id'])
        self.assertEqual(recovered['context']['render_settings'], settings)
        w = json.loads(self.m.patch_workflow(recovered, self.m.production_workflow(recovered)).read_text())
        a = self.m.job_workflow_adapter(recovered)
        for role in ['latent_upscaler', 'second_pass_enable', 'second_lora_stack']:
            self.assertEqual(a.node(w, role)['mode'], 4)
        self.assertEqual(a.node(w, 'latent_upscaler')['widgets_values_named']['mode.megapixels'], .8)

    async def test_missing_any_extended_frozen_field_requires_reconciliation(self):
        await self.job(); snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        for key in ['latent_upscaler_enabled', 'latent_upscaler_mpx', 'second_pass_enabled', 'second_pass_loras']:
            broken = copy.deepcopy(snapshot); broken['queue'][0]['context']['render_settings'].pop(key)
            self.m.QUEUE_FILE.write_text(json.dumps(broken)); self.m.load_state()
            with self.subTest(key=key): self.assertEqual(self.m.queue[0]['status'], 'reconciliation_required')

    async def test_changed_product_target_default_cannot_mutate_existing_jobs(self):
        job = await self.job(); before = copy.deepcopy(job['context'])
        path = self.m.ROOT / 'workflow_adapters/h3_v20.json'; descriptor = json.loads(path.read_text())
        descriptor['render_controls']['latent_upscaler_mpx']['default'] = 1.9
        path.write_text(json.dumps(descriptor)); self.m.load_state()
        recovered = self.m.find_job(job['id'])
        self.assertEqual(recovered['context'], before)
        self.assertEqual(recovered['status'], 'reconciliation_required')

    async def test_old_pr6_identity_and_three_field_settings_are_not_migrated(self):
        job = await self.job(); snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        context = snapshot['queue'][0]['context']
        context['workflow_adapter_identity'] = 'a' * 64
        context['render_settings'] = {'target_mpx': .4, 'first_steps': 24, 'second_steps': 4}
        self.m.QUEUE_FILE.write_text(json.dumps(snapshot)); self.m.load_state()
        recovered = self.m.find_job(job['id'])
        self.assertEqual(recovered['status'], 'reconciliation_required')
        self.assertEqual(recovered['context']['render_settings'], context['render_settings'])

    async def test_lora_registry_defaults_and_added_ids_do_not_refill_frozen_selection(self):
        job = await self.job(); settings = copy.deepcopy(job['context']['render_settings'])
        path = self.m.ROOT / 'config/lora_registry.json'; data = json.loads(path.read_text())
        next(entry for entry in data['loras'] if entry['id']=='hm-breasts-v2')['defaults']['h3_full'] = {'enabled': False, 'strength': -1.5}
        added = copy.deepcopy(data['loras'][0]); added.update(id='extra-registered', filename='extra_registered.safetensors')
        data['loras'].append(added); path.write_text(json.dumps(data))
        self.m.load_state(); recovered = self.m.find_job(job['id'])
        self.assertEqual(recovered['context']['render_settings'], settings)
        self.assertNotIn('extra-registered', self.m.LORA_REGISTRY)
        self.m.patch_workflow(recovered, self.m.production_workflow(recovered))

    async def test_functional_identity_changes_for_each_toggle_target_and_lora_strength(self):
        baseline = self.m.workflow_context('h3_full', SETTINGS)['workflow_render_identity']
        for key, value in [('latent_upscaler_enabled', False), ('latent_upscaler_mpx', 1.8),
                           ('second_pass_enabled', False), ('second_steps', 8), ('second_pass_loras', [])]:
            changed = {**SETTINGS, key: value}
            self.assertNotEqual(self.m.workflow_context('h3_full', changed)['workflow_render_identity'], baseline)

    async def test_controller_prepares_all_four_routes_using_frozen_settings(self):
        for upscaler, second in [(False, False), (True, False), (False, True), (True, True)]:
            job = await self.job({**SETTINGS, 'latent_upscaler_enabled': upscaler, 'second_pass_enabled': second})
            job.update(active_service='render', creative_plan=CANONICAL)
            path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=FINAL)
            converted = exact_convert(json.loads(path.read_text()), production_catalog())
            transport, _ = self.transport()
            with transport, patch.object(self.m, 'run_cli_envelope', AsyncMock(return_value={'data': {'valid': True, 'error_count': 0}})):
                graph = await self.m._prepare_workflow_api(path, 'render', preview_envelope={'data': {'status': 'preview', 'prompt': converted}})
            incoming = ['6178', 0] if upscaler else ['7334:6773', 0]
            self.assertEqual(graph['7335:469']['inputs']['latent_image'], incoming)
            self.assertEqual('6263' in graph, second)
            self.assertEqual('6165' in graph, second)
            self.assertEqual(job['context']['profile_identity']['memory_policy'], self.m.profile_identity('h3_full')['memory_policy'])
            self.assertEqual(job['context']['profile_identity']['overlap_policy'], self.m.profile_identity('h3_full')['overlap_policy'])

    async def test_http_exact_extended_retry_is_idempotent_and_changed_toggle_conflicts(self):
        body = self.req.model_dump(mode='json'); body['render_settings'] = SETTINGS
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url='http://panel.test', auth=('h3', 'test-only-password')) as client:
            first = await client.post('/api/batches', json=body); self.assertEqual(first.status_code, 200, first.text)
            again = await client.post('/api/batches', json=body); self.assertEqual(again.json(), first.json())
            for key in ['second_pass_enabled', 'latent_upscaler_enabled']:
                changed = await client.post('/api/batches', json={**body, 'render_settings': {**SETTINGS, key: False}})
                self.assertEqual(changed.status_code, 409)
        self.assertEqual(len(self.m.queue), 10)
        self.assertEqual(self.m.queue[0]['context']['render_settings'], SETTINGS)

    async def test_http_bool_disguises_paths_widgets_unknown_ids_and_fractional_steps_fail(self):
        body = self.req.model_dump(mode='json')
        invalid = [{key: value} for key in ['second_pass_enabled', 'latent_upscaler_enabled'] for value in [0, 1, 'true', None]]
        invalid += [{'second_steps': 4.5}, {'latent_upscaler_mpx': '1.2'}, {'node_id': 6175},
            {'mode.megapixels': 1.2}, {'graph_path': '6175'}, {'widget_name': 'mode'},
            {'second_pass_loras': [{'id': 'unknown', 'strength': 1}]},
            {'second_pass_loras': [{'id': 'HMBreastsV2.safetensors', 'strength': 1}]},
            {'second_pass_loras': [{'id': '/tmp/lora.safetensors', 'strength': 1}]},
            {'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': True}]},
            {'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': 2.01}]},
            {'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': 1, 'filename': 'evil.safetensors'}]},
            {'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': 1, 'enabled': 1}]}]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url='http://panel.test', auth=('h3', 'test-only-password')) as client:
            for values in invalid:
                response = await client.post('/api/batches', json={**body, 'render_settings': values})
                self.assertIn(response.status_code, [400, 422], response.text)
        self.assertFalse(self.m.queue)

    async def test_http_nonfinite_mpx_or_lora_strength_fails_before_publication(self):
        body = self.req.model_dump(mode='json')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url='http://panel.test', auth=('h3', 'test-only-password')) as client:
            for value in [float('nan'), float('inf'), -float('inf')]:
                for values in [{'latent_upscaler_mpx': value}, {'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': value}]}]:
                    response = await client.post('/api/batches', content=json.dumps({**body, 'render_settings': values}), headers={'content-type': 'application/json'})
                    self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.m.queue)

    async def test_http_downscale_constraint_applies_only_when_enabled(self):
        body = self.req.model_dump(mode='json'); values = {'target_mpx': 1.6, 'latent_upscaler_mpx': 1.2, 'latent_upscaler_enabled': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url='http://panel.test', auth=('h3', 'test-only-password')) as client:
            bad = await client.post('/api/batches', json={**body, 'render_settings': values}); self.assertEqual(bad.status_code, 400)
            self.assertFalse(self.m.queue)
            good = await client.post('/api/batches', json={**body, 'render_settings': {**values, 'latent_upscaler_enabled': False}})
            self.assertEqual(good.status_code, 200, good.text)

    async def test_hash_tampering_each_extended_field_is_quarantined(self):
        await self.job(); snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        for key, value in [('latent_upscaler_enabled', False), ('latent_upscaler_mpx', 1.7),
                           ('second_pass_enabled', False), ('second_pass_loras', [])]:
            broken = copy.deepcopy(snapshot); broken['queue'][0]['context']['render_settings'][key] = value
            self.m.QUEUE_FILE.write_text(json.dumps(broken)); self.m.load_state()
            self.assertEqual(self.m.queue[0]['status'], 'reconciliation_required')
