"""Direct manual jobs use existing durable render dispatch, never Prompt ComfyUI."""
import asyncio
import copy
import hashlib
import json
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from tests.helpers import load_controller
from tests.test_final_blocker_fixes import request, remote, run_workers, prior_review
from tests.test_controller import history
from tests.test_manual_prompt import MANUAL


class ManualPromptControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        self.req = request(self.m)

    def manual_request(self, **values):
        body = self.req.model_dump(exclude={"prompt", "manual_final_prompt"})
        body.update(prompt_mode="manual_final", manual_final_prompt=MANUAL)
        body.update(values)
        profile = body.get("profile")
        path = self.m.model_path_for_preset(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"CPU fixture; never loaded")
        return self.m.BatchRequest(**body)

    async def job(self, **values):
        start = len(self.m.queue)
        await self.m.create_batches(self.manual_request(**values))
        return self.m.queue[start]

    def forbid_generation(self, stack):
        generation = stack.enter_context(patch.object(self.m, "generate_prompt_candidate", AsyncMock(side_effect=AssertionError("Prompt Worker used"))))
        classifier = stack.enter_context(patch.object(self.m.analysis_cache, "classify", side_effect=AssertionError("analysis classified")))
        recorder = stack.enter_context(patch.object(self.m.analysis_cache, "record", side_effect=AssertionError("analysis cached")))
        epoch = stack.enter_context(patch.object(self.m, "refresh_prompt_epoch", AsyncMock(side_effect=AssertionError("Prompt Worker contacted"))))
        return generation, classifier, recorder, epoch

    async def test_autoprompt_default_still_creates_ten_with_five_auto_five_review(self):
        self.assertEqual(self.req.prompt_mode, "autoprompt")
        await self.m.create_batches(self.req)
        self.assertEqual(len(self.m.queue), 10)
        self.assertEqual([j["candidate_index"] for j in self.m.queue], list(range(1, 11)))
        self.assertEqual([j["review_required"] for j in self.m.queue], [False] * 5 + [True] * 5)
        self.assertTrue(all(j["status"] == "prompt_queued" and "prompt_seed" in j and "analysis_seed" in j for j in self.m.queue))

    async def test_autoprompt_request_hash_retains_pre_mode_idempotency_format(self):
        await self.m.create_batches(self.req)
        original = self.req.model_dump(mode="json", exclude={"request_id", "prompt_mode", "manual_final_prompt"})
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(self.m.request_results[str(self.req.request_id)]["request_hash"], digest)

    async def test_manual_is_one_ready_render_with_no_generated_or_review_data(self):
        job = await self.job()
        self.assertEqual(len(self.m.queue), 1)
        self.assertEqual(job["status"], "render_queued_auto")
        self.assertFalse(job["review_required"])
        self.assertEqual(job["final_h3_prompt"], MANUAL)
        for key in ("creative_plan", "visual_facts", "expanded_intent", "reference_map", "prompt_seed", "analysis_seed", "prompt_kind", "approved_final_prompt"):
            self.assertNotIn(key, job)
        batch = self.m.batches[0]
        self.assertEqual((batch["prompts_per_batch"], batch["auto_approved"], batch["review_required"]), (1, 1, 0))

    async def test_three_batches_make_exactly_three_frozen_renders_with_normal_seeds(self):
        await self.job(batches=3, seed=101)
        self.assertEqual(len(self.m.queue), 3)
        self.assertEqual(len(self.m.batches), 3)
        self.assertEqual([j["render_seed"] for j in self.m.queue], [101, 102, 103])
        self.assertTrue(all(j["context"]["manual_final_prompt"] == MANUAL and j["candidate_index"] == 1 for j in self.m.queue))

    async def test_nonrandom_seed_is_preserved_across_manual_batches(self):
        await self.job(batches=3, seed=101, random_each=False)
        self.assertEqual([j["render_seed"] for j in self.m.queue], [101] * 3)

    async def test_manual_jobs_do_not_consume_prompt_prefetch_or_use_cache(self):
        with ExitStack() as stack:
            probes = self.forbid_generation(stack)
            await self.job(batches=5)
            self.assertEqual(self.m.prompt_prefetch_status(), {"target": 3, "ready": 0, "preparing": 0, "buffer": 0})
            self.assertIsNone(self.m.pick_next_prompt_job())
            self.assertIsNotNone(self.m.pick_next_render_job())
        for probe in probes:
            probe.assert_not_called()

    async def test_mixed_autoprompt_work_can_still_prefetch_alongside_queued_manual_jobs(self):
        await self.job(batches=5)
        await self.m.create_batches(self.req.model_copy(update={"request_id": uuid.uuid4()}))
        self.assertEqual(self.m.pick_next_prompt_job()["prompt_mode"], "autoprompt")
        self.assertEqual(self.m.prompt_prefetch_status()["buffer"], 0)

    async def test_complete_worker_flow_has_zero_prompt_submissions_for_three_manual_renders(self):
        with ExitStack() as stack:
            probes = self.forbid_generation(stack)
            await self.job(batches=3)
            calls = []
            await run_workers(self.m, 3, calls)
        self.assertEqual([service for service, _ in calls], ["render"] * 3)
        for probe in probes:
            probe.assert_not_called()
        self.assertTrue(all(j["status"] == "completed" for j in self.m.queue))

    async def test_every_prompt_entrypoint_refuses_manual_before_http_or_cache(self):
        job = await self.job()
        with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")), ExitStack() as stack:
            probes = self.forbid_generation(stack)
            for entrypoint in (lambda: self.m.submit_and_watch(job, self.m.production_workflow(job), "prompt"),
                               lambda: self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")):
                with self.assertRaisesRegex(self.m.SubmissionRejected, "never use Prompt Worker"):
                    await entrypoint()
            self.assertFalse(self.m.phase_admission(job, "prompt")[0])
        for probe in probes:
            probe.assert_not_called()
        self.assertEqual(job["status"], "render_queued_auto")

    async def test_real_generation_function_refuses_manual_without_step_calls(self):
        job = await self.job()
        with patch.object(self.m, "submit_and_watch", AsyncMock(side_effect=AssertionError("generation dispatched"))) as submit:
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.generate_prompt_candidate(job)
            submit.assert_not_called()

    async def test_invalid_manual_prompts_fail_locally_before_any_worker_http(self):
        with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")):
            for text in ("[Shot 2] At 00:05.000, cut.", "The clock reads 01:30.000.", MANUAL + "\0"):
                with self.subTest(text=text), self.assertRaises(self.m.HTTPException) as error:
                    await self.m.create_batches(self.manual_request(manual_final_prompt=text))
                self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_empty_unknown_conflicting_and_nontext_requests_are_rejected(self):
        for change in ({"manual_final_prompt": ""}, {"manual_final_prompt": " \n\t"}, {"manual_final_prompt": None},
                       {"manual_final_prompt": 42}, {"manual_final_prompt": True}, {"prompt_mode": "manual"},
                       {"prompt_mode": 0}, {"prompt_mode": True}, {"prompt": "conflicting scene"}, {"prompt": ""}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.manual_request(**change)
        with self.assertRaises(ValidationError):
            self.m.BatchRequest(prompt="scene", manual_final_prompt=MANUAL)
        with self.assertRaises(ValidationError):
            self.m.BatchRequest(prompt_mode="manual_final")

    async def test_http_extra_fields_and_graph_paths_are_forbidden(self):
        req = self.manual_request()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://test",
                                    auth=("h3", "test-only-password")) as client:
            for field, value in (("node_id", 2632), ("widget_name", "positive"), ("graph_path", "2632.positive"),
                                 ("manual_workflow", {}), ("manual_prompt_sha256", "0" * 64)):
                response = await client.post("/api/batches", json={**req.model_dump(mode="json"), field: value})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.m.queue, [])

    async def test_manual_http_render_settings_cannot_bypass_the_trusted_schema(self):
        req = self.manual_request()
        invalid = ({"node_id": 6175}, {"widget_name": "mode.megapixels"}, {"graph_path": "6175.mode"},
                   {"first_steps": 1.5}, {"second_steps": True}, {"first_steps": "24"},
                   {"second_pass_enabled": 1}, {"latent_upscaler_enabled": "false"},
                   {"target_mpx": float("nan")}, {"latent_upscaler_mpx": float("inf")},
                   {"second_pass_loras": [{"id": "../../local.safetensors", "strength": 1}]},
                   {"second_pass_loras": [{"id": "unknown-lora", "strength": 1}]},
                   {"second_pass_loras": [{"id": "hm-breasts-v2", "strength": True}]},
                   {"second_pass_loras": [{"id": "hm-breasts-v2", "strength": 3}]},
                   {"latent_upscaler_enabled": True, "target_mpx": 1.5, "latent_upscaler_mpx": 1.2})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://test",
                                    auth=("h3", "test-only-password")) as client:
            for settings in invalid:
                body = {**req.model_dump(mode="json"), "render_settings": settings}
                response = await client.post("/api/batches", content=json.dumps(body), headers={"content-type": "application/json"})
                self.assertIn(response.status_code, (400, 422), response.text)
        self.assertEqual(self.m.queue, [])

    async def test_manual_mode_retains_six_reference_audio_and_filename_safety(self):
        for assets in ({"pictures": []}, {"pictures": self.req.pictures[:5]}, {"pictures": self.req.pictures + ["extra.png"]},
                       {"audio": ["one.wav", "two.wav"]}, {"pictures": ["../escape.png"] + self.req.pictures[1:]},
                       {"loras": [{"id": "../../local.safetensors", "enabled": False, "strength": 1}]}):
            with self.subTest(assets=assets), self.assertRaises((self.m.HTTPException, ValidationError)):
                await self.m.create_batches(self.manual_request(**assets))
        self.assertEqual(self.m.queue, [])

    async def test_exact_manual_text_and_hash_are_durable_before_publication(self):
        job = await self.job()
        saved = json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]
        self.assertEqual(saved, job)
        self.assertEqual(saved["context"]["manual_final_prompt"], MANUAL)
        self.assertEqual(saved["context"]["manual_final_prompt_sha256"], hashlib.sha256(MANUAL.encode()).hexdigest())
        self.assertTrue(saved["context"]["workflow_prompt_identity"])
        self.assertEqual(saved["context"]["legal_frame_count"], 481)
        self.assertEqual(saved["context"]["effective_duration_seconds"], 20.04)

    async def test_request_object_edits_cannot_mutate_published_manual_prompt(self):
        req = self.manual_request()
        await self.m.create_batches(req)
        req.manual_final_prompt = "Edited after submission"
        self.assertEqual(self.m.queue[0]["context"]["manual_final_prompt"], MANUAL)
        self.assertEqual(self.m.queue[0]["final_h3_prompt"], MANUAL)

    async def test_retry_is_exact_and_changed_manual_text_conflicts_even_after_restart(self):
        req = self.manual_request()
        original = await self.m.create_batches(req)
        self.m.queue, self.m.batches, self.m.request_results = [], [], {}
        self.m.load_state()
        self.assertEqual(await self.m.create_batches(req), original)
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.create_batches(req.model_copy(update={"manual_final_prompt": MANUAL + "Changed."}))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.m.queue), 1)

    async def test_queued_restart_restores_exact_prompt_and_settings_without_new_defaults(self):
        settings = {"target_mpx": .8, "first_steps": 32, "latent_upscaler_enabled": True, "latent_upscaler_mpx": 1.2,
                    "second_pass_enabled": True, "second_steps": 9,
                    "second_pass_loras": [{"id": "hm-breasts-v2", "enabled": True, "strength": .75}]}
        job = await self.job(render_settings=settings)
        context = copy.deepcopy(job["context"])
        self.m.queue, self.m.batches, self.m.request_results = [], [], {}
        self.m.load_state()
        self.assertIsNone(self.m.state_load_error)
        self.assertEqual(self.m.queue[0]["context"], context)
        self.assertEqual(self.m.queue[0]["status"], "render_queued_auto")
        self.assertEqual(self.m.job_profile(self.m.queue[0]), "h3_full")

    async def test_manual_prompt_drift_requires_reconciliation_on_recovery(self):
        job = await self.job()
        job["context"]["manual_final_prompt"] += "Edited."
        self.m.save_state()
        self.m.load_state()
        self.assertEqual(self.m.queue[0]["status"], "reconciliation_required")
        self.assertIsNone(self.m.pick_next_render_job())

    async def test_adapter_identity_drift_still_fences_manual_jobs(self):
        await self.job()
        path = self.m.ROOT / "app/prompt_modes.py"
        path.write_text(path.read_text() + "\n# disposable implementation drift\n")
        self.m.load_state()
        self.assertEqual(self.m.queue[0]["status"], "reconciliation_required")

    async def test_preview_and_public_job_explicitly_identify_manual_source_without_fake_plan(self):
        job = await self.job()
        public = self.m.public_job(job)
        preview = await self.m.prompts(job["id"])
        self.assertEqual(public["prompt_mode"], "manual_final")
        self.assertEqual(public["manual_final_prompt"], MANUAL)
        self.assertEqual(preview["manual_final_prompt"], MANUAL)
        self.assertEqual(preview["final_h3_prompt"], MANUAL)
        self.assertFalse(preview["review_required"])
        for key in ("visual_facts", "expanded_intent", "reference_map", "creative_plan", "prompt_seed", "analysis_seed"):
            self.assertIsNone(preview[key])

    async def test_manual_job_cannot_require_review_or_be_approved_again(self):
        job = await self.job()
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.approve(job["id"], self.m.ApprovalRequest(final_prompt=MANUAL))
        self.assertEqual(error.exception.status_code, 409)
        job["status"] = "pending_review"
        with self.assertRaises(ValueError):
            self.m.job_profile(job)

    async def test_approved_review_retains_highest_priority_over_manual(self):
        manual = await self.job()
        review = prior_review(self.m, self.req)
        await self.m.approve(review["id"], self.m.ApprovalRequest())
        self.assertIs(self.m.pick_next_render_job(), review)
        review["status"] = "completed"
        self.assertIs(self.m.pick_next_render_job(), manual)

    async def test_stop_after_current_blocks_manual_dispatch(self):
        await self.job()
        self.m.vast_control["plan"] = "stop_after_current"
        self.assertIsNone(self.m.pick_next_render_job())
        self.assertIsNone(self.m.pick_next_prompt_job())
        self.assertFalse(self.m.queue_finished_for_shutdown())

    async def test_queue_drain_and_destroy_wait_for_manual_then_finish_normally(self):
        job = await self.job()
        for plan in ("stop_after_queue", "destroy_after_queue_keep_data"):
            self.m.vast_control["plan"] = plan
            self.assertIs(self.m.pick_next_render_job(), job)
            self.assertFalse(self.m.queue_finished_for_shutdown())
            self.assertEqual(self.m.skip_unstarted_review_prompts_for_shutdown(), 0)
            job["status"] = "completed"
            self.assertTrue(self.m.queue_finished_for_shutdown())
            job["status"] = "render_queued_auto"

    async def test_publication_failure_has_no_jobs_or_worker_dispatch(self):
        with patch.object(self.m, "_persist_queue_snapshot", side_effect=OSError("durability fault")):
            with self.assertRaises(OSError):
                await self.job()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})

    async def test_pre_dispatch_persistence_failure_keeps_manual_retryable_auto_priority(self):
        job = await self.job()
        with patch.object(self.m, "save_state", side_effect=OSError("fsync failed")), \
                patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
            with self.assertRaises(OSError):
                await self.m.render_job(job)
            self.m._handle_worker_error(job, "render", OSError("fsync failed"))
            dispatch.assert_not_called()
        self.assertEqual(job["status"], "render_queued_auto")
        self.assertEqual(job["context"]["manual_final_prompt"], MANUAL)

    async def test_active_manual_restart_uses_normal_render_recovery_and_history(self):
        job = await self.job()
        job.update(status="render_running", active_service="render", render_prompt_id="accepted-manual",
                   render_submission_state="accepted", render_submission_attempted=True)
        self.m.save_state()
        self.m.load_state()
        recovered = self.m.queue[0]
        self.assertEqual(recovered["status"], "recovery_render")
        with patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)), \
                patch.object(self.m, "fetch_history", AsyncMock(return_value=history(video=True))), \
                patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
            await self.m._recover_one_job(recovered, "render")
            dispatch.assert_not_called()
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["context"]["manual_final_prompt"], MANUAL)

    async def test_uncertain_submission_is_never_automatically_duplicated(self):
        job = await self.job()
        calls = []
        with remote(self.m, calls), patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock(side_effect=TimeoutError("lost acknowledgement"))) as dispatch:
            await self.m.render_job(job)
            self.assertEqual(dispatch.await_count, 1)
        self.assertIn(job["status"], {"recovery_render", "render_submission_uncertain"})
        self.assertEqual(job["render_submission_state"], "uncertain")
        self.m.save_state()
        self.m.load_state()
        recovered = self.m.queue[0]
        with patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)), \
                patch.object(self.m, "fetch_history", AsyncMock(return_value={})), \
                patch.object(self.m, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})), \
                patch.object(self.m, "fetch_durable_result", AsyncMock(return_value={"protocol": "aj-terminal-v1", "prompt_id": recovered["render_prompt_id"], "state": "unknown"})), \
                patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
            await self.m._recover_one_job(recovered, "render")
            dispatch.assert_not_called()
        self.assertEqual(recovered["status"], "recovery_render")

    async def test_changed_render_prompt_mirror_fails_before_comfyui_post(self):
        job = await self.job()
        job["final_h3_prompt"] = "Changed mirror"
        with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")):
            with self.assertRaises(ValueError):
                await self.m.render_job(job)

    async def test_config_exposes_both_modes_with_autoprompt_default(self):
        config = await self.m.config()
        self.assertEqual(config["default_prompt_mode"], "autoprompt")
        self.assertEqual([m["id"] for m in config["prompt_modes"]], ["autoprompt", "manual_final"])
        self.assertIn("one render", config["prompt_modes"][1]["batches_help"])
