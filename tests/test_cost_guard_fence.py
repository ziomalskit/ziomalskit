"""F1: real controller dispatch fences with CPU/provider fault fixtures."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.helpers import load_controller


class CostGuardFenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        self.m.SESSION_STARTED_AT = time.time() - 3600
        self.m.vast_control.update(plan='none', cost_guard_usd=0.5, idle_minutes=0)
        self.job = dict(id='cost-fixture', status='render_queued_auto', created_at=0,
                        batch_seq=1, candidate_index=1, context={'prompt':'CPU fixture'})
        self.m.queue[:] = [self.job]

    async def tick(self, status):
        with patch.object(self.m, 'vast_instance_status', AsyncMock(return_value=status)), \
             patch.object(self.m, '_schedule_instance_action') as action, \
             patch.object(self.m.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.m.vast_guard_worker()
            action.assert_not_called()

    async def provider_status(self, data, cli=True):
        with ExitStack() as context:
            context.enter_context(patch.object(self.m, 'instance_id_from_env', return_value='CPU-fixture'))
            context.enter_context(patch.object(self.m.shutil, 'which', return_value='/CPU/vast' if cli else None))
            for name in ('persistent_storage_status', 'disk_status', 'gpu_status'):
                context.enter_context(patch.object(self.m, name, return_value={}))
            api = AsyncMock(side_effect=data) if isinstance(data, Exception) else AsyncMock(return_value=data)
            context.enter_context(patch.object(self.m, 'run_vast_cli', api))
            return await self.m.vast_instance_status()

    async def resume(self, estimate=0.1):
        with patch.object(self.m, 'vast_instance_status', AsyncMock(return_value={
                'estimated_session_compute_usd':estimate, 'hourly_usd':estimate})):
            return await self.m.api_vast_action(self.m.VastActionRequest(action='resume_cost_guard'))

    async def test_original_unknown_cost_dispatch_regression_is_blocked_and_durable(self):
        for label, cli, data in (
            ('missing CLI',False,{}), ('API timeout',True,TimeoutError('fixture timeout')),
            ('missing price',True,{}), ('malformed',True,{'dph_total':'invalid'}),
            ('NaN',True,{'dph_total':float('nan')}), ('infinity',True,{'dph_total':float('inf')}),
            ('negative',True,{'dph_total':-1}), ('boolean',True,{'dph_total':True}),
        ):
            with self.subTest(case=label):
                status = await self.provider_status(data,cli)
                self.assertIsNone(status.get('estimated_session_compute_usd'))
                await self.tick(status)
                self.assertIsNone(self.m.pick_next_render_job())
                self.assertTrue(self.m.vast_control['cost_hold'])
                self.assertEqual(self.m.vast_control['plan'],'none')
                stored=json.loads(self.m.VAST_CONTROL_FILE.read_text())
                self.assertTrue(stored['cost_hold'])
                self.assertTrue(self.m.state_diagnostics()['cost_guard_error'])
                self.assertFalse(self.m.state_diagnostics()['dispatch_ready'])

    async def test_enable_and_restart_require_explicit_verified_resume(self):
        await self.m.api_vast_action(self.m.VastActionRequest(action='set_cost_guard',cost_guard_usd=.5))
        self.assertIsNone(self.m.pick_next_render_job())
        await self.tick({'estimated_session_compute_usd':.1,'hourly_usd':.1})
        self.assertIsNone(self.m.pick_next_render_job(),'restored telemetry must not silently resume')
        await self.resume()
        self.assertIs(self.m.pick_next_render_job(),self.job)
        self.m.vast_control=self.m.load_vast_control()
        self.assertTrue(self.m.vast_control['cost_hold'])
        self.assertIsNone(self.m.pick_next_render_job())
        await self.resume()
        self.assertIs(self.m.pick_next_render_job(),self.job)

    async def test_above_limit_blocks_and_preserves_stop_after_current_behavior(self):
        await self.tick({'estimated_session_compute_usd':1,'hourly_usd':1})
        self.assertIsNone(self.m.pick_next_render_job())
        self.assertEqual(self.m.vast_control['plan'],'stop_after_current')
        with self.assertRaises(self.m.HTTPException):await self.resume(1)

    async def test_unknown_cost_does_not_interrupt_active_render_or_arm_lifecycle(self):
        self.job['status']='render_running'
        await self.tick({})
        self.assertEqual(self.job['status'],'render_running')
        self.assertEqual(self.m.vast_control['plan'],'none')
        self.assertTrue(self.m.vast_control['cost_hold'])

    async def test_stale_observation_closes_dispatch_before_next_guard_tick(self):
        await self.resume()
        self.m.cost_observation['at'] -= self.m.COST_TELEMETRY_MAX_AGE + 1
        self.assertIsNone(self.m.pick_next_render_job())
        self.assertTrue(self.m.vast_control['cost_hold'])

    async def test_telemetry_loss_during_preparation_cannot_reach_post_or_arm_uuid(self):
        await self.resume()
        preparing, finish = asyncio.Event(), asyncio.Event()
        async def prepare(*_):
            preparing.set();await finish.wait();return {}
        with patch.object(self.m,'_prepare_workflow_api',side_effect=prepare), \
             patch.object(self.m,'_dispatch_prepared_workflow',AsyncMock()) as post:
            task=asyncio.create_task(self.m._submit_workflow(self.job,Path('CPU.json'),'render'))
            await asyncio.wait_for(preparing.wait(),2)
            with patch.object(self.m,'vast_instance_status',AsyncMock(return_value={})):
                await self.m.refresh_cost_observation()
            finish.set()
            with self.assertRaises(self.m.SubmissionDeferred):await task
            post.assert_not_awaited()
            self.assertNotIn('render_prompt_id',self.job)

    async def test_late_verification_cannot_undo_newer_controls(self):
        started,finish=asyncio.Event(),asyncio.Event()
        async def delayed():
            started.set();await finish.wait();return {'estimated_session_compute_usd':.1,'hourly_usd':.1}
        with patch.object(self.m,'vast_instance_status',side_effect=delayed):
            task=asyncio.create_task(self.m.api_vast_action(self.m.VastActionRequest(action='resume_cost_guard')))
            await asyncio.wait_for(started.wait(),2)
            await self.m.api_vast_action(self.m.VastActionRequest(action='set_cost_guard',cost_guard_usd=.05))
            finish.set()
            with self.assertRaises(self.m.HTTPException):await task
        self.assertTrue(self.m.vast_control['cost_hold'])
        self.assertEqual(self.m.vast_control['cost_guard_usd'],.05)

    async def test_hold_and_persistence_failure_do_not_overwrite_shutdown_decision(self):
        self.m.vast_control.update(plan='stop_after_queue',armed_generation=self.m.queue_persistence_epoch)
        with patch.object(self.m,'_atomic_json_write',side_effect=OSError('CPU ENOSPC')):
            await self.tick({})
        self.assertEqual(self.m.vast_control['plan'],'stop_after_queue')
        self.assertIsNone(self.m.pick_next_render_job())

    async def test_cancel_plan_does_not_clear_hold_and_explicit_stop_remains_available(self):
        await self.tick({})
        await self.m.api_vast_action(self.m.VastActionRequest(action='cancel_plan'))
        self.assertIsNone(self.m.pick_next_render_job())
        with patch.object(self.m,'_schedule_instance_action') as action:
            await self.m.api_vast_action(self.m.VastActionRequest(action='stop_now',confirm='STOP'))
            action.assert_called_once_with('stop',2.0)
        self.assertEqual(self.m.vast_control['plan'],'executing_stop')

    async def test_fresh_cost_crossing_limit_closes_boundary_without_waiting_for_poll(self):
        await self.resume(.49)
        self.m.SESSION_STARTED_AT -= 300
        self.assertIsNone(self.m.pick_next_render_job())
        self.assertTrue(self.m.vast_control['cost_hold'])

    def test_ui_exposes_cost_hold_and_explicit_resume_without_total_bill_claim(self):
        page=(self.m.ROOT/'static/index.html').read_text()
        self.assertIn("vastAction('resume_cost_guard')",page)
        self.assertIn('NEW DISPATCHES PAUSED',page)
        self.assertIn('does not cap the total Vast bill',page)

    async def test_status_returns_same_fresh_cost_hold_as_diagnostics(self):
        status=await self.provider_status({},cli=False)
        self.assertTrue(status['plan']['cost_hold'])
        self.assertEqual(status['plan']['cost_hold_reason'],status['state']['cost_guard_error'])

    def test_corrupt_lifecycle_file_is_not_repaired_by_cost_diagnostics(self):
        body='{"cost_guard_usd":0.5,"control_generation":-1}'
        self.m.VAST_CONTROL_FILE.write_text(body)
        self.m.vast_control=self.m.load_vast_control()
        self.assertTrue(self.m.vast_control_load_error)
        self.assertTrue(self.m.cost_guard_error())
        self.assertEqual(self.m.VAST_CONTROL_FILE.read_text(),body)
        self.assertIsNone(self.m.pick_next_render_job())

    async def test_successful_verified_resume_clears_durable_prior_write_error(self):
        with patch.object(self.m,'_atomic_json_write',side_effect=OSError('CPU EIO')):
            self.m.cost_guard_error()
        self.assertTrue(self.m.vast_control['cost_hold_persistence_error'])
        await self.resume()
        stored=json.loads(self.m.VAST_CONTROL_FILE.read_text())
        self.assertNotIn('cost_hold_persistence_error',stored)
        self.assertFalse(stored['cost_hold'])
