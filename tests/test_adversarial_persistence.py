"""Regression reproductions from the independent adversarial audit; CPU only."""
from __future__ import annotations

import asyncio
import copy
import errno
import io
import json
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from h3.scripts import preflight
from tests.helpers import load_controller
from tests.test_controller import history


class AdversarialPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.m.COMFY_INPUT_DIR.mkdir(parents=True)
        names = [f"ref_{index}.png" for index in range(6)]
        for name in names + ["__h3_silence_1s.wav"]:
            (self.m.COMFY_INPUT_DIR / name).write_bytes(b"CPU fixture")
        model = self.m.model_path_for_preset("h3_full")
        model.parent.mkdir(parents=True)
        model.write_bytes(b"CPU fixture")
        self.request = self.m.BatchRequest(request_id=uuid.uuid4(), prompt="inert reference scene", pictures=names, profile="h3_full")
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def review_job(self):
        job = {"id": "prior-review", "status": "pending_review", "batch_id": "prior-batch", "profile": "h3_full",
               "batch_seq": 1, "candidate_index": 6, "created_at": 1,
               "review_required": True, "final_h3_prompt": "prior candidate",
               "render_seed": 1, "analysis_seed": 2, "prompt_seed": 3,
               "context": self.request.model_dump(mode="json")}
        self.m.queue.append(job)
        self.m.batches.append({"id": "prior-batch", "seq": 1})
        self.m.save_state()
        return job

    def writer_failure(self, permanent=False):
        real_write = self.m._atomic_json_write
        failures = 0

        def write(path, payload):
            nonlocal failures
            if path == self.m.QUEUE_FILE and (permanent or failures == 0):
                failures += 1
                raise OSError(errno.ENOSPC, "adversarial storage full")
            real_write(path, payload)
        return write

    def start_workers(self):
        self.m.controller_started = True
        for service in ("prompt", "render"):
            task = asyncio.create_task(getattr(self.m, service + "_worker")())
            setattr(self.m, service + "_worker_task", task)
            self.tasks.append(task)

    async def wait_until(self, predicate):
        async with asyncio.timeout(8):
            while not predicate():
                for task in self.tasks:
                    if task.done() and not task.cancelled():
                        task.result()
                await asyncio.sleep(0.01)

    def remote_boundaries(self):
        calls = []

        async def dispatch(_prepared, _workflow, service, prompt_id):
            stored = json.loads(self.m.QUEUE_FILE.read_text())["queue"]
            self.assertTrue(any(job.get(service + "_prompt_id") == prompt_id for job in stored))
            calls.append((service, prompt_id))
            return prompt_id

        async def fetch(service, _prompt_id, **_options):
            if service == "render":
                return history(video=True)
            return {"status": {"status_str": "success", "completed": True},
                    "prompt": {"5732": {"class_type": "PreviewAny", "inputs": {}}},
                    "outputs": {"5732": {"text": ["generated candidate"]}}}

        return calls, [patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})),
                       patch.object(self.m, "_dispatch_prepared_workflow", side_effect=dispatch),
                       patch.object(self.m, "watch_prompt", AsyncMock(return_value={"ok": True})),
                       patch.object(self.m, "fetch_history", side_effect=fetch)]

    async def failed_request_and_scheduler(self, *, approval, permanent):
        prior = self.review_job()
        queue_before, batches_before = copy.deepcopy(self.m.queue), copy.deepcopy(self.m.batches)
        queue_identity, batch_identity = self.m.queue, self.m.batches
        invoke = (lambda: self.m.approve(prior["id"], self.m.ApprovalRequest(final_prompt="approved edit"))) if approval else (
            lambda: self.m.create_batches(self.request))
        calls, boundaries = self.remote_boundaries()
        for boundary in boundaries:
            boundary.start()
            self.addCleanup(boundary.stop)
        with patch.object(self.m, "_atomic_json_write", side_effect=self.writer_failure(permanent)):
            with self.assertRaises(OSError) as error:
                await invoke()
            self.assertEqual(error.exception.errno, errno.ENOSPC)
            self.assertEqual(self.m.queue, queue_before)
            self.assertEqual(self.m.batches, batches_before)
            self.assertIs(self.m.queue, queue_identity)
            self.assertIs(self.m.batches, batch_identity)
            self.assertIs(self.m.queue[0], prior)
            self.assertFalse(self.m.state_diagnostics()["ready"])
            self.assertIsNone(self.m.pick_next_render_job())
            self.start_workers()
            await asyncio.sleep(0.06)
            self.assertEqual(calls, [], "failed requests must not submit even one new job")
            self.assertTrue(all(not task.done() for task in self.tasks))
            if permanent:
                with self.assertRaises(OSError):
                    await invoke()
                self.assertEqual(self.m.queue, queue_before)
                self.assertEqual(self.m.batches, batches_before)
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertEqual(calls, [])
        await invoke()
        expected = 1 if approval else 5
        await self.wait_until(lambda: sum(job["status"] == "completed" for job in self.m.queue) == expected)
        renders = [pid for service, pid in calls if service == "render"]
        self.assertEqual(len(renders), expected)
        self.assertEqual(len(set(renders)), expected)
        self.assertEqual(len(self.m.queue), 1 if approval else 11)
        self.assertEqual(len(self.m.batches), 1 if approval else 2)
        if not approval:
            self.assertEqual(self.m.batches[-1]["seq"], 2)
        else:
            self.assertEqual(await invoke(), {"ok": True, "priority": "top"})
            self.assertEqual(len([pid for service, pid in calls if service == "render"]), expected)
        self.assertTrue(self.m.state_diagnostics()["ready"])

    async def test_create_one_shot_enospc_retry_runs_only_five_durable_renders(self):
        await self.failed_request_and_scheduler(approval=False, permanent=False)

    async def test_create_permanent_enospc_retry_dispatches_nothing_until_storage_recovers(self):
        await self.failed_request_and_scheduler(approval=False, permanent=True)

    async def test_approval_one_shot_enospc_retry_runs_one_durable_render(self):
        await self.failed_request_and_scheduler(approval=True, permanent=False)

    async def test_approval_permanent_enospc_preserves_review_until_durable_retry(self):
        await self.failed_request_and_scheduler(approval=True, permanent=True)

    async def test_create_state_is_not_published_while_durable_commit_is_in_progress(self):
        real_write = self.m._atomic_json_write
        observed = []

        def write(path, payload):
            if path == self.m.QUEUE_FILE:
                observed.append((len(self.m.queue), len(self.m.batches), self.m.pick_next_prompt_job()))
                self.assertEqual(len(payload["queue"]), 10)
            real_write(path, payload)
        with patch.object(self.m, "_atomic_json_write", side_effect=write):
            await self.m.create_batches(self.request)
        self.assertEqual(observed, [(0, 0, None)])
        self.assertEqual(len(self.m.queue), 10)
        self.assertFalse(self.m.QUEUE_PUBLICATION_FILE.exists())

    async def test_approval_durable_commit_precedes_publication_and_preserves_job_identity(self):
        job = self.review_job()
        real_write = self.m._atomic_json_write

        def write(path, payload):
            if path == self.m.QUEUE_FILE:
                self.assertEqual(job["status"], "pending_review")
                self.assertIsNone(self.m.pick_next_render_job())
                self.assertEqual(payload["queue"][0]["status"], "render_queued_review")
            real_write(path, payload)
        with patch.object(self.m, "_atomic_json_write", side_effect=write):
            await self.m.approve(job["id"], self.m.ApprovalRequest())
        self.assertIs(self.m.queue[0], job)
        self.assertEqual(job["status"], "render_queued_review")

    async def test_shutdown_armed_while_waiting_for_queue_lock_blocks_publication(self):
        for approval in (False, True):
            with self.subTest(approval=approval):
                self.m.queue[:] = []
                self.m.batches[:] = []
                self.m.vast_control.update(plan="none")
                job = self.review_job()
                before = copy.deepcopy(self.m.queue)
                async with self.m.queue_lock:
                    request = asyncio.create_task(self.m.approve(job["id"], self.m.ApprovalRequest()) if approval else
                                                  self.m.create_batches(self.request))
                    self.tasks.append(request)
                    await asyncio.sleep(0)
                    await self.m.api_vast_action(self.m.VastActionRequest(action="stop_after_current"))
                with self.assertRaises(self.m.HTTPException) as error:
                    await request
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.m.queue, before)

    async def test_legacy_interrupted_publication_marker_still_blocks_boot(self):
        before = copy.deepcopy(self.m.queue)
        self.m.QUEUE_PUBLICATION_FILE.write_text('{"pending":true}')
        self.m.load_state()
        self.assertEqual(self.m.queue, before)
        self.assertFalse(self.m.state_diagnostics()["ready"])
        self.assertTrue(self.m.QUEUE_PUBLICATION_FILE.exists())
        with self.assertRaises(RuntimeError):
            self.m.save_state()

    async def test_ambiguous_post_replace_failure_boot_replays_committed_create_and_approval(self):
        job = self.review_job()
        for approval in (False, True):
            with self.subTest(approval=approval):
                before = copy.deepcopy(self.m.queue)
                real_write = self.m._atomic_json_write

                def write(path, payload):
                    real_write(path, payload)
                    if path == self.m.QUEUE_FILE:
                        raise OSError(errno.EIO, "directory fsync failed after replace")
                with patch.object(self.m, "_atomic_json_write", side_effect=write):
                    with self.assertRaises(OSError):
                        if approval:
                            await self.m.approve(job["id"], self.m.ApprovalRequest())
                        else:
                            await self.m.create_batches(self.request)
                self.assertEqual(self.m.queue, before)
                self.assertFalse(self.m.QUEUE_PUBLICATION_FILE.exists())
                disk = self.m.QUEUE_FILE.read_bytes()
                with load_controller() as restarted:
                    restarted.QUEUE_FILE = self.m.QUEUE_FILE
                    restarted.QUEUE_PUBLICATION_FILE = self.m.QUEUE_PUBLICATION_FILE
                    restarted.load_state()
                    self.assertTrue(restarted.state_diagnostics()["ready"])
                    if approval:
                        self.assertEqual(await restarted.approve(job["id"], restarted.ApprovalRequest()), {"ok": True, "priority": "top"})
                    else:
                        result = await restarted.create_batches(self.request)
                        self.assertEqual(result, restarted.request_results[str(self.request.request_id)]["result"])
                    self.assertEqual(len(restarted.queue), 1 if approval else 11)
                self.assertEqual(self.m.QUEUE_FILE.read_bytes(), disk)
                # A live repair writes only the original published snapshot.
                self.m._resume_queue_persistence()
                self.assertEqual(json.loads(self.m.QUEUE_FILE.read_text())["queue"], before)
                self.assertFalse(self.m.QUEUE_PUBLICATION_FILE.exists())

    async def test_permanent_enospc_error_handler_keeps_uncertain_submission_in_recovery(self):
        for service in ("prompt", "render"):
            with self.subTest(service=service):
                job = {"id": "accepted", "status": service + "_running",
                       service + "_prompt_id": "durable-known-id", service + "_submission_state": "accepted"}
                self.m.queue[:] = [job]
                with patch.object(self.m, "_atomic_json_write", side_effect=OSError(errno.ENOSPC, "full")):
                    self.m._handle_worker_error(job, service, ConnectionError("watcher disconnected"))
                self.assertEqual(job["status"], "recovery_" + service)
                self.assertEqual(job[service + "_prompt_id"], "durable-known-id")
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertIn("persistence_warning", job)

    async def test_workers_survive_secondary_enospc_and_recover_without_paid_retry(self):
        for service in ("prompt", "render"):
            with self.subTest(service=service):
                self.m.queue[:] = []
                self.m.batches[:] = []
                job = self.review_job()
                job["status"] = "prompt_queued" if service == "prompt" else "render_queued_review"
                job["approved_final_prompt"] = "approved candidate"
                self.m.queue_persistence_error = None
                original_task = getattr(self.m, service + "_worker_task")
                self.m.controller_started = True
                with patch.object(self.m, "_atomic_json_write", side_effect=OSError(errno.ENOSPC, "full")), \
                     patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as boundary:
                    task = asyncio.create_task(getattr(self.m, service + "_worker")())
                    self.tasks.append(task)
                    setattr(self.m, service + "_worker_task", task)
                    await self.wait_until(lambda: self.m.queue_persistence_error is not None)
                    await asyncio.sleep(0.03)
                    self.assertFalse(task.done())
                    boundary.assert_not_awaited()
                    self.assertFalse(self.m.state_diagnostics()["ready"])
                await self.wait_until(lambda: self.m.queue_persistence_error is None)
                self.assertFalse(task.done())
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                setattr(self.m, service + "_worker_task", original_task)
                self.m.queue[:] = []

    async def test_missing_or_dead_required_task_is_not_ready_and_blocks_dispatch(self):
        for name in ("prompt_worker_task", "render_worker_task", "recovery_task", "vast_guard_task"):
            with self.subTest(task=name):
                original = getattr(self.m, name)
                setattr(self.m, name, None)
                self.assertFalse(self.m.state_diagnostics()["ready"])
                task = asyncio.create_task(asyncio.sleep(0))
                await task
                setattr(self.m, name, task)
                self.m.controller_started = True
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertIsNone(self.m.pick_next_prompt_job())
                self.assertIsNone(self.m.pick_next_render_job())
                setattr(self.m, name, original)
        self.assertTrue(self.m.state_diagnostics()["ready"])

    async def test_unexpected_worker_exception_is_visible_and_blocks_other_dispatch(self):
        self.m.controller_started = True
        with patch.object(self.m, "pick_next_render_job", side_effect=RuntimeError("unexpected worker fault")):
            task = asyncio.create_task(self.m.render_worker())
            self.m.render_worker_task = task
            self.tasks.append(task)
            with self.assertRaisesRegex(RuntimeError, "unexpected worker fault"):
                await task
        self.assertFalse(self.m.state_diagnostics()["ready"])
        self.assertIn("render", self.m.state_diagnostics()["worker_error"])
        self.assertIsNone(self.m.pick_next_prompt_job())
        with patch.object(self.m, "_prepare_workflow_api", AsyncMock()) as prepare:
            with self.assertRaises(RuntimeError):
                await self.m._submit_workflow({}, self.m.MASTER, "prompt")
        prepare.assert_not_awaited()

    async def test_real_startup_reports_task_death_and_shutdown_reaps_workers(self):
        await self.m.startup()
        try:
            await asyncio.sleep(0)
            self.assertTrue(self.m.state_diagnostics()["ready"])
            self.m.render_worker_task.cancel()
            await asyncio.gather(self.m.render_worker_task, return_exceptions=True)
            self.assertFalse(self.m.state_diagnostics()["ready"])
        finally:
            await self.m.shutdown()
        self.assertTrue(all(task.done() for task in (self.m.prompt_worker_task, self.m.render_worker_task,
                                                   self.m.recovery_task, self.m.vast_guard_task)))

    async def test_preflight_rejects_actual_dead_worker_and_degraded_persistence(self):
        critical = ["LLMTextProcessor", "BunnyH3ConditioningBridge", "MinimaxH3LatentUpscaler3D",
                    "MergeImageBatchAndAudioList", "Power Lora Loader (rgthree)", "Seed (rgthree)"]
        dead = asyncio.create_task(asyncio.sleep(0))
        await dead
        original = self.m.render_worker_task
        for fault in ("dead_worker", "persistence", "contradictory_workers"):
            with self.subTest(fault=fault):
                self.m.render_worker_task = dead if fault == "dead_worker" else original
                self.m.queue_persistence_error = "ENOSPC" if fault == "persistence" else None
                config = await self.m.config()
                if fault == "contradictory_workers":
                    config["state"]["workers"]["render"] = "stopped"
                with patch.object(preflight, "get_json", side_effect=lambda url, *_args: config if url.endswith("/api/config") else {key: {} for key in critical}), \
                     patch.object(preflight, "verify_llama"), patch.object(preflight, "validate_gpu"), \
                     patch.object(preflight, "model_requirements", return_value=[]), \
                     patch.object(preflight.subprocess, "check_output", side_effect=[preflight.COMFY_COMMIT, "1.21.0", "release 13.0", "{}"]), \
                     patch("pathlib.Path.read_text", return_value="{}"), redirect_stdout(io.StringIO()):
                    errors = preflight.run_checks()
                self.assertTrue(errors)
                self.assertTrue(all(error.startswith("panel service:") for error in errors), errors)

    async def test_queue_failure_during_pending_stop_and_destroy_prevents_cli_even_after_repair(self):
        for action in ("stop", "destroy"):
            for repair in (False, True):
                with self.subTest(action=action, repair=repair):
                    self.m.vast_control.update(plan="executing_" + action)
                    self.m.queue_persistence_error = None

                    async def delay(_seconds):
                        with patch.object(self.m, "_atomic_json_write", side_effect=OSError(errno.ENOSPC, "full")):
                            with self.assertRaises(OSError):
                                self.m.save_state()
                        if repair:
                            self.m.save_state()
                            self.assertFalse(self.m.state_diagnostics()["ready"])
                    with patch.object(self.m.asyncio, "sleep", side_effect=delay), \
                         patch.object(self.m, "instance_id_from_env", return_value="inert-instance"), \
                         patch.object(self.m, "run_vast_cli", AsyncMock()) as cli:
                        await self.m.delayed_instance_action(action)
                    cli.assert_not_awaited()
                    self.assertEqual(self.m.vast_control["plan"], "action_failed")
                    with patch.object(self.m.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())), \
                         patch.object(self.m, "_schedule_instance_action") as schedule:
                        with self.assertRaises(asyncio.CancelledError):
                            await self.m.vast_guard_worker()
                    schedule.assert_not_called()

    async def test_scheduled_action_captures_persistence_epoch_before_task_starts(self):
        self.m.vast_control.update(plan="executing_stop")
        with patch.object(self.m, "run_vast_cli", AsyncMock()) as cli, \
             patch.object(self.m, "instance_id_from_env", return_value="inert-instance"):
            self.m._schedule_instance_action("stop", 0)
            self.tasks.append(self.m.lifecycle_action_task)
            self.m._record_queue_persistence_failure(OSError("one-shot EIO"))
            self.m.save_state()
            await self.m.lifecycle_action_task
        cli.assert_not_awaited()
        self.assertEqual(self.m.vast_control["plan"], "action_failed")
