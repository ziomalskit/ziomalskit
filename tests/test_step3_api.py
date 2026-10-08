"""STEP 3: real ASGI/JSON boundaries, durable state and deterministic races."""
import asyncio
from contextlib import asynccontextmanager
import json
import math
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import load_controller
from tests.test_controller import history
from tests.test_final_blocker_fixes import request
from tests.test_submission import fixture_job


@asynccontextmanager
async def api(module, app=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app or module.app),
                                base_url='http://fixture', auth=('h3', 'test-only-password')) as client:
        yield client


class Step3ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonfinite_json_strings_and_numbers_rejected_before_persistence(self):
        with load_controller() as m:
            body = request(m).model_dump(mode='json')
            m.save_state()
            original = m.QUEUE_FILE.read_bytes()
            name = m.MAP['nodes']['first_pass_loras']['entries'][0]['lora']
            async with api(m) as c:
                for enabled in (False, True):
                    for value in ('NaN', 'Infinity', '-Infinity', '1e400', float('nan'), float('inf'), -float('inf')):
                        with self.subTest(enabled=enabled, value=value):
                            body['loras'] = [dict(name=name, enabled=enabled, strength=value)]
                            r = await c.post('/api/batches', content=json.dumps(body), headers={'content-type':'application/json'})
                            self.assertEqual(r.status_code, 422, r.text)
                            json.dumps(r.json(), allow_nan=False)
                            self.assertEqual(m.QUEUE_FILE.read_bytes(), original)
                            self.assertEqual(m.queue, [])
                            self.assertEqual(m.batches, [])
                m.load_state()
                self.assertEqual((await c.get('/api/jobs')).status_code, 200)
                self.assertEqual(m.queue, [])

    async def test_finite_strength_boundaries_persist_reload_and_patch_exactly(self):
        with load_controller() as m:
            req = request(m)
            name = m.MAP['nodes']['first_pass_loras']['entries'][0]['lora']
            installed = m.COMFY_MODELS_DIR / 'loras' / name
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.write_bytes(b'fake model')
            for strength in (-2, 0, 0.7, 2):
                self.assertTrue(math.isfinite(m.LoraSetting(name=name, enabled=False, strength=strength).strength))
            async with api(m) as c:
                body = req.model_dump(mode='json')
                body['loras'] = [dict(name=name, enabled=True, strength=0.7)]
                self.assertEqual((await c.post('/api/batches', json=body)).status_code, 200)
                persisted = json.loads(m.QUEUE_FILE.read_text())
                json.dumps(persisted, allow_nan=False)
                m.queue.clear(); m.batches.clear(); m.load_state()
                self.assertEqual(len(m.queue), 10)
                self.assertEqual((await c.get('/api/jobs')).status_code, 200)
                path = m.patch_workflow(m.queue[0], m.MASTER, approved_prompt='approved')
                node = next(n for n in json.loads(path.read_text())['nodes'] if n['id'] == 6164)
                rows = [row for row in node['widgets_values'] if isinstance(row, dict) and row.get('lora') == name]
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['on'], rows[0]['strength']), (True, 0.7))
                disabled = fixture_job(m)
                disabled['context']['loras'] = [dict(name=name, enabled=False, strength=-0.4)]
                path = m.patch_workflow(disabled, m.MASTER, approved_prompt='approved')
                node = next(n for n in json.loads(path.read_text())['nodes'] if n['id'] == 6164)
                row = next(row for row in node['widgets_values'] if isinstance(row, dict) and row.get('lora') == name)
                self.assertEqual((row['on'], row['strength']), (False, -0.4))

    async def test_nonfinite_persisted_state_is_preserved_and_blocks_reload(self):
        with load_controller() as m:
            for value in ('NaN', 'Infinity', '-Infinity', '1e400'):
                raw = '{"queue":[{"context":{"loras":[{"strength":' + value + '}]}}],"batches":[]}'
                m.QUEUE_FILE.write_text(raw)
                m.state_load_error = None
                m.load_state()
                self.assertTrue(m.state_load_error)
                self.assertEqual(m.QUEUE_FILE.read_text(), raw)
                self.assertEqual(m.queue, [])
            with self.assertRaises(ValueError):
                m._atomic_json_write(m.STATE / 'invalid.json', {'value': float('nan')})
            self.assertFalse((m.STATE / 'invalid.json').exists())

    async def test_unknown_prefixed_duplicate_and_out_of_range_loras_reject_before_write(self):
        with load_controller() as m:
            body = request(m).model_dump(mode='json')
            name = m.MAP['nodes']['first_pass_loras']['entries'][0]['lora']
            installed = m.COMFY_MODELS_DIR / 'loras' / 'installed-but-unsupported.safetensors'
            installed.parent.mkdir(parents=True, exist_ok=True); installed.write_bytes(b'fake')
            m.save_state(); original = m.QUEUE_FILE.read_bytes()
            cases = [([dict(name=installed.name, enabled=True, strength=1)], 400),
                     ([dict(name='folder/'+name, enabled=True, strength=1)], 422),
                     ([dict(name='folder\\'+name, enabled=False, strength=1)], 422),
                     ([dict(name=name, enabled=False, strength=2.00001)], 422),
                     ([dict(name=name, enabled=True, strength=-2.00001)], 422),
                     ([dict(name=name, enabled=False, strength=1)]*2, 400)]
            async with api(m) as c:
                for settings, status in cases:
                    body['loras'] = settings
                    r = await c.post('/api/batches', json=body)
                    self.assertEqual(r.status_code, status, r.text)
                    self.assertEqual(m.QUEUE_FILE.read_bytes(), original)
                    self.assertEqual(m.queue, [])

    async def test_barrier_late_lifecycle_actions_cannot_cross_acknowledged_cancel(self):
        for action in ('stop_after_current', 'stop_after_queue', 'destroy_after_queue_keep_data', 'stop_now', 'destroy_now'):
            with self.subTest(action=action), load_controller() as m:
                arrived, release = asyncio.Event(), asyncio.Event()
                async def barrier(scope, receive, send):
                    if dict(scope.get('headers', [])).get(b'x-delay') == b'yes':
                        arrived.set(); await release.wait()
                    await m.app(scope, receive, send)
                with patch.object(m, 'persistent_storage_status', return_value={'safe_for_destroy_keep_data':True}), \
                     patch.object(m, '_schedule_instance_action') as dispatch:
                    async with api(m, barrier) as c:
                        token = m.lifecycle_control_token()
                        body = dict(action=action, control_token=token, confirm='STOP' if action == 'stop_now' else 'DESTROY')
                        late = asyncio.create_task(c.post('/api/vast/action', json=body, headers={'x-delay':'yes'}))
                        try:
                            await asyncio.wait_for(arrived.wait(), 2)
                            cancel = await c.post('/api/vast/action', json={'action':'cancel_plan','control_token':token})
                            self.assertEqual(cancel.status_code, 200)
                            self.assertNotEqual(cancel.json()['control_token'], token)
                        finally:
                            release.set()
                        response = await late
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(m.vast_control['plan'], 'none')
                        saved = json.loads(m.VAST_CONTROL_FILE.read_text())
                        self.assertEqual((saved['plan'], saved['control_generation']), ('none', 1))
                        m.vast_control = m.load_vast_control()
                        self.assertEqual(m.vast_control['control_generation'], 1)
                        dispatch.assert_not_called()

    async def test_lifecycle_missing_token_and_previous_boot_token_reject(self):
        with load_controller() as m:
            async with api(m) as c:
                self.assertEqual((await c.post('/api/vast/action', json={'action':'cancel_plan'})).status_code, 428)
                token = m.lifecycle_control_token()
                m.LIFECYCLE_CONTROL_EPOCH = 'new-process'
                self.assertEqual((await c.post('/api/vast/action', json={'action':'stop_after_queue','control_token':token})).status_code, 409)
                self.assertEqual(m.vast_control['plan'], 'none')

    async def test_concurrent_restart_is_one_owned_restart_and_inprogress_409(self):
        with load_controller() as m:
            entered, release = asyncio.Event(), asyncio.Event()
            async def restart(_command, **_options):
                entered.set(); await release.wait(); return 0, b'', b''
            with patch.object(m, '_run_owned_restart', side_effect=restart) as run:
                async with api(m) as c:
                    first = asyncio.create_task(c.post('/api/services/render/restart'))
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        second = await c.post('/api/services/render/restart')
                        self.assertEqual(second.status_code, 409)
                        self.assertEqual(m.service_maintenance, {'render':'restarting'})
                    finally:
                        release.set()
                    self.assertEqual((await first).status_code, 200)
                run.assert_awaited_once()
                self.assertEqual(m.service_maintenance, {})

    async def test_restart_pauses_queue_and_resume_dispatches_exactly_once(self):
        with load_controller() as m:
            job = fixture_job(m)
            job.update(status='render_queued_auto', final_h3_prompt='approved', batch_seq=1, candidate_index=1, created_at=1)
            m.queue[:] = [job]
            entered, release = asyncio.Event(), asyncio.Event()
            async def restart(_command, **_options):
                entered.set(); await release.wait(); return 0, b'', b''
            async def dispatch(_prepared, _workflow, _service, pid): return pid
            with patch.object(m, '_run_owned_restart', side_effect=restart), \
                 patch.object(m, '_prepare_workflow_api', AsyncMock(return_value={})), \
                 patch.object(m, '_dispatch_prepared_workflow', side_effect=dispatch) as post, \
                 patch.object(m, 'watch_prompt', AsyncMock(return_value={'ok':True})), \
                 patch.object(m, 'fetch_history', AsyncMock(return_value=history(video=True))):
                task = asyncio.create_task(m.restart_local_service('render'))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    self.assertIsNone(m.pick_next_render_job())
                    self.assertEqual(job['status'], 'render_queued_auto')
                    await m.render_job(job)  # admission raced with restart
                    self.assertEqual(job['status'], 'render_queued_auto')
                    post.assert_not_awaited()
                finally: release.set()
                await task
                self.assertIs(m.pick_next_render_job(), job)
                await m.render_job(job)
                self.assertEqual(job['status'], 'completed')
                self.assertIsNone(m.pick_next_render_job())
                post.assert_awaited_once()

    async def test_restart_during_preparation_defers_even_after_success_or_failure(self):
        with load_controller() as m:
            entered, release = asyncio.Event(), asyncio.Event()
            job = fixture_job(m); job.update(status='render_queued_auto', final_h3_prompt='approved')
            m.queue[:] = [job]
            async def prepare(*_args):
                entered.set(); await release.wait(); raise httpx.ConnectError('maintenance')
            with patch.object(m, '_prepare_workflow_api', side_effect=prepare), \
                 patch.object(m, '_run_owned_restart', AsyncMock(return_value=(0,b'',b''))), \
                 patch.object(m, '_dispatch_prepared_workflow', AsyncMock()) as post:
                task = asyncio.create_task(m.render_job(job))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    await m.restart_local_service('render')
                finally: release.set()
                await task
                self.assertEqual(job['status'], 'render_queued_auto')
                post.assert_not_awaited()
            with patch.object(m, '_run_owned_restart', AsyncMock(return_value=(1,b'',b'failed'))):
                with self.assertRaises(RuntimeError): await m.restart_local_service('render')
            self.assertEqual(m.service_maintenance['render'], 'unavailable')
            self.assertIsNone(m.pick_next_render_job())

    async def test_vast_nonfinite_prices_never_break_status_json(self):
        with load_controller() as m:
            with patch.object(m, 'instance_id_from_env', return_value='fake'), \
                 patch.object(m, 'persistent_storage_status', return_value={'safe_for_destroy_keep_data':False}), \
                 patch.object(m, 'gpu_status', return_value={'name':'fake'}):
                async with api(m) as c:
                    for price in ('NaN','Infinity','-Infinity','1e309',0.25,0,None,'invalid',float('nan'),float('inf')):
                        with self.subTest(price=price), patch.object(m, 'run_vast_cli', AsyncMock(return_value={
                                'dph_total':price, 'actual_status':'running', 'extra':{'value':float('nan')}})):
                            r = await c.get('/api/vast/status')
                            self.assertEqual(r.status_code, 200, r.text)
                            data = r.json(); json.dumps(data, allow_nan=False)
                            self.assertEqual(data['hourly_usd'], price if price in (0, 0.25) else None)
                            self.assertEqual(data['instance']['actual_status'], 'running')
                            self.assertIsNone(data['instance']['extra']['value'])
                            self.assertIn('state', data)
