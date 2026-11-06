"""Functional HTTP/controller journeys; no real inference, weights or provider.

AJ_E2E_COMFY_PYTHON=/path/to/pinned/python selects genuine comfy-cli child
processes. Normal CPU regression uses the existing verified converter fixture
and local validator, with only the CLI/watch boundary replaced.
"""
import asyncio
from collections import Counter
from contextlib import ExitStack
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import httpx

from tests.helpers import load_controller
from tests.functional_e2e_support import Backend, PNG, fixture_cli, stage_models, wav


class FunctionalJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2

    def tearDown(self):
        for backend in self.backends.values():
            self.assertTrue(all(backend.durable_arms), 'backend received POST without a durable UUID arm')
        trace_dir = os.environ.get('AJ_E2E_TRACE_DIR')
        if trace_dir:
            directory = Path(trace_dir)
            directory.mkdir(parents=True, exist_ok=True)
            trace = {'test': self.id(), 'cli': 'installed 1.21.0' if os.environ.get('AJ_E2E_COMFY_PYTHON') else 'CPU fixture',
                'inference': 'SIMULATED', 'queue_status_counts': dict(Counter(j['status'] for j in self.m.queue)), 'backends': {}}
            for service, backend in self.backends.items():
                trace['backends'][service] = {'requests': backend.calls, 'durable_arms': backend.durable_arms,
                    'submissions': [{'prompt_id': row['prompt_id'], 'nodes': len(row['prompt']),
                        'duration': row['prompt'].get('aj_frozen_duration', {}).get('inputs'),
                        'model_inputs': {key: {field: node['inputs'][field] for field in
                            ('model', 'unet_name', 'clip_name', 'vae_name', 'reasoning', 'ctx_size', 'max_tokens', 'extra_args')
                            if field in node['inputs']} for key, node in row['prompt'].items() if
                            any(field in node['inputs'] for field in ('unet_name', 'clip_name', 'vae_name')) or
                            'TextProcessor' in node['class_type']}}
                        for row in backend.submissions]}
            (directory / (self._testMethodName + '.json')).write_text(json.dumps(trace, indent=2) + '\n')

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.m = self.stack.enter_context(load_controller(production_probes=True))
        stage_models(self.m)
        self.backends = {service: Backend(self.m, service) for service in ('render', 'prompt')}
        for backend in self.backends.values():
            self.addCleanup(backend.close)
        self.m.RENDER_COMFY_URL = self.backends['render'].url
        self.m.PROMPT_COMFY_URL = self.backends['prompt'].url
        self.stack.enter_context(patch.dict(os.environ, {'H3_ALLOW_DIAGNOSTIC_SUBMISSIONS': '0',
            'NO_PROXY': '127.0.0.1,localhost', 'COMFY_CLI_NO_UPDATE_CHECK': '1'}))
        cli_python = os.environ.get('AJ_E2E_COMFY_PYTHON')
        if cli_python:
            self.m.COMFY_PYTHON = str(Path(cli_python).absolute())
        else:
            async def cli(service, *args):
                return await fixture_cli(self.m, self.backends[service], service, *args)
            async def watch(*_args):
                return {'ok': True}
            self.stack.enter_context(patch.object(self.m, 'run_cli_envelope', side_effect=cli))
            self.stack.enter_context(patch.object(self.m, 'watch_prompt', side_effect=watch))

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url='http://panel',
                                 auth=('h3', 'test-only-password'))

    async def assets(self, client, audio=False):
        names = []
        for i in range(6):
            result = await client.post('/api/upload', files={'file': (f'reference-{i}.png', PNG, 'image/png')})
            self.assertEqual(result.status_code, 200, result.text)
            names.append(result.json()['filename'])
        audio_names = []
        if audio:
            result = await client.post('/api/upload', files={'file': ('reference.wav', wav(), 'audio/wav')})
            self.assertEqual(result.status_code, 200, result.text)
            audio_names.append(result.json()['filename'])
        return dict(pictures=names, audio=audio_names)

    async def create(self, client, assets, **changes):
        body = dict(request_id=str(uuid.uuid4()), profile='h3_full', prompt_mode='manual_final',
                    manual_final_prompt='Reference 1 walks across the sunlit room. Preserve identity and clothing.',
                    duration_seconds=5, seed=12345, batches=1, **assets)
        body.update(changes)
        start = len(self.m.queue)
        response = await client.post('/api/batches', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        repeated = await client.post('/api/batches', json=body)
        self.assertEqual(repeated.json(), response.json())
        saved = json.loads(self.m.QUEUE_FILE.read_text())
        self.assertEqual(saved['queue'], self.m.queue)
        return self.m.queue[start:], body

    async def execute(self, job, service):
        method = self.m.render_job if service == 'render' else self.m.generate_prompt_candidate
        await asyncio.wait_for(method(job), timeout=45)

    async def test_manual_profile_switches_upload_snapshot_dispatch_result_and_range(self):
        async with self.client() as client:
            assets = await self.assets(client, audio=True)
            config = await client.get('/api/config')
            self.assertEqual(config.status_code, 200)
            for profile in ('h3_full', '10eros_full', 'h3_full'):
                jobs, _ = await self.create(client, assets, profile=profile)
                job = jobs[0]
                self.assertEqual(len(jobs), 1)
                await self.execute(job, 'render')
                self.assertEqual(job['status'], 'completed', job.get('error'))
                graph = self.backends['render'].submissions[-1]['prompt']
                expected = self.m.PRODUCTION['profiles'][profile]
                self.assertEqual(graph['4595:4529']['inputs']['unet_name'], expected['checkpoint'])
                self.assertEqual(graph['4595:130']['inputs']['clip_name'], expected['encoder'])
                self.assertEqual(graph['2632']['inputs']['positive'], job['context']['manual_final_prompt'])
                self.assertFalse(any('TextProcessor' in n['class_type'] for n in graph.values()))
                self.assertEqual(graph['aj_frozen_duration']['inputs'],
                    {'legal_frame_count': 124, 'effective_duration_seconds': 5.16})
                public = (await client.get('/api/jobs/' + job['id'])).json()
                url = public['video_outputs'][0]
                download = await client.get(url)
                self.assertEqual(download.content, self.backends['render'].media)
                partial = await client.get(url, headers={'Range': 'bytes=0-15'})
                self.assertEqual(partial.status_code, 206)
                self.assertEqual(partial.content, download.content[:16])
            self.assertEqual(len(self.backends['render'].submissions), 3)
            self.assertEqual(self.backends['prompt'].calls, [])
            self.m.load_state()
            self.assertIsNone(self.m.state_load_error)
            self.assertEqual([j['status'] for j in self.m.queue], ['completed'] * 3)

    async def test_autoprompt_both_profiles_cache_auto_five_and_review_approval(self):
        async with self.client() as client:
            assets = await self.assets(client)
            previous_step0 = None
            for profile in ('h3_full', '10eros_full'):
                jobs, _ = await self.create(client, assets, profile=profile, duration_seconds=20,
                    prompt_mode='autoprompt', manual_final_prompt=None, prompt='A person walks through a sunlit room.')
                self.assertEqual(len(jobs), 10)
                self.assertEqual(sum(not j['review_required'] for j in jobs), 5)
                for job in jobs:
                    await self.execute(job, 'prompt')
                    self.assertIn(job['status'], {'render_queued_auto', 'pending_review'}, job.get('error'))
                    graph = self.backends['prompt'].submissions[-1]['prompt']
                    if previous_step0 is not None:
                        self.assertIn('aj_analysis_visual_facts', graph)
                    if job['candidate_index'] > 1:
                        self.assertTrue({'aj_analysis_expanded_intent', 'aj_analysis_reference_map'} <= graph.keys())
                    previous_step0 = True
                    self.assertEqual(graph['aj_frozen_duration']['inputs'],
                        {'legal_frame_count': 481, 'effective_duration_seconds': 20.04})
                    self.assertEqual(graph['2551:2640:4275']['inputs']['model'], self.m.PRODUCTION['profiles'][profile]['writer'])
                    if not job['review_required']:
                        await self.execute(job, 'render')
                        self.assertEqual(job['status'], 'completed', job.get('error'))
                review = jobs[5]
                response = await client.post('/api/jobs/' + review['id'] + '/approve', json={})
                self.assertEqual(response.status_code, 200, response.text)
                repeated = await client.post('/api/jobs/' + review['id'] + '/approve', json={})
                self.assertEqual(repeated.json(), response.json())
                await self.execute(review, 'render')
                self.assertEqual(review['status'], 'completed')
                for job in jobs[6:]:
                    response = await client.post('/api/jobs/' + job['id'] + '/reject', json={})
                    self.assertEqual(response.status_code, 200)
            self.assertEqual(len(self.backends['prompt'].submissions), 20)
            self.assertEqual(len(self.backends['render'].submissions), 12)
            self.assertEqual(len({b['prompt_id'] for b in self.backends['render'].submissions}), 12)

    async def test_missing_and_changed_model_receipts_block_before_submission(self):
        async with self.client() as client:
            assets = await self.assets(client)
            for failure in ('missing', 'changed', 'receipt'):
                jobs, _ = await self.create(client, assets)
                job = jobs[0]
                path = self.m.model_path_for_preset('h3_full')
                if failure == 'missing':
                    path.unlink()
                elif failure == 'changed':
                    path.write_bytes(b'wrong model')
                else:
                    receipt = self.m.COMFY_MODELS_DIR / '.verification/h3-transformer.json'
                    receipt.unlink()
                with self.assertRaises(self.m.SubmissionRejected):
                    await self.execute(job, 'render')
                self.assertEqual(self.backends['render'].submissions, [])
                job['status'] = 'render_failed'
                stage_models(self.m)

    async def test_lost_ack_restart_recovers_same_uuid_without_second_post(self):
        async with self.client() as client:
            jobs, body = await self.create(client, await self.assets(client))
            self.backends['render'].failure = 'lost_ack'
            await self.execute(jobs[0], 'render')
            self.assertEqual(jobs[0]['status'], 'recovery_render')
            self.m.load_state()
            job = self.m.queue[0]
            await self.m._recover_one_job(job, 'render')
            self.assertEqual(job['status'], 'completed', job.get('recovery_warning'))
            retry = await client.post('/api/batches', json=body)
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(len(self.m.queue), 1)
            self.assertEqual(len(self.backends['render'].submissions), 1)

    async def test_malformed_prompt_and_missing_video_never_become_success(self):
        async with self.client() as client:
            assets = await self.assets(client)
            for failure in ('bad_final', 'bad_time', 'interrupted'):
                jobs, _ = await self.create(client, assets, prompt_mode='autoprompt', manual_final_prompt=None, prompt='Walk. ' + failure)
                self.backends['prompt'].failure = failure
                await self.execute(jobs[0], 'prompt')
                self.assertEqual(jobs[0]['status'], 'prompt_failed')
                if failure == 'interrupted':
                    analysis = self.m.batch_for_job(jobs[0])['analysis']['texts']
                    self.assertEqual(set(analysis), {'visual_facts'})
                    self.backends['prompt'].failure = None
                    await self.execute(jobs[1], 'prompt')
                    graph = self.backends['prompt'].submissions[-1]['prompt']
                    self.assertIn('aj_analysis_visual_facts', graph)
                    self.assertNotIn('aj_analysis_expanded_intent', graph)
                for job in jobs[1:]:
                    job['status'] = 'cancelled'
            self.assertEqual(self.backends['render'].submissions, [])
            jobs, _ = await self.create(client, assets)
            self.backends['render'].failure = 'no_video'
            await self.execute(jobs[0], 'render')
            self.assertEqual(jobs[0]['status'], 'render_failed')

    async def test_real_workers_stop_drain_restart_and_durable_auto_five(self):
        async with self.client() as client:
            assets = await self.assets(client)
            manual, _ = await self.create(client, assets, profile='10eros_full')
            auto, _ = await self.create(client, assets, prompt_mode='autoprompt',
                manual_final_prompt=None, prompt='Walk through a sunlit room.')
            async def control(action):
                result = await client.post('/api/vast/action', json={
                    'action': action, 'control_token': self.m.lifecycle_control_token()})
                self.assertEqual(result.status_code, 200, result.text)

            await control('stop_after_current')
            self.m.load_state()
            self.m.vast_control = self.m.load_vast_control()
            # Restart deliberately converts an armed provider plan into a
            # fail-closed hold; an operator must make a fresh decision.
            self.assertEqual(self.m.vast_control['plan'], 'action_interrupted')
            self.assertIsNone(self.m.pick_next_render_job())
            self.assertIsNone(self.m.pick_next_prompt_job())
            await control('cancel_plan')
            await control('stop_after_queue')
            # The lifecycle/provider worker is deliberately never launched.
            # Only production prompt/render scheduling and draining run here.
            tasks = [asyncio.create_task(self.m.prompt_worker()), asyncio.create_task(self.m.render_worker())]
            try:
                async with asyncio.timeout(100):
                    while not self.m.queue_finished_for_shutdown():
                        for task in tasks:
                            if task.done():
                                task.result()
                                self.fail('worker exited')
                        failed = [j for j in self.m.queue if j.get('status') in {'prompt_failed', 'render_failed', 'failed'}]
                        self.assertEqual(failed, [], str(failed))
                        await asyncio.sleep(.1)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            saved = json.loads(self.m.QUEUE_FILE.read_text())['queue']
            self.assertEqual(sum(j['status'] == 'completed' for j in saved), 6)
            self.assertEqual(sum(j['status'] == 'review_skipped_shutdown' for j in saved), 5)
            self.assertEqual(len(self.backends['render'].submissions), 6)
            self.assertEqual(len(self.backends['prompt'].submissions), 5)
            self.m.load_state()
            self.assertIsNone(self.m.state_load_error)
            self.assertEqual(sum(j['status'] == 'completed' for j in self.m.queue), 6)

    async def test_controls_loras_audio_and_profile_writer_budgets_reach_graph(self):
        async with self.client() as client:
            assets = await self.assets(client, audio=True)
            settings = {'target_mpx': .6, 'first_steps': 32, 'latent_upscaler_enabled': True,
                'latent_upscaler_mpx': 1.2, 'second_pass_enabled': True, 'second_steps': 9,
                'second_pass_loras': [{'id': 'hm-breasts-v2', 'enabled': True, 'strength': .75}]}
            for profile in ('h3_full', '10eros_full'):
                jobs, _ = await self.create(client, assets, profile=profile, render_settings=settings,
                    loras=[{'id': 'hm-breasts-v2', 'enabled': True, 'strength': .5}])
                await self.execute(jobs[0], 'render')
                graph = self.backends['render'].submissions[-1]['prompt']
                for identifier, field, value in (('1731:1735', 'megapixels', .6), ('7334:6771', 'steps', 32),
                        ('6175', 'mode.megapixels', 1.2), ('7335:360', 'steps', 9)):
                    self.assertEqual(graph[identifier]['inputs'][field], value)
                self.assertEqual(graph['6164']['inputs']['lora_1']['strength'], .5)
                self.assertEqual(graph['6165']['inputs']['lora_1']['strength'], .75)
                jobs, _ = await self.create(client, assets, profile=profile, loras=[
                    {'id': key, 'enabled': False, 'strength': 1} for key in self.m.LORA_REGISTRY])
                await self.execute(jobs[0], 'render')
                graph = self.backends['render'].submissions[-1]['prompt']
                self.assertFalse(any(v.get('on') for k, v in graph['6164']['inputs'].items() if k.startswith('lora_')))
                jobs, _ = await self.create(client, assets, profile=profile, prompt_mode='autoprompt',
                    manual_final_prompt=None, prompt='Walk.')
                await self.execute(jobs[0], 'prompt')
                graph = self.backends['prompt'].submissions[-1]['prompt']
                policy = self.m.PRODUCTION['profiles'][profile]
                for stage, key in (('step3', '2551:2640:2445'), ('step4', '2551:2640:4275')):
                    inputs = graph[key]['inputs']
                    for field in ('reasoning', 'max_tokens', 'ctx_size'):
                        self.assertEqual(inputs[field], policy['writer_stages'][stage][field])
                    self.assertIn('--reasoning-budget ' + str(policy['writer_stages'][stage]['reasoning_budget']), inputs['extra_args'])
                for job in jobs:
                    job['status'] = 'cancelled'

    async def test_rejection_unknown_execution_and_corrupt_queue_are_fail_closed(self):
        async with self.client() as client:
            assets = await self.assets(client)
            jobs, _ = await self.create(client, assets)
            self.backends['render'].failure = 'unavailable'
            with self.assertRaises(httpx.HTTPStatusError):
                await self.execute(jobs[0], 'render')
            self.assertEqual(self.backends['render'].submissions, [])
            self.m._handle_worker_error(jobs[0], 'render', RuntimeError('endpoint unavailable'))
            self.assertEqual(jobs[0]['status'], 'render_failed')
            jobs, _ = await self.create(client, assets)
            self.backends['render'].failure = 'reject'
            with self.assertRaises(self.m.SubmissionRejected):
                await self.execute(jobs[0], 'render')
            self.assertEqual(jobs[0]['render_submission_state'], 'rejected')
            self.m._handle_worker_error(jobs[0], 'render', self.m.SubmissionRejected('rejected'))
            self.assertEqual(jobs[0]['status'], 'render_failed')
            jobs, _ = await self.create(client, assets)
            self.backends['render'].failure = 'lost_ack'
            await self.execute(jobs[0], 'render')
            self.backends['render'].histories.clear()
            self.backends['render'].epoch = '2' * 32
            self.m.load_state()
            await self.m._recover_one_job(self.m.queue[-1], 'render')
            self.assertEqual(self.m.queue[-1]['status'], 'recovery_render')
            self.assertEqual(len(self.backends['render'].submissions), 1)
            self.m.QUEUE_FILE.write_text('{incomplete queue write')
            self.m.load_state()
            response = await client.post('/api/batches', json={**assets, 'request_id': str(uuid.uuid4()),
                'prompt_mode': 'manual_final', 'manual_final_prompt': 'Walk.', 'duration_seconds': 5})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.m.QUEUE_FILE.read_text(), '{incomplete queue write')


if __name__ == '__main__':
    unittest.main()
