"""Controller regressions: temporary state, mocked ComfyUI/Vast, CPU only."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

from tests.helpers import load_controller


def history(*, success=True, video=False, text=False):
    outputs = {}
    if text:
        outputs["preview"] = {"text": ["intermediate text"]}
    if video:
        outputs["save"] = {"videos": [{"filename": "result.mp4", "subfolder": "H3", "type": "output"}]}
    return {"outputs": outputs, "status": {"status_str": "success" if success else "error",
            "completed": success, "messages": [] if success else [["execution_error", {"exception_type": "RuntimeError"}]]}}


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"H3_ALLOW_SUBMISSIONS": "1"})
        self.environment.start()
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.m.vast_control.update({"plan": "none", "idle_minutes": 0, "cost_guard_usd": 0})

    def tearDown(self):
        self.fixture.__exit__(None, None, None)
        self.environment.stop()

    def job(self, *, service="render", status=None, pid=None):
        job = {"id": "test-job", "status": status or f"recovery_{service}", "active_service": service,
               "batch_seq": 1, "candidate_index": 1, "created_at": 1, "review_required": False,
               "approved_final_prompt": "approved scene", "final_h3_prompt": "scene", "context": {"prompt": "scene"}}
        if pid:
            job[f"{service}_prompt_id"] = pid
        self.m.queue[:] = [job]
        return job

    def remote(self, *, ready=True, entry=None, queue=None):
        patches = [patch.object(self.m, "wait_service_ready", AsyncMock(return_value=ready)),
                   patch.object(self.m, "fetch_history", AsyncMock(return_value={} if entry is None else entry)),
                   patch.object(self.m, "fetch_service_queue", AsyncMock(return_value=queue or {"queue_running": [], "queue_pending": []}))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_unicode_authentication_and_invalid_credentials(self):
        self.m.PANEL_AUTH_USER = "użytkownik"
        self.m.PANEL_AUTH_PASSWORD = "zażółć gęślą"
        token = lambda value: "Basic " + base64.b64encode(value.encode()).decode()
        self.assertTrue(self.m._authorized(token("użytkownik:zażółć gęślą")))
        self.assertFalse(self.m._authorized(token("użytkownik:błędne")))
        self.assertFalse(self.m._authorized("Basic !!!!"))
        self.assertFalse(self.m._authorized(None))

    async def test_gpu_and_mount_inspection_use_imported_subprocess(self):
        gpu_response = subprocess.CompletedProcess([], 0, "Blackwell,25,4096,98304,65,125\n", "")
        with patch.object(self.m.subprocess, "run", return_value=gpu_response) as run:
            gpu = self.m.gpu_status()
        self.assertEqual(gpu["vram_total_mb"], 98304)
        self.assertEqual(gpu["util_pct"], 25)
        run.assert_called_once()
        with patch.object(self.m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"filesystems": [{"source": "/dev/volume", "target": "/volume", "fstype": "ext4", "fsroot": "/", "uuid": "volume-uuid", "maj:min": "8:1"}]}), "")):
            self.assertEqual(self.m._mount_info(Path("/volume")), {"source": "/dev/volume", "target": "/volume",
                "fstype": "ext4", "fsroot": "/", "uuid": "volume-uuid", "maj:min": "8:1"})

    async def test_persistent_volume_verification_checks_all_protected_paths(self):
        root = self.m.ROOT / "persistent"
        root.mkdir()
        self.m.H3_PERSISTENT_ROOT = str(root)
        self.m.H3_PERSISTENCE_MODE = "volume"
        self.m.STATE = root / "state"
        self.m.COMFY_MODELS_DIR = root / "models"
        self.m.COMFY_OUTPUT_DIR = root / "output"
        self.m.COMFY_INPUT_DIR = root / "input"
        for directory in (self.m.STATE, self.m.COMFY_MODELS_DIR, self.m.COMFY_OUTPUT_DIR, self.m.COMFY_INPUT_DIR):
            directory.mkdir()

        def mounted(path):
            return {"source": "overlay", "target": "/", "fstype": "overlay", "fsroot": "/", "uuid": None, "maj:min": "0:1"} if path == Path("/") else mount

        mount = {"source": "volume", "target": str(root), "fstype": "ext4", "fsroot": "/", "uuid": "volume-uuid", "maj:min": "8:1"}
        proof = root / "volume-proof.json"
        proof.write_text(json.dumps({"provider": "vast-local-volume", "volume_id": 123, "retained_on_instance_destroy": True,
            "instance_id": "123", "boot_id": Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            "root_identity": [root.stat().st_dev, root.stat().st_ino], "mount": mount}))
        with patch.dict(os.environ, {"CONTAINER_ID": "123", "H3_PERSISTENT_VOLUME_PROOF": str(proof)}), patch.object(self.m, "_mount_info", side_effect=mounted):
            self.assertTrue(self.m.persistent_storage_status()["safe_for_destroy_keep_data"])
            self.m.COMFY_OUTPUT_DIR = self.m.ROOT / "outside-volume"
            self.assertFalse(self.m.persistent_storage_status()["safe_for_destroy_keep_data"])

    async def test_lifecycle_states_block_both_workers_approvals_and_batches(self):
        prompt = self.job(service="prompt", status="prompt_queued")
        render = {**prompt, "id": "render", "status": "render_queued_auto"}
        review = {**prompt, "id": "review", "status": "pending_review"}
        self.m.queue.extend([render, review])
        for plan in ("executing_stop", "executing_destroy", "executing_idle_stop", "action_failed", "action_interrupted", "blocked_unsafe_persistence"):
            with self.subTest(plan=plan):
                self.m.vast_control["plan"] = plan
                self.assertIsNone(self.m.pick_next_prompt_job())
                self.assertIsNone(self.m.pick_next_render_job())
                with self.assertRaises(self.m.HTTPException) as denied:
                    await self.m.approve("review", self.m.ApprovalRequest())
                self.assertEqual(denied.exception.status_code, 409)
                with self.assertRaises(self.m.HTTPException):
                    await self.m.create_batches(self.m.BatchRequest(prompt="scene"))

    async def test_recovery_barrier_is_per_service(self):
        prior = self.job(pid="previous")
        next_render = {**prior, "id": "next-render", "status": "render_queued_auto"}
        next_prompt = {**prior, "id": "next-prompt", "status": "prompt_queued", "active_service": "prompt"}
        self.m.queue.extend([next_render, next_prompt])
        self.assertIsNone(self.m.pick_next_render_job())
        self.assertIs(self.m.pick_next_prompt_job(), next_prompt)
        prior["status"] = "completed"
        self.assertIs(self.m.pick_next_render_job(), next_render)

    async def test_failed_render_with_preview_text_is_failed(self):
        job = self.job(pid="known")
        self.remote(entry=history(success=False, text=True))
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "render_failed")
        self.assertNotIn("video_outputs", job)
        self.m.fetch_service_queue.assert_not_awaited()

    async def test_render_success_requires_video_and_success_status(self):
        job = self.job(pid="known")
        self.remote(entry=history(text=True))
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "render_failed")
        job["status"] = "recovery_render"
        self.m.fetch_history.return_value = history(video=True)
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(job["video_outputs"]), 1)
        self.assertIn("filename=result.mp4", job["video_outputs"][0])
        self.assertEqual(len(job["outputs"]), 1)

    async def test_temp_video_and_audio_are_not_render_artifacts(self):
        entry = history()
        entry["outputs"] = {"save": {"files": [
            {"filename": "temp.mp4", "type": "temp"}, {"filename": "audio.wav", "type": "output"},
            {"filename": "still.png", "type": "output"}]}}
        job = self.job(pid="known")
        self.remote(entry=entry)
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "render_failed")

    async def test_prompt_history_requires_success_and_final_text(self):
        job = self.job(service="prompt", pid="known")
        entry = history()
        fallback = str(self.m.MAP["prompt_capture"]["fallback_top_level_final_node_id"])
        entry["outputs"] = {fallback: {"text": ["final scene"]}}
        self.remote(entry=entry)
        await self.m._recover_one_job(job, "prompt")
        self.assertEqual(job["status"], "render_queued_auto")
        self.assertEqual(job["final_h3_prompt"], "final scene")
        self.assertEqual(job["render_priority"], 10)

    async def test_history_request_failure_preserves_recovery(self):
        job = self.job(pid="known")
        self.remote()
        self.m.fetch_history.side_effect = ConnectionError("test history unavailable")
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.assertIn("retry", job["recovery_warning"])
        self.m.fetch_service_queue.assert_not_awaited()

    async def test_queue_failure_and_invalid_response_preserve_recovery(self):
        job = self.job(pid="known")
        self.remote()
        self.m.fetch_service_queue.side_effect = ConnectionError("test queue unavailable")
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.m.fetch_service_queue.side_effect = None
        self.m.fetch_service_queue.return_value = {}
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.assertIn("invalid queue", job["recovery_warning"])

    async def test_known_submitted_id_absence_never_automatically_requeues(self):
        job = self.job(pid="known")
        job["render_submission_attempted"] = True
        self.remote()
        for _ in range(2):
            await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.assertIn("manual reconciliation", job["recovery_warning"])

    async def test_pre_submit_no_id_recovery_has_no_external_calls(self):
        job = self.job(pid=None)
        self.remote(ready=False)
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "render_queued_review")
        self.m.wait_service_ready.assert_not_awaited()
        self.m.fetch_history.assert_not_awaited()
        self.m.fetch_service_queue.assert_not_awaited()

    async def test_attempt_without_id_stays_blocked(self):
        job = self.job(pid=None)
        job["render_submission_attempted"] = True
        self.remote()
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.assertIn("manual reconciliation", job["recovery_warning"])
        self.m.wait_service_ready.assert_not_awaited()

    async def test_restart_preserves_legacy_running_and_inflight_submission_uncertainty(self):
        for status in ("render_running", "render_submitting", "render_submission_uncertain", "render_preparing"):
            with self.subTest(status=status):
                self.job(status=status)
                self.m.save_state()
                self.m.load_state()
                job = self.m.queue[0]
                self.assertEqual(job["status"], "recovery_render")
                self.remote()
                await self.m._recover_one_job(job, "render")
                self.assertEqual(job["status"], "render_queued_review" if status == "render_preparing" else "recovery_render")

    async def test_background_recovery_retries_readiness_failure(self):
        job = self.job(pid="known")
        self.remote(entry=history(video=True))
        self.m.wait_service_ready.side_effect = [False, True]
        finished = asyncio.Event()
        blocked = asyncio.Event()
        real_sleep = asyncio.sleep

        async def next_tick(_seconds):
            if asyncio.current_task().get_name() == "h3-recovery-render":
                if job["status"] == "completed":
                    finished.set()
                    await blocked.wait()
                else:
                    await real_sleep(0)
            else:
                await blocked.wait()

        with patch.object(self.m.asyncio, "sleep", side_effect=next_tick):
            runner = asyncio.create_task(self.m.recover_jobs_after_controller_restart())
            try:
                await asyncio.wait_for(finished.wait(), timeout=1)
            finally:
                runner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await runner
        self.assertEqual(self.m.wait_service_ready.await_count, 2)
        self.assertEqual(job["status"], "completed")

    async def test_long_render_recovery_does_not_block_prompt_service_retries(self):
        render = self.job(pid="render-id")
        prompt = {**render, "id": "prompt", "active_service": "prompt", "status": "recovery_prompt", "prompt_prompt_id": "prompt-id"}
        self.m.queue.append(prompt)
        started = asyncio.Event()
        finished = asyncio.Event()
        blocked = asyncio.Event()
        real_sleep = asyncio.sleep
        prompt_reads = 0
        self.remote(queue={"queue_running": [[0, "render-id", {}]], "queue_pending": []})

        async def fetch(service, *_args, **_kwargs):
            nonlocal prompt_reads
            if service == "render":
                return {}
            prompt_reads += 1
            if prompt_reads == 1:
                raise ConnectionError("temporary prompt history failure")
            entry = history()
            fallback = str(self.m.MAP["prompt_capture"]["fallback_top_level_final_node_id"])
            entry["outputs"] = {fallback: {"text": ["final scene"]}}
            return entry

        async def watcher(*_args):
            started.set()
            await blocked.wait()

        async def next_tick(_seconds):
            if asyncio.current_task().get_name() == "h3-recovery-prompt" and prompt["status"] == "recovery_prompt":
                await real_sleep(0)
            else:
                if prompt["status"] == "render_queued_auto":
                    finished.set()
                await blocked.wait()

        self.m.fetch_history.side_effect = fetch
        with patch.object(self.m, "watch_prompt", side_effect=watcher), \
             patch.object(self.m.asyncio, "sleep", side_effect=next_tick):
            runner = asyncio.create_task(self.m.recover_jobs_after_controller_restart())
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                await asyncio.wait_for(finished.wait(), timeout=1)
                self.assertEqual(prompt_reads, 2)
                self.assertEqual(prompt["status"], "render_queued_auto")
                self.assertEqual(render["status"], "recovery_render")
            finally:
                runner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await runner

    async def test_corrupt_queue_state_is_preserved_and_blocks_mutation_and_dispatch(self):
        for raw in ("{", "42", '{"queue":{},"batches":[]}', '{"queue":[42],"batches":[]}'):
            with self.subTest(raw=raw):
                prior = self.job(service="prompt", status="prompt_queued")
                self.m.QUEUE_FILE.write_text(raw)
                self.m.load_state()
                self.assertIs(self.m.queue[0], prior)
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertIsNone(self.m.pick_next_prompt_job())
                self.assertIsNone(self.m.pick_next_render_job())
                with self.assertRaises(RuntimeError):
                    self.m.save_state()
                with self.assertRaises(self.m.HTTPException):
                    await self.m.create_batches(self.m.BatchRequest(prompt="scene"))
                with self.assertRaises(self.m.HTTPException):
                    await self.m.approve(prior["id"], self.m.ApprovalRequest())
                with self.assertRaises(self.m.HTTPException):
                    await self.m.api_vast_action(self.m.VastActionRequest(action="cancel_plan"))
                self.assertEqual(self.m.QUEUE_FILE.read_text(), raw)
                self.assertFalse((await self.m.config())["state"]["ready"])

    async def test_unreadable_queue_file_is_not_overwritten(self):
        self.job(status="render_queued_auto")
        self.m.save_state()
        original = self.m.QUEUE_FILE.read_bytes()
        with patch.object(Path, "read_text", side_effect=PermissionError("test queue read denied")):
            self.m.load_state()
        self.assertIn("read denied", self.m.state_load_error)
        with self.assertRaises(RuntimeError):
            self.m.save_state()
        self.assertEqual(self.m.QUEUE_FILE.read_bytes(), original)
        self.assertIsNone(self.m.pick_next_render_job())

    async def test_corrupt_lifecycle_state_is_preserved_and_blocks_all_work(self):
        for raw in ("{", "[]", '{"plan":123}', '{"plan":"unknown"}', '{"idle_minutes":"5"}', '{"cost_guard_usd":NaN}'):
            with self.subTest(raw=raw):
                self.m.VAST_CONTROL_FILE.write_text(raw)
                self.m.vast_control = self.m.load_vast_control()
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertTrue(self.m.lifecycle_dispatch_blocked())
                with self.assertRaises(RuntimeError):
                    self.m.save_vast_control()
                with self.assertRaises(self.m.HTTPException):
                    await self.m.api_vast_action(self.m.VastActionRequest(action="set_idle_timer", idle_minutes=5))
                self.assertEqual(self.m.VAST_CONTROL_FILE.read_text(), raw)

    async def test_unreadable_lifecycle_file_is_not_overwritten(self):
        self.m.save_vast_control()
        original = self.m.VAST_CONTROL_FILE.read_bytes()
        with patch.object(Path, "read_text", side_effect=PermissionError("test lifecycle read denied")):
            self.m.vast_control = self.m.load_vast_control()
        self.assertIn("read denied", self.m.vast_control_load_error)
        with self.assertRaises(RuntimeError):
            self.m.save_vast_control()
        self.assertEqual(self.m.VAST_CONTROL_FILE.read_bytes(), original)

    async def test_unavailable_state_blocks_submit_before_preparation(self):
        job = self.job(status="render_preparing")
        self.m.state_load_error = "test state unavailable"
        with patch.object(self.m, "_prepare_workflow_api", AsyncMock()) as prepare, \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
            with self.assertRaises(RuntimeError):
                await self.m._submit_workflow(job, self.m.MASTER, "render")
        prepare.assert_not_awaited()
        dispatch.assert_not_awaited()

    async def test_queued_recovery_attaches_without_resubmission(self):
        job = self.job(pid="known")
        self.remote(queue={"queue_running": [[0, "known", {}]], "queue_pending": []})
        with patch.object(self.m, "watch_prompt", AsyncMock(return_value={"ok": True})) as watcher:
            self.m.fetch_history.side_effect = [{}, history(video=True)]
            with patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
                await self.m._recover_one_job(job, "render")
                dispatch.assert_not_awaited()
        watcher.assert_awaited_once_with(job, "known", "render")
        self.assertEqual(job["status"], "completed")

    async def test_active_recovery_barrier_survives_transient_watcher_status(self):
        job = self.job(pid="known")
        self.m.queue.append({**job, "id": "next", "status": "render_queued_auto", "created_at": 2})
        self.remote(queue={"queue_running": [[0, "known", {}]], "queue_pending": []})

        async def watcher(*_args):
            job["status"] = "soft_timeout_cancelling"
            self.assertTrue(self.m.service_recovering("render"))
            self.assertIsNone(self.m.pick_next_render_job())
            return {"ok": False, "uncertain": True}

        with patch.object(self.m, "watch_prompt", side_effect=watcher):
            await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")
        self.assertEqual(self.m.recovering_services, set())

    async def test_uuid_is_durable_before_dispatch(self):
        job = self.job(status="render_preparing")
        captured = {}

        async def dispatch(prepared, workflow, service, prompt_id):
            captured.update(json.loads(self.m.QUEUE_FILE.read_text())["queue"][0])
            self.assertEqual(str(uuid.UUID(prompt_id)), prompt_id)
            self.assertEqual(captured["render_prompt_id"], prompt_id)
            self.assertEqual(captured["status"], "render_submitting")
            self.assertTrue(captured["render_submission_attempted"])
            return prompt_id

        with patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={"node": {}})), \
             patch.object(self.m, "_dispatch_prepared_workflow", side_effect=dispatch):
            prompt_id = await self.m._submit_workflow(job, self.m.MASTER, "render")
        self.assertEqual(job["render_prompt_id"], prompt_id)
        self.assertEqual(job["status"], "render_running")
        self.assertEqual(job["render_submission_state"], "accepted")

    async def test_timeout_or_mismatched_acknowledgement_is_not_resubmitted(self):
        for failure in (TimeoutError("test timeout"), "different-id"):
            with self.subTest(failure=str(failure)):
                job = self.job(status="render_preparing")
                dispatch = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=failure)
                with patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                     patch.object(self.m, "_dispatch_prepared_workflow", dispatch):
                    with self.assertRaises(self.m.SubmissionUncertain):
                        await self.m._submit_workflow(job, self.m.MASTER, "render")
                self.assertEqual(job["status"], "recovery_render")
                self.assertEqual(job["render_submission_state"], "uncertain")
                self.assertIsNotNone(job["render_prompt_id"])
                self.remote()
                await self.m._recover_one_job(job, "render")
                self.assertEqual(job["status"], "recovery_render")
                self.assertEqual(dispatch.await_count, 1)

    async def test_durable_state_failure_prevents_network_dispatch(self):
        job = self.job(status="render_preparing")
        with patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch, \
             patch.object(self.m.os, "fsync", side_effect=OSError("test storage failure")):
            with self.assertRaises(OSError):
                await self.m._submit_workflow(job, self.m.MASTER, "render")
        dispatch.assert_not_awaited()

    async def test_preparation_and_explicit_rejection_fail_without_unknown_state(self):
        for preparation_error in (True, False):
            with self.subTest(preparation_error=preparation_error):
                job = self.job(status="render_queued_auto")
                preparation = AsyncMock(side_effect=RuntimeError("test invalid conversion")) if preparation_error else AsyncMock(return_value={})
                dispatch = AsyncMock(side_effect=self.m.SubmissionRejected("test invalid graph"))
                with patch.object(self.m, "patch_workflow", return_value=self.m.MASTER), \
                     patch.object(self.m, "_prepare_workflow_api", preparation), \
                     patch.object(self.m, "_dispatch_prepared_workflow", dispatch):
                    with self.assertRaises(RuntimeError) as error:
                        await self.m.render_job(job)
                    self.m._handle_worker_error(job, "render", error.exception)
                self.assertEqual(job["status"], "render_failed")
                self.assertFalse(self.m.service_recovering("render"))
                if preparation_error:
                    self.assertNotIn("render_submission_attempted", job)
                    dispatch.assert_not_awaited()
                else:
                    self.assertEqual(job["render_submission_state"], "rejected")

    async def test_cancellation_during_preparation_sends_nothing(self):
        job = self.job(status="render_queued_auto")

        async def prepare(*_args):
            await self.m.cancel(job["id"])
            return {}

        with patch.object(self.m, "patch_workflow", return_value=self.m.MASTER), \
             patch.object(self.m, "_prepare_workflow_api", side_effect=prepare), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
            result = await self.m.submit_and_watch(job, self.m.MASTER, "render", "approved")
        dispatch.assert_not_awaited()
        self.assertTrue(result["cancelled"])
        self.assertEqual(job["status"], "cancelled")
        self.assertNotIn("render_submission_attempted", job)

    async def test_cancellation_during_submit_waits_for_ack_and_terminal_history(self):
        job = self.job(status="render_queued_auto")

        async def dispatch(prepared, workflow, service, prompt_id):
            outcome = await self.m.cancel(job["id"])
            self.assertTrue(outcome["pending"])
            self.assertEqual(job["status"], "render_submitting")
            self.assertTrue(self.m.current_work_running())
            return prompt_id

        with patch.object(self.m, "patch_workflow", return_value=self.m.MASTER), \
             patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})), \
             patch.object(self.m, "_dispatch_prepared_workflow", side_effect=dispatch), \
             patch.object(self.m, "cancel_prompt", AsyncMock()) as cancel, \
             patch.object(self.m, "watch_prompt", AsyncMock(return_value={"ok": False})), \
             patch.object(self.m, "fetch_history", AsyncMock(return_value=history(success=False))):
            await self.m.render_job(job)
        cancel.assert_awaited_once_with("render", job["render_prompt_id"])
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse(self.m.current_work_running())

    async def test_running_cancel_keeps_work_active_until_history_settles(self):
        job = self.job(status="render_running", pid="known")
        with patch.object(self.m, "cancel_prompt", AsyncMock()) as cancel:
            response = await self.m.cancel(job["id"])
        self.assertTrue(response["pending"])
        self.assertEqual(job["status"], "cancelling")
        self.assertTrue(self.m.current_work_running())
        self.assertNotIn("finished_at", job)
        cancel.assert_awaited_once_with("render", "known")

    async def test_late_cancel_acknowledgement_does_not_erase_terminal_history(self):
        for success in (True, False):
            with self.subTest(success=success):
                job = self.job(status="render_running", pid="known")

                async def cancel_after_completion(*_args):
                    # Actual history settlement runs while the network cancel
                    # request awaits; its result must survive the late reply.
                    self.m._apply_history_outcome(job, "render", history(success=success, video=success))

                with patch.object(self.m, "cancel_prompt", side_effect=cancel_after_completion):
                    await self.m.cancel(job["id"])
                self.assertEqual(job["status"], "completed" if success else "cancelled")
                self.assertFalse(self.m.current_work_running())

    async def test_late_cancel_acknowledgement_does_not_rewrite_new_phase_or_queue(self):
        for next_status in ("render_queued_auto", "pending_review", "prompt_failed", "render_running"):
            with self.subTest(next_status=next_status):
                job = self.job(service="prompt", status="prompt_running", pid="prompt-id")

                async def phase_advances(*_args):
                    job["status"] = next_status
                    if next_status == "render_running":
                        job["active_service"] = "render"
                        job["render_prompt_id"] = "render-id"

                with patch.object(self.m, "cancel_prompt", side_effect=phase_advances):
                    await self.m.cancel(job["id"])
                self.assertEqual(job["status"], next_status)

    async def test_cancel_does_not_rewrite_completed_job(self):
        job = self.job(status="completed", pid="known")
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.cancel(job["id"])
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(job["status"], "completed")

    async def test_shutdown_armed_during_preparation_defers_without_submission(self):
        for plan in ("stop_after_current", "executing_stop", "executing_destroy"):
            with self.subTest(plan=plan):
                self.m.vast_control["plan"] = "none"
                job = self.job(status="render_queued_auto")

                async def prepare(*_args):
                    self.m.vast_control["plan"] = plan
                    return {}

                with patch.object(self.m, "patch_workflow", return_value=self.m.MASTER), \
                     patch.object(self.m, "_prepare_workflow_api", side_effect=prepare), \
                     patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
                    result = await self.m.submit_and_watch(job, self.m.MASTER, "render", "approved")
                self.assertTrue(result["deferred"])
                dispatch.assert_not_awaited()
                self.assertEqual(job["status"], "render_queued_review")

    async def test_draining_shutdown_skips_only_unsubmitted_review_prompts(self):
        for plan in ("stop_after_queue", "destroy_after_queue_keep_data"):
            for review in (True, False):
                with self.subTest(plan=plan, review=review):
                    self.m.vast_control["plan"] = "none"
                    job = self.job(service="prompt", status="prompt_queued")
                    job["review_required"] = review

                    async def prepare(*_args):
                        self.m.vast_control["plan"] = plan
                        return {}

                    async def dispatch(prepared, workflow, service, prompt_id):
                        return prompt_id

                    with patch.object(self.m, "patch_workflow", return_value=self.m.PROMPT_ONLY), \
                         patch.object(self.m, "_prepare_workflow_api", side_effect=prepare), \
                         patch.object(self.m, "_dispatch_prepared_workflow", side_effect=dispatch) as dispatched, \
                         patch.object(self.m, "watch_prompt", AsyncMock(return_value={"ok": True})):
                        await self.m.submit_and_watch(job, self.m.PROMPT_ONLY, "prompt")
                    if review:
                        dispatched.assert_not_awaited()
                        self.assertEqual(job["status"], "review_skipped_shutdown")
                        self.assertNotIn("prompt_submission_attempted", job)
                    else:
                        self.assertEqual(dispatched.await_count, 1)
                        self.assertEqual(job["status"], "prompt_running")

    async def test_confirmed_targeted_cancel_without_history_remains_in_recovery(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                job = self.job(pid="known")
                job["cancel_requested"] = True
                self.m._record_cancel_confirmation(job, "render", "known", confirmed)
                self.remote()
                await self.m._recover_one_job(job, "render")
                self.assertEqual(job["status"], "recovery_render")
                self.assertTrue(self.m.current_work_running())
                self.assertNotIn("finished_at", job)

    async def test_confirmation_for_an_old_id_cannot_settle_new_submission(self):
        job = self.job(pid="new-id")
        job["cancel_requested"] = True
        self.m._record_cancel_confirmation(job, "render", "old-id", True)
        self.remote()
        await self.m._recover_one_job(job, "render")
        self.assertEqual(job["status"], "recovery_render")

    async def test_cpu_gate_prevents_preparation_dispatch_and_lifecycle_calls(self):
        job = self.job(status="render_preparing")
        with patch.dict(os.environ, {"H3_ALLOW_SUBMISSIONS": "0"}), \
             patch.object(self.m, "_prepare_workflow_api", AsyncMock()) as prepare, \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch, \
             patch.object(self.m, "_schedule_instance_action") as schedule:
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m._submit_workflow(job, self.m.MASTER, "render")
            with self.assertRaises(self.m.HTTPException):
                await self.m.api_vast_action(self.m.VastActionRequest(action="stop_now", confirm="STOP"))
        prepare.assert_not_awaited()
        dispatch.assert_not_awaited()
        schedule.assert_not_called()

    async def test_watcher_uncertainty_and_worker_exception_preserve_barrier(self):
        job = self.job(status="render_running", pid="known")
        job["render_submission_state"] = "accepted"
        await self.m._finish_watched_job(job, "render", {"ok": False, "uncertain": True, "error": "connection lost"})
        self.assertEqual(job["status"], "recovery_render")
        self.m._handle_worker_error(job, "render", ConnectionError("lost watcher"))
        self.assertEqual(job["status"], "recovery_render")
        self.assertTrue(self.m.current_work_running())

    async def test_guard_survives_failed_action_and_resumes_cost_checks_after_cancel(self):
        self.m.vast_control.update({"plan": "stop_after_current", "cost_guard_usd": 1,
                                    "armed_generation": self.m.queue_persistence_epoch})
        self.m.queue[:] = []
        sleeps = 0

        def failed_action(*_args):
            self.m.vast_control["plan"] = "action_failed"

        async def next_tick(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                self.assertEqual(self.m.vast_control["plan"], "action_failed")
                await self.m.api_vast_action(self.m.VastActionRequest(action="cancel_plan"))
            else:
                self.assertEqual(self.m.vast_control["plan"], "stop_after_current")
                raise asyncio.CancelledError()

        with patch.object(self.m, "_schedule_instance_action", side_effect=failed_action) as schedule, \
             patch.object(self.m, "vast_instance_status", AsyncMock(return_value={"estimated_session_compute_usd": 2})) as status, \
             patch.object(self.m.asyncio, "sleep", side_effect=next_tick):
            with self.assertRaises(asyncio.CancelledError):
                await self.m.vast_guard_worker()
        self.assertEqual(sleeps, 2)
        schedule.assert_called_once_with("stop")
        status.assert_awaited_once()

    async def test_late_cost_reply_does_not_overwrite_an_executing_lifecycle_action(self):
        for action, confirmation, expected_plan in (("stop_now", "STOP", "executing_stop"), ("destroy_now", "DESTROY", "executing_destroy")):
            with self.subTest(action=action):
                self.m.lifecycle_action_task = None
                self.m.vast_control.update({"plan": "none", "cost_guard_usd": 1})
                self.m.queue[:] = []

                def schedule(*_args):
                    self.m.lifecycle_action_task = Mock(done=Mock(return_value=False))

                async def delayed_status():
                    await self.m.api_vast_action(self.m.VastActionRequest(action=action, confirm=confirmation))
                    return {"estimated_session_compute_usd": 10}

                with patch.object(self.m, "_schedule_instance_action", side_effect=schedule), \
                     patch.object(self.m, "vast_instance_status", side_effect=delayed_status), \
                     patch.object(self.m.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())):
                    with self.assertRaises(asyncio.CancelledError):
                        await self.m.vast_guard_worker()
                self.assertEqual(self.m.vast_control["plan"], expected_plan)
                with self.assertRaises(self.m.HTTPException):
                    await self.m.api_vast_action(self.m.VastActionRequest(action="cancel_plan"))

    async def test_live_lifecycle_task_blocks_dispatch_even_if_plan_is_reset(self):
        self.m.lifecycle_action_task = Mock(done=Mock(return_value=False))
        self.m.vast_control["plan"] = "none"
        self.job(service="prompt", status="prompt_queued")
        self.assertIsNone(self.m.pick_next_prompt_job())
        self.assertTrue(self.m.shutdown_armed())
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.api_vast_action(self.m.VastActionRequest(action="cancel_plan"))
        self.assertEqual(error.exception.status_code, 409)

    async def test_actual_failed_lifecycle_action_is_reported(self):
        with patch.object(self.m, "instance_id_from_env", return_value="mock-instance"), \
             patch.object(self.m, "run_vast_cli", AsyncMock(side_effect=RuntimeError("test Vast failure"))) as cli, \
             patch.object(self.m.asyncio, "sleep", AsyncMock()):
            await self.m.delayed_instance_action("stop")
        cli.assert_awaited_once_with("stop", "instance", "mock-instance")
        self.assertEqual(self.m.vast_control["plan"], "action_failed")

    async def test_duplicate_lifecycle_action_and_cancellation_are_denied(self):
        with patch.object(self.m, "_schedule_instance_action") as schedule:
            await self.m.api_vast_action(self.m.VastActionRequest(action="stop_now", confirm="STOP"))
            for action, confirmation in (("stop_now", "STOP"), ("destroy_now", "DESTROY"), ("cancel_plan", None)):
                with self.assertRaises(self.m.HTTPException) as error:
                    await self.m.api_vast_action(self.m.VastActionRequest(action=action, confirm=confirmation))
                self.assertEqual(error.exception.status_code, 409)
        schedule.assert_called_once_with("stop", 2.0)

    async def test_confirmation_errors_precede_cpu_lifecycle_gate(self):
        with patch.dict(os.environ, {"H3_ALLOW_SUBMISSIONS": "0"}):
            for action in ("stop_now", "destroy_now"):
                with self.assertRaises(self.m.HTTPException) as error:
                    await self.m.api_vast_action(self.m.VastActionRequest(action=action, confirm="wrong"))
                self.assertEqual(error.exception.status_code, 400)

    async def test_keep_data_destroy_rechecks_mount_before_cli_dispatch(self):
        with patch.object(self.m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": False}), \
             patch.object(self.m, "run_vast_cli", AsyncMock()) as cli, \
             patch.object(self.m.asyncio, "sleep", AsyncMock()):
            await self.m.delayed_instance_action("destroy", require_persistence=True)
        cli.assert_not_awaited()
        self.assertEqual(self.m.vast_control["plan"], "blocked_unsafe_persistence")

    async def test_restart_failure_is_not_reported_as_success(self):
        with patch.object(self.m, "_run_owned_restart", AsyncMock(return_value=(1, b"", b"failure"))):
            with self.assertRaisesRegex(RuntimeError, "restart failed"):
                await self.m.restart_comfy("render")

    async def test_cancelling_watcher_reaps_local_child_without_cancelling_remote_job(self):
        job = self.job(status="render_running", pid="known")
        job["context"].update({"soft_timeout_minutes": 8, "hard_restart_after_seconds": 90})
        started = asyncio.Event()
        blocked = asyncio.Event()
        process = Mock(returncode=None)

        async def blocked_stdout():
            started.set()
            await blocked.wait()
            return b""

        async def exited():
            process.returncode = -15
            return -15

        process.stdout.readline = AsyncMock(side_effect=blocked_stdout)
        process.stderr.read = AsyncMock(return_value=b"")
        process.wait = AsyncMock(side_effect=exited)
        with patch.object(self.m.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)), \
             patch.object(self.m, "cancel_prompt", AsyncMock()) as remote_cancel:
            watcher = asyncio.create_task(self.m.watch_prompt(job, "known", "render"))
            await asyncio.wait_for(started.wait(), timeout=1)
            watcher.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await watcher
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()
        remote_cancel.assert_not_awaited()
        self.assertEqual(job["status"], "render_running")

    async def test_cancelling_preparation_cli_reaps_local_child(self):
        started = asyncio.Event()
        blocked = asyncio.Event()
        process = Mock(returncode=None)

        async def communicate():
            started.set()
            await blocked.wait()

        async def exited():
            process.returncode = -15
            return -15

        process.communicate = AsyncMock(side_effect=communicate)
        process.wait = AsyncMock(side_effect=exited)
        with patch.object(self.m.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            conversion = asyncio.create_task(self.m.run_cli_envelope("render", "run", "--print-prompt"))
            await asyncio.wait_for(started.wait(), timeout=1)
            conversion.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await conversion
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
