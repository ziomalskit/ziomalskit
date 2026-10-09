"""Final video contract, durable timing, and read-only legacy normalization."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import load_controller
from tests.test_controller import history
from tests.test_step3_api import api
from tests.test_submission import fixture_job


class ResultContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_records_canonical_result_and_timing_in_snapshot_and_api(self):
        with load_controller() as m:
            job = fixture_job(m)
            job.update(status="render_queued_auto", final_h3_prompt="final scene")
            m.queue[:] = [job]
            with patch.object(m.time, "time", side_effect=[100.0, 112.345]), \
                 patch.object(m, "submit_and_watch", AsyncMock(return_value={"ok": True})), \
                 patch.object(m, "fetch_history", AsyncMock(return_value=history(video=True))):
                await m.render_job(job)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["render_started_at"], 100.0)
            self.assertEqual(job["finished_at"], 112.345)
            self.assertEqual(job["render_duration_seconds"], 12.345)
            self.assertEqual(len(job["video_outputs"]), 1)
            saved = json.loads(m.QUEUE_FILE.read_text())["queue"][0]
            self.assertEqual(saved["video_outputs"], job["video_outputs"])
            m.queue.clear(); m.load_state()
            async with api(m) as c:
                single = (await c.get("/api/jobs/" + job["id"])).json()
                listed = (await c.get("/api/jobs")).json()["jobs"][0]
            self.assertEqual(single, listed)
            self.assertEqual(single["render_duration_seconds"], 12.345)
            self.assertIn("/api/proxy/render/view?filename=result.mp4", single["video_outputs"][0])

    async def test_recovery_preserves_start_and_does_not_submit_again(self):
        with load_controller() as m:
            job = fixture_job(m)
            job.update(status="recovery_render", render_started_at=100, render_prompt_id="accepted-id",
                       render_submission_state="accepted")
            m.queue[:] = [job]
            with patch.object(m, "wait_service_ready", AsyncMock(return_value=True)), \
                 patch.object(m, "fetch_history", AsyncMock(return_value=history(video=True))), \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as submit, \
                 patch.object(m.time, "time", return_value=130):
                await m._recover_one_job(job, "render")
            submit.assert_not_awaited()
            self.assertEqual(job["render_duration_seconds"], 30)
            self.assertEqual(job["render_started_at"], 100)

    async def test_missing_start_uses_history_only_when_available(self):
        with load_controller() as m:
            for timestamp, expected in ((100_000, 100), (None, None), ("100000", None)):
                with self.subTest(timestamp=timestamp):
                    job = {"id": "old", "status": "recovery_render"}
                    entry = history(video=True)
                    entry["status"]["messages"] = [["execution_start", {"timestamp": timestamp}]]
                    with patch.object(m.time, "time", return_value=120):
                        m._apply_history_outcome(job, "render", entry)
                    self.assertEqual(job.get("render_started_at"), expected)
                    self.assertEqual(job["render_duration_seconds"], 20 if expected else None)

    async def test_duration_handles_unknown_invalid_and_backwards_clock(self):
        with load_controller() as m:
            for start, finish in ((None, 2), (True, 2), ("1", 2), (float("nan"), 2),
                                  (1, float("inf")), (3, 2), (-1e308, 1e308)):
                self.assertIsNone(m.render_duration_seconds({"render_started_at": start, "finished_at": finish}))

    async def test_legacy_results_are_normalized_without_mutating_queue_or_disk(self):
        with load_controller() as m:
            video = "/api/proxy/render/view?filename=old.mp4&type=output"
            job = {"id": "old", "status": "completed", "outputs": [video, video,
                "/api/proxy/render/view?filename=image.png", "/api/proxy/prompt/view?filename=wrong.mp4",
                "/api/proxy/render/view?filename=temp.mp4&type=temp", "https://other/video.mp4", "//["],
                "render_started_at": 10, "finished_at": 15}
            m.queue[:] = [job]; m.save_state()
            original = m.QUEUE_FILE.read_bytes()
            async with api(m) as c:
                result = (await c.get("/api/jobs/old")).json()
            self.assertEqual(result["video_outputs"], [video])
            self.assertEqual(result["render_duration_seconds"], 5)
            self.assertNotIn("video_outputs", job)
            self.assertEqual(m.QUEUE_FILE.read_bytes(), original)

    async def test_failure_and_preview_never_become_completed_results(self):
        with load_controller() as m:
            for entry in (history(text=True), history(success=False, video=True)):
                job = {"id": "failed", "render_started_at": 100}
                with patch.object(m.time, "time", return_value=120):
                    self.assertTrue(m._apply_history_outcome(job, "render", entry))
                self.assertEqual(job["status"], "render_failed")
                self.assertNotIn("video_outputs", job)
                self.assertEqual(job["render_duration_seconds"], 20)
