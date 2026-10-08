"""Known-no-dispatch outcomes survive actual queue fsync faults in workers."""
import asyncio
import errno
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import load_controller
from tests.test_controller import history
from tests.test_submission import client_factory, fixture_job


async def until(predicate, worker, seconds=8):
    async with asyncio.timeout(seconds):
        while not predicate():
            if worker.done():
                worker.result()
                raise AssertionError('worker exited before its barrier')
            await asyncio.sleep(.01)


def job(m, service, identifier, index=1):
    item = fixture_job(m)
    item.update(id=identifier, batch_seq=1, candidate_index=index, created_at=index,
                status='prompt_queued' if service == 'prompt' else 'render_queued_review',
                review_required=service == 'prompt', final_h3_prompt='approved',
                approved_final_prompt='approved' if service == 'render' else None)
    return item


class DeferredPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def worker_fault(self, service, *, permanent=False, directory=False, shutdown=False, initial=False):
        with load_controller() as m:
            first, second = job(m, service, 'first'), job(m, service, 'second', 2)
            m.queue[:] = [first, second]
            m.save_state()
            entered, release, degraded, repair = [asyncio.Event() for _ in range(4)]
            dispatched, failed = [], False
            broken = True
            original_fsync = os.fsync
            original_wait = m._wait_for_queue_persistence

            async def prepare(*_args):
                if not entered.is_set():
                    entered.set()
                    await release.wait()
                return {}

            def fsync(fd):
                nonlocal failed, broken
                path = os.readlink(f'/proc/self/fd/{fd}')
                phase = first.get(service + '_submission_state')
                target = path == str(m.STATE) if directory else '.queue.json.' in path
                if broken and target and phase == ('preparing' if initial else 'deferred'):
                    failed = True
                    if not permanent:
                        broken = False
                    raise OSError(errno.EIO if not permanent else errno.ENOSPC, 'deferred queue fsync fault')
                return original_fsync(fd)

            async def wait_for_repair():
                if m.queue_persistence_error:
                    degraded.set()
                    await repair.wait()
                return await original_wait()

            async def dispatch(_graph, _path, actual_service, pid):
                self.assertEqual(actual_service, service)
                stored = json.loads(m.QUEUE_FILE.read_text())['queue']
                saved = next(j for j in stored if j.get(service + '_prompt_id') == pid)
                self.assertEqual(saved[service + '_submission_state'], 'armed')
                self.assertTrue(saved[service + '_submission_attempted'])
                dispatched.append((saved['id'], pid))
                return pid

            result = history(video=True) if service == 'render' else {
                'status': {'status_str': 'success', 'completed': True},
                'prompt': {'5732': {'class_type': 'PreviewAny', 'inputs': {}}},
                'outputs': {'5732': {'text': ['generated prompt']}},
            }
            with patch.object(os, 'fsync', side_effect=fsync), \
                 patch.object(m, '_wait_for_queue_persistence', side_effect=wait_for_repair), \
                 patch.object(m, '_prepare_workflow_api', side_effect=prepare) as preparation, \
                 patch.object(m, '_run_owned_restart', AsyncMock(return_value=(0, b'', b''))), \
                 patch.object(m, '_dispatch_prepared_workflow', side_effect=dispatch), \
                 patch.object(m, 'watch_prompt', AsyncMock(return_value={'ok': True})), \
                 patch.object(m, 'fetch_history', AsyncMock(return_value=result)):
                worker = asyncio.create_task(getattr(m, service + '_worker')())
                setattr(m, service + '_worker_task', worker)
                m.controller_started = True
                try:
                    if not initial:
                        await until(entered.is_set, worker)
                        if shutdown:
                            m.vast_control.update(plan='stop_after_current', armed_generation=m.queue_persistence_epoch)
                        else:
                            await m.restart_local_service(service)
                        release.set()
                    await until(degraded.is_set, worker)
                    self.assertTrue(failed)
                    self.assertEqual(dispatched, [])
                    self.assertIsNotNone(m.queue_persistence_error)
                    self.assertEqual(first['status'], 'prompt_queued' if service == 'prompt' else 'render_queued_review')
                    self.assertEqual(first[service + '_submission_state'], 'deferred')
                    self.assertIn('persistence_warning', first)
                    self.assertNotIn('finished_at', first)
                    self.assertNotIn(service + '_prompt_id', first)
                    self.assertNotIn(service + '_submission_attempted', first)
                    self.assertIsNone(getattr(m, 'pick_next_' + service + '_job')())
                    self.assertEqual(second['status'], 'prompt_queued' if service == 'prompt' else 'render_queued_review')
                    if initial:
                        preparation.assert_not_awaited()
                    if permanent:
                        with self.assertRaises(OSError):
                            m._resume_queue_persistence()
                        self.assertIsNotNone(m.queue_persistence_error)
                        self.assertEqual(dispatched, [])
                    if directory:
                        saved = json.loads(m.QUEUE_FILE.read_text())['queue'][0]
                        self.assertEqual(saved['status'], first['status'])
                        self.assertEqual(saved[service + '_submission_state'], 'deferred')
                        # The replaced queued/deferred snapshot proves no POST.
                        # A fresh controller can schedule it, but load never
                        # dispatches or invents a submitted id/attempt marker.
                        with load_controller() as restarted:
                            restarted.QUEUE_FILE = m.QUEUE_FILE
                            with patch.object(restarted, '_dispatch_prepared_workflow', AsyncMock()) as post:
                                restarted.load_state()
                                restored = restarted.queue[0]
                                self.assertEqual(restored['status'], first['status'])
                                self.assertFalse(restarted.service_recovering(service))
                                self.assertNotIn(service + '_prompt_id', restored)
                                self.assertNotIn(service + '_submission_attempted', restored)
                                self.assertIs(getattr(restarted, 'pick_next_' + service + '_job')(), restored)
                                post.assert_not_awaited()
                    broken = False
                    if shutdown:
                        # Persistence degradation invalidates the lifecycle arm;
                        # repair must not silently re-arm or bypass that fence.
                        self.assertEqual(m.vast_control['plan'], 'action_failed')
                        m.vast_control.update(plan='none', armed_generation=None)
                        m.save_vast_control()
                    release.set()
                    repair.set()
                    expected = 'completed' if service == 'render' else 'pending_review'
                    await until(lambda: all(j['status'] == expected for j in m.queue), worker)
                    self.assertEqual([identifier for identifier, _pid in dispatched], ['first', 'second'])
                    self.assertEqual(len({pid for _identifier, pid in dispatched}), 2)
                    self.assertIsNone(m.queue_persistence_error)
                    self.assertIsNone(getattr(m, 'pick_next_' + service + '_job')())
                finally:
                    release.set(); repair.set()
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    async def test_render_maintenance_file_fsync_eio_repairs_and_dispatches_once(self):
        await self.worker_fault('render')

    async def test_prompt_maintenance_file_fsync_eio_repairs_and_dispatches_once(self):
        await self.worker_fault('prompt')

    async def test_permanent_deferred_storage_failure_fences_both_workers_and_job_order(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.worker_fault(service, permanent=True)

    async def test_directory_fsync_after_replace_preserves_live_and_reloaded_no_dispatch(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.worker_fault(service, directory=True)

    async def test_shutdown_deferral_keeps_no_dispatch_outcome_through_storage_fault(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.worker_fault(service, shutdown=True)

    async def test_initial_queue_save_failure_before_preparation_remains_retryable(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.worker_fault(service, initial=True)

    async def terminal_control(self, service, failure):
        with load_controller() as m:
            item = job(m, service, 'invalid')
            m.queue[:] = [item]
            m.save_state()
            if failure == 'validation':
                # A genuine duplicate patch target fails production validation.
                path = m.MASTER if service == 'render' else m.PROMPT_ONLY
                graph = json.loads(path.read_text())
                graph['nodes'].append(next(n for n in graph['nodes'] if n['id'] == 1854).copy())
                path.write_text(json.dumps(graph))
                cli = AsyncMock()
            else:
                # Actual preparation rejects the invalid converter envelope.
                cli = AsyncMock(return_value={'data': {'status': 'invalid', 'prompt': {}}})
            with patch.object(m, 'run_cli_envelope', cli), \
                 patch.object(m, '_dispatch_prepared_workflow', AsyncMock()) as post:
                worker = asyncio.create_task(getattr(m, service + '_worker')())
                try:
                    await until(lambda: item['status'] == service + '_failed', worker)
                    self.assertEqual(item[service + '_submission_state'], 'preparing')
                    self.assertIn('finished_at', item)
                    self.assertIsNone(getattr(m, 'pick_next_' + service + '_job')())
                    post.assert_not_awaited()
                    if failure == 'validation':
                        cli.assert_not_awaited()
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    async def test_real_workflow_validation_failure_in_preparing_stays_terminal(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.terminal_control(service, 'validation')

    async def test_invalid_converter_envelope_in_preparing_stays_terminal(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service):
                await self.terminal_control(service, 'converter')

    async def test_authoritative_http_rejection_stays_terminal_in_both_workers(self):
        for service in ('render', 'prompt'):
            with self.subTest(service=service), load_controller() as m:
                item = job(m, service, 'rejected')
                m.queue[:] = [item]
                requests = []
                def reject(request):
                    requests.append(request)
                    return httpx.Response(400, json={'error': 'invalid workflow'})
                with patch.object(m, '_prepare_workflow_api', AsyncMock(return_value={})), \
                     patch.object(m.httpx, 'AsyncClient', side_effect=client_factory(reject)):
                    worker = asyncio.create_task(getattr(m, service + '_worker')())
                    try:
                        await until(lambda: item['status'] == service + '_failed', worker)
                        self.assertEqual(item[service + '_submission_state'], 'rejected')
                        self.assertIn('finished_at', item)
                        self.assertEqual(len(requests), 1)
                        self.assertEqual(requests[0].method, 'POST')
                        self.assertIsNone(getattr(m, 'pick_next_' + service + '_job')())
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)

    async def test_armed_attempting_accepted_uncertain_never_requeue_on_storage_error(self):
        for service in ('render', 'prompt'):
            for state in ('armed', 'attempting', 'accepted', 'uncertain'):
                with self.subTest(service=service, state=state), load_controller() as m:
                    item = job(m, service, 'submitted')
                    item.update(status=service + '_submitting', active_service=service)
                    item[service + '_submission_state'] = state
                    item[service + '_submission_attempted'] = True
                    item[service + '_prompt_id'] = 'known-remote-id'
                    m.queue[:] = [item]
                    m._handle_worker_error(item, service, OSError(errno.EIO, 'storage fault'))
                    self.assertEqual(item['status'], 'recovery_' + service)
                    self.assertIsNone(getattr(m, 'pick_next_' + service + '_job')())


if __name__ == '__main__':
    unittest.main()
