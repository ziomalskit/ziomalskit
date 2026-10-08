"""Cancellation recovery reconciles independent remote snapshots, CPU only."""
from __future__ import annotations

import errno
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import load_controller
from tests.test_controller import history

REAL_ASYNC_CLIENT = httpx.AsyncClient


class CancellationReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.job = {
            "id": "cancelled-render", "status": "recovery_render", "active_service": "render",
            "render_prompt_id": "known-id", "render_submission_attempted": True,
            "render_submission_state": "accepted", "cancel_requested": True,
        }
        self.m.queue[:] = [self.job]
        self.m._record_cancel_confirmation(self.job, "render", "known-id", True)

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def remote(self, entries):
        patches = [
            patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)),
            patch.object(self.m, "fetch_history", AsyncMock(side_effect=entries)),
            patch.object(self.m, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})),
            patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_success_between_history_and_queue_reads_preserves_durable_video(self):
        requests = []
        remote_history = {}

        def respond(request):
            nonlocal remote_history
            requests.append((request.method, request.url.path))
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={})
            if request.url.path == "/history/known-id":
                return httpx.Response(200, json=remote_history)
            if request.url.path == "/queue":
                # The worker atomically leaves running and publishes success
                # after the initial history reads, before final reconciliation.
                remote_history = {"known-id": history(video=True)}
                return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
            self.fail(f"Unexpected network operation: {request.method} {request.url}")

        def client(*args, **kwargs):
            return REAL_ASYNC_CLIENT(*args, **kwargs, transport=httpx.MockTransport(respond), trust_env=False)

        with patch.object(self.m.httpx, "AsyncClient", side_effect=client):
            await self.m._recover_one_job(self.job, "render")

        self.assertEqual(requests, [
            ("GET", "/system_stats"), ("GET", "/history/known-id"), ("GET", "/history/known-id"),
            ("GET", "/queue"), ("GET", "/history/known-id"),
        ])
        saved = json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["render_submission_state"], "settled")
        self.assertEqual(len(saved["video_outputs"]), 1)
        self.assertEqual(saved["outputs"], saved["video_outputs"])
        self.assertIn("filename=result.mp4", saved["video_outputs"][0])
        self.assertFalse(self.m.current_work_running())

    async def test_acknowledgement_and_absence_without_terminal_history_stay_blocked(self):
        self.remote([{}, {}, {}, {}])
        for _ in range(2):
            await self.m._recover_one_job(self.job, "render")
            self.assertEqual(self.job["status"], "recovery_render")
            self.assertEqual(self.job["render_submission_state"], "accepted")
            self.assertEqual(self.job["render_prompt_id"], "known-id")
            self.assertNotIn("finished_at", self.job)
            self.assertTrue(self.m.current_work_running())
        self.assertEqual(self.m.fetch_history.await_count, 4)
        self.m._dispatch_prepared_workflow.assert_not_awaited()

    async def test_final_history_read_failure_preserves_recovery_and_identity(self):
        self.remote([{}, ConnectionError("history temporarily unavailable")])
        await self.m._recover_one_job(self.job, "render")
        self.assertEqual(self.job["status"], "recovery_render")
        self.assertIn("Post-cancellation history unavailable", self.job["recovery_warning"])
        self.assertEqual(self.job["render_prompt_id"], "known-id")
        self.assertNotIn("finished_at", self.job)
        self.m._dispatch_prepared_workflow.assert_not_awaited()

    async def test_unsettled_history_with_output_is_not_terminal_cancellation_proof(self):
        partial = history(video=True)
        partial["status"] = {"status_str": "running", "completed": False}
        self.remote([{}, partial])
        await self.m._recover_one_job(self.job, "render")
        self.assertEqual(self.job["status"], "recovery_render")
        self.assertNotIn("finished_at", self.job)
        self.assertNotIn("video_outputs", self.job)
        self.m._dispatch_prepared_workflow.assert_not_awaited()

    async def test_authoritative_interruption_can_settle_user_cancel_and_timeout(self):
        self.remote([{}, history(success=False), {}, history(success=False)])
        for user_cancel in (True, False):
            with self.subTest(user_cancel=user_cancel):
                self.job["status"] = "recovery_render"
                self.job["cancel_requested"] = user_cancel
                self.job["render_cancel_reason"] = "timeout"
                await self.m._recover_one_job(self.job, "render")
                self.assertEqual(self.job["status"], "cancelled" if user_cancel else "render_stuck_skipped")
                self.assertEqual(self.job["render_submission_state"], "settled")
                self.assertIn("finished_at", self.job)
        self.m._dispatch_prepared_workflow.assert_not_awaited()

    async def test_reconciled_success_survives_local_persistence_failure(self):
        self.remote([{}, history(video=True)])
        with patch.object(self.m, "_atomic_json_write", side_effect=OSError(errno.ENOSPC, "full")):
            await self.m._recover_one_job(self.job, "render")
        self.assertEqual(self.job["status"], "completed")
        self.assertEqual(len(self.job["video_outputs"]), 1)
        self.assertIn("persistence_warning", self.job)
        self.assertIsNotNone(self.m.queue_persistence_error)
        self.m._resume_queue_persistence()
        saved = json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["video_outputs"], self.job["video_outputs"])
        self.m._dispatch_prepared_workflow.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
