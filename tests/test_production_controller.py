"""Production controller boundaries with synthetic bytes and HTTP transport.

No GPU, provider download, real ComfyUI POST or Vast command is performed.
"""
import copy
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from tests.helpers import load_controller
from tests.test_final_blocker_fixes import request
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert

TEXTS = {field: field + " exact saved analysis" for field in ("visual_facts", "expanded_intent", "reference_map")}


class ProductionControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller(production_probes=True)
        self.m = self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        environment = patch.dict(os.environ, {"H3_ALLOW_DIAGNOSTIC_SUBMISSIONS": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.req = request(self.m)

    async def batch(self, profile="h3_full"):
        model = self.m.model_path_for_preset(profile)
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"CPU fixture; never loaded")
        await self.m.create_batches(self.req.model_copy(update={"profile": profile}))
        return self.m.queue[0]

    def warm(self, job):
        self.m.analysis_cache.observe_service("1" * 32)
        self.m.analysis_cache.record(job, self.m.batch_for_job(job), TEXTS, execution_epoch=self.m.analysis_cache.epoch)

    def render_active(self, profile="h3_full", phase="sampling"):
        job = {"id": "other-render", "profile": profile, "render_phase": phase, "status": "render_running"}
        self.m.queue.append(job)
        self.m.overlap_telemetry.update(free_vram_mb=40000, sampled_at=time.monotonic())
        return job

    def transport(self, callback=None):
        real_client = httpx.AsyncClient
        calls = []
        catalog = production_catalog()
        def handle(req):
            calls.append((req.method, req.url.port, req.url.path))
            if callback:
                value = callback(req)
                if value is not None:
                    return value
            if req.method == "GET" and req.url.path == "/aj/service_epoch":
                return httpx.Response(200, json={"service_epoch": "1" * 32})
            if req.method == "GET" and req.url.path == "/object_info":
                return httpx.Response(200, json=catalog)
            if req.method == "POST" and req.url.path == "/free":
                self.assertEqual(req.url.port, 8188)
                self.assertEqual(json.loads(req.content), {"unload_models": True, "free_memory": True})
                return httpx.Response(200, json={})
            raise AssertionError("unexpected HTTP request; real submission forbidden")
        transport = httpx.MockTransport(handle)
        return patch.object(self.m.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)), calls

    async def test_profile_is_durable_in_every_job_and_batch_before_publication(self):
        job = await self.batch("10eros_full")
        snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        self.assertEqual(len(snapshot["queue"]), 10)
        self.assertTrue(all(value["profile"] == value["context"]["profile"] == "10eros_full" for value in snapshot["queue"]))
        self.assertEqual(snapshot["batches"][0]["profile"], "10eros_full")
        self.assertEqual(snapshot["queue"][0]["context"]["profile_identity"], self.m.profile_identity("10eros_full"))
        self.assertFalse(any(value["enabled"] for value in job["context"]["loras"]))

    async def test_twenty_second_six_section_fixture_survives_capture_and_render_without_truncation(self):
        sections = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
                    "overall_soundscape", "non_diegetic_music")
        timeline = "20 seconds: [0, 5 s], [5, 10 s], [10, 15 s], [15, 20 s]."
        # A complete long-form transport fixture, not an unrun writer benchmark.
        final = "\n\n".join(f"{name}: {timeline if name == 'detailed_description' else 'Reference 1 retained.'} "
                              + "Detailed fixture text. " * 150 for name in sections)
        graph = {"final": {"class_type": "PreviewAny", "_meta": {
            "title": "STEP 4 — Final H3 Prompt / Output"}}}
        for profile in ("h3_full", "10eros_full"):
            with self.subTest(profile=profile):
                self.req = self.req.model_copy(update={"request_id": __import__("uuid").uuid4()})
                first = len(self.m.queue)
                await self.batch(profile)
                job = self.m.queue[first]
                self.assertEqual(job["profile"], profile)
                captured = self.m.extract_prompt_texts({"prompt": graph, "outputs": {"final": {"text": [final]}}})
                self.assertEqual(captured["final_h3_prompt"], final)
                self.assertEqual(job["context"]["duration_seconds"], 20)
                path = self.m.patch_workflow(job, self.m.execution_workflow(job, "render"), approved_prompt=final)
                workflow = json.loads(path.read_text())
                prompt = next(node for node in workflow["nodes"] if node["id"] == 2632)
                self.assertEqual(prompt["widgets_values_named"]["positive"], final)
                self.assertEqual([line.split(":", 1)[0] for line in final.split("\n\n")], list(sections))
                self.assertIn("[15, 20 s]", prompt["widgets_values_named"]["positive"])

    async def test_restart_recovery_keeps_profile_despite_runtime_default_change(self):
        job = await self.batch("10eros_full")
        job["status"] = "render_preparing"
        self.m.save_state()
        self.m.PRODUCTION["default_profile"] = "h3_full"
        self.m.queue.clear()
        self.m.batches.clear()
        self.m.load_state()
        recovered = self.m.find_job(job["id"])
        self.assertEqual(recovered["status"], "recovery_render")
        await self.m._recover_one_job(recovered, "render")
        self.assertEqual(recovered["status"], "render_queued_auto")
        self.assertEqual(self.m.execution_workflow(recovered, "render").name, "10EROS_FULL_RENDER.json")

    async def test_default_profile_change_applies_only_to_new_request_ids(self):
        request_body = self.req.model_copy(update={"profile": None, "model": None})
        original = await self.m.create_batches(request_body)
        self.m.PRODUCTION["default_profile"] = "10eros_full"
        self.assertEqual(await self.m.create_batches(request_body), original)
        self.assertTrue(all(job["profile"] == "h3_full" for job in self.m.queue))
        model = self.m.model_path_for_preset("10eros_full")
        model.write_bytes(b"CPU fixture")
        new = await self.m.create_batches(request_body.model_copy(update={"request_id": __import__("uuid").uuid4()}))
        self.assertNotEqual(original, new)
        self.assertTrue(all(job["profile"] == "10eros_full" for job in self.m.queue[10:]))

    async def test_model_route_drift_is_rejected_before_loading_state(self):
        job = await self.batch()
        self.m.save_state()
        self.m.queue.clear()
        self.m.PRODUCTION["profiles"]["h3_full"]["writer"] = "changed.gguf"
        self.m.load_state()
        self.assertIn("routing changed", self.m.state_load_error)
        self.assertEqual(self.m.queue, [])

    async def prepared_prompt(self,job):
        job["active_service"]="prompt"
        path=self.m.patch_workflow(job,self.m.production_workflow(job))
        converted=exact_convert(json.loads(path.read_text()),production_catalog())
        transport,_=self.transport()
        with transport,patch.object(self.m,"run_cli_envelope",AsyncMock(return_value={"data":{"valid":True,"error_count":0}})):
            return await self.m._prepare_workflow_api(path,"prompt",preview_envelope={"data":{"status":"preview","prompt":converted}})

    def guarded_history(self,graph,plan,final):
        values={"STEP 0 — JoyCaption Visual Facts / Output":TEXTS["visual_facts"],
                "STEP 1 — Expanded Intent / Output":TEXTS["expanded_intent"],
                "STEP 2 — Reference Map / Output":TEXTS["reference_map"],
                "STEP 3 — Creative Director / Output":plan,"STEP 4 — Final H3 Prompt / Output":final}
        outputs={key:{"text":[values[node["_meta"]["title"]]]} for key,node in graph.items()
                 if node.get("_meta",{}).get("title") in values}
        outputs["5732"]={"text":[final]}
        return {"prompt":graph,"outputs":outputs,"status":{"completed":True,"status_str":"success"}}

    async def test_reasoning_and_duration_identity_are_frozen_before_publication(self):
        for profile in ("h3_full","10eros_full"):
            self.req=self.req.model_copy(update={"request_id":__import__("uuid").uuid4()})
            first=len(self.m.queue)
            await self.batch(profile)
            snapshot=json.loads(self.m.QUEUE_FILE.read_text())
            for job in snapshot["queue"][first:]:
                ctx=job["context"]
                self.assertEqual((ctx["requested_duration_seconds"],ctx["effective_duration_seconds"],ctx["legal_frame_count"]),(20,20.04,481))
                self.assertEqual(ctx["profile_identity"]["writer_stages"]["step3"]["reasoning"],"off")
                self.assertEqual(ctx["profile_identity"]["writer_stages"]["step4"]["reasoning"],"on")

    async def test_recovery_uses_persisted_writer_budget_and_duration_after_default_changes(self):
        from tests.test_temporal_contract import CANONICAL,FINAL
        job=await self.batch("10eros_full")
        graph=await self.prepared_prompt(job)
        job["status"]="prompt_running"
        self.m.save_state()
        stages=self.m.PRODUCTION["profiles"]["10eros_full"]["writer_stages"]
        stages["step4"].update(reasoning_budget=5120,max_tokens=13376,ctx_size=30208)
        self.m.PRODUCTION["default_profile"]="h3_full"
        self.m.BatchRequest.model_fields["duration_seconds"].default=30
        self.m.queue.clear();self.m.batches.clear();self.m.load_state()
        recovered=self.m.find_job(job["id"])
        self.assertEqual(recovered["status"],"recovery_prompt")
        after=await self.prepared_prompt(recovered)
        self.assertEqual(after["2551:2640:4275"]["inputs"]["max_tokens"],12352)
        self.assertEqual(after["aj_frozen_duration"]["inputs"],{"legal_frame_count":481,"effective_duration_seconds":20.04})
        self.assertTrue(self.m._apply_history_outcome(recovered,"prompt",self.guarded_history(graph,CANONICAL,FINAL)))
        self.assertEqual(recovered["status"],"render_queued_auto")
        self.assertEqual(recovered["temporal_guard_status"],"verified")

    async def test_old_jobs_without_stage_or_effective_duration_identity_require_reconciliation(self):
        job=await self.batch()
        for field,container in (("writer_stages",job["context"]["profile_identity"]),("effective_duration_seconds",job["context"])):
            saved=container.pop(field)
            with self.assertRaises(ValueError):self.m.job_profile(job)
            container[field]=saved

    async def test_invalid_temporal_result_never_becomes_renderable_or_reaches_post(self):
        from tests.test_temporal_contract import RAW_NINETY,CANONICAL,FINAL
        job=await self.batch();graph=await self.prepared_prompt(job)
        for plan,final in ((RAW_NINETY,FINAL),(CANONICAL,FINAL.replace("00:06.680","01:30.000")),
                           (CANONICAL,FINAL.replace("[Shot 3]","[Shot 4]")),(CANONICAL,"[Start thinking]\nPrivate\n"+FINAL)):
            with patch.object(self.m,"_dispatch_prepared_workflow",AsyncMock()) as post:
                self.assertTrue(self.m._apply_history_outcome(job,"prompt",self.guarded_history(graph,plan,final)))
                self.assertEqual(job["status"],"prompt_failed")
                self.assertEqual(job["temporal_guard_status"],"failed")
                self.assertLess(len(job["error"]),180)
                self.assertIsNone(job["final_h3_prompt"])
                post.assert_not_awaited()

    async def test_review_edit_cannot_bypass_the_canonical_schedule(self):
        from tests.test_temporal_contract import CANONICAL,FINAL
        job=await self.batch();job.update(status="pending_review",creative_plan=CANONICAL,final_h3_prompt=FINAL)
        with self.assertRaises(self.m.HTTPException):
            await self.m.approve(job["id"],self.m.ApprovalRequest(final_prompt=FINAL.replace("00:06.680","01:30.000")))
        self.assertEqual(job["status"],"pending_review")
        result=await self.m.approve(job["id"],self.m.ApprovalRequest(final_prompt=FINAL))
        self.assertEqual(result,{"ok":True,"priority":"top"})
        self.assertEqual(job["approved_final_prompt"],FINAL)

    async def test_review_candidate_is_published_only_after_temporal_validation(self):
        from tests.test_temporal_contract import CANONICAL,FINAL
        await self.batch();job=self.m.queue[5]
        graph=await self.prepared_prompt(job)
        self.assertTrue(self.m._apply_history_outcome(job,"prompt",self.guarded_history(graph,CANONICAL,FINAL)))
        self.assertEqual(job["status"],"pending_review")
        self.assertEqual(job["temporal_guard_status"],"verified")

    async def test_changed_writer_runtime_requires_explicit_reconciliation(self):
        job=await self.batch()
        self.m.PRODUCTION["writer_runtime"]["llm_node_revision"]="a"*40
        with self.assertRaisesRegex(ValueError,"writer runtime changed"):
            self.m.production_workflow(job)

    async def test_same_filename_writer_hash_or_conversion_identity_change_is_fenced(self):
        job=await self.batch()
        manifest=self.m.CONFIG/"models_manifest.json"
        data=json.loads(manifest.read_text())
        writer=next(item for item in data["artifacts"] if item["destination"].endswith("Qwen3.5-4B-Heretic-Q8_0.gguf"))
        writer["sha256"]="a"*64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"model provenance changed"):self.m.production_workflow(job)
        self.req=self.req.model_copy(update={"request_id":__import__("uuid").uuid4()})
        first=len(self.m.queue);await self.batch("10eros_full");eros=self.m.queue[first]
        data["pending_artifacts"][0]["converter_sha256"]="b"*64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"model provenance changed"):self.m.production_workflow(eros)

    async def test_gpu_validation_labels_do_not_change_a_queued_model_identity(self):
        job=await self.batch()
        manifest=self.m.CONFIG/"models_manifest.json"
        data=json.loads(manifest.read_text())
        for item in data["artifacts"]:item["gpu_validation"]="validated"
        manifest.write_text(json.dumps(data))
        self.assertEqual(self.m.job_profile(job),"h3_full")

    async def test_real_render_preparation_rejects_a_temporally_invalid_approved_prompt(self):
        from tests.test_temporal_contract import CANONICAL,FINAL
        job=await self.batch();job.update(active_service="render",creative_plan=CANONICAL)
        wrong=FINAL.replace("00:06.680","01:30.000")
        path=self.m.patch_workflow(job,self.m.production_workflow(job),approved_prompt=wrong)
        converted=exact_convert(json.loads(path.read_text()),production_catalog())
        transport,_=self.transport()
        with transport,patch.object(self.m,"_dispatch_prepared_workflow",AsyncMock()) as post:
            with self.assertRaisesRegex(ValueError,"canonical creative timeline"):
                await self.m._prepare_workflow_api(path,"render",preview_envelope={"data":{"status":"preview","prompt":converted}})
            post.assert_not_awaited()

    async def test_inconsistent_persisted_batch_profile_is_rejected(self):
        await self.batch()
        self.m.batches[0]["profile"] = "10eros_full"
        self.m.save_state()
        self.m.queue.clear()
        self.m.load_state()
        self.assertIn("persisted profile", self.m.state_load_error)
        self.assertEqual(self.m.queue, [])

    async def test_browser_cannot_supply_model_urls_paths_or_unregistered_lora_fields(self):
        for field in ("repository", "model_path", "download_url", "checkpoint"):
            with self.assertRaises(ValueError):
                self.m.BatchRequest.model_validate({**self.req.model_dump(), field: "../untrusted"})
        with self.assertRaises(ValueError):
            self.m.LoraSetting.model_validate({"id": "movement-v1", "enabled": True, "strength": .5, "path": "/tmp/other"})

    async def test_uncertain_post_recovery_retains_10eros_profile_and_never_resubmits(self):
        job = await self.batch("10eros_full")
        transport, _ = self.transport()
        with transport, patch.object(self.m, "verify_production_files", AsyncMock()), \
             patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock(side_effect=httpx.ReadError("lost acknowledgement"))) as post:
            with self.assertRaises(self.m.SubmissionUncertain):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")
            durable = json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]
            self.assertEqual(durable["profile"], "10eros_full")
            self.assertEqual(durable["status"], "recovery_prompt")
            self.m.PRODUCTION["default_profile"] = "h3_full"
            self.m.load_state()
            recovered = self.m.find_job(job["id"])
            with patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)), \
                 patch.object(self.m, "fetch_history", AsyncMock(return_value={})), \
                 patch.object(self.m, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})):
                await self.m._recover_one_job(recovered, "prompt")
            self.assertEqual(recovered["profile"], "10eros_full")
            self.assertEqual(recovered["status"], "recovery_prompt")
            self.assertEqual(recovered["prompt_prompt_id"], durable["prompt_prompt_id"])
            post.assert_awaited_once()

    async def test_unknown_production_profile_and_path_lora_never_create_jobs(self):
        for change in ({"profile": "native_int8"}, {"profile": "../10eros_full"},
                       {"loras": [self.m.LoraSetting(name="unknown", enabled=True, strength=1)]}):
            with self.assertRaises(self.m.HTTPException):
                await self.m.create_batches(self.req.model_copy(update=change))
            self.assertEqual(self.m.queue, [])
        for value in ("../file.safetensors", "/tmp/file", "loras/file.safetensors"):
            with self.assertRaises(ValueError):
                self.m.LoraSetting(name=value, enabled=True, strength=1)

    async def test_queued_lora_and_bridge_defaults_do_not_drift(self):
        job = await self.batch()
        self.m.LORA_REGISTRY["h3-combat-v2"]["defaults"]["h3_full"]["enabled"] = True
        self.m.LORA_REGISTRY["movement-v1"]["defaults"]["h3_full"]["strength"] = 1.9
        self.m.CONDITIONING_BRIDGE["defaults"]["h3_full"] = {"enabled": False, "strength": 1.7}
        path = self.m.patch_workflow(job, self.m.production_workflow(job))
        nodes = {node["id"]: node for node in json.loads(path.read_text())["nodes"]}
        rows = [row for row in nodes[6164]["widgets_values"] if isinstance(row, dict) and row.get("on")]
        self.assertEqual({row["lora"]: row["strength"] for row in rows}, {
            "HMBreastsV2.safetensors": 1, "MysticXXX_MMH3-V4-ref2va.safetensors": .6,
            "movement_h3_lora_v1_500.safetensors": .5})
        self.assertTrue(nodes[7362]["widgets_values_named"]["enabled"])
        self.assertEqual(nodes[7362]["widgets_values_named"]["alpha"], .1)
        self.m.LORA_REGISTRY["movement-v1"]["filename"] = "other.safetensors"
        with self.assertRaisesRegex(ValueError, "LoRA routing changed"):
            self.m.production_workflow(job)

    async def test_h3_cold_analysis_defers_before_any_remote_or_paid_boundary(self):
        job = await self.batch()
        self.render_active()
        transport, calls = self.transport()
        with transport, patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post, \
             patch.object(self.m, "verify_production_files", AsyncMock()) as files:
            with self.assertRaisesRegex(self.m.SubmissionDeferred, "cold Step 0"):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")
        self.assertEqual(calls, [])
        post.assert_not_awaited()
        files.assert_not_awaited()
        self.assertNotIn("prompt_prompt_id", job)

    async def test_warm_writer_admission_keeps_parallel_overlap_and_one_heavy_render(self):
        job = await self.batch()
        self.warm(job)
        active = self.render_active()
        self.assertTrue(self.m.phase_admission(job, "prompt")[0])
        self.assertFalse(self.m.phase_admission(job, "render")[0])
        for phase in ("conditioning", "decoding", "unknown"):
            active["render_phase"] = phase
            self.assertFalse(self.m.phase_admission(job, "prompt")[0])
        active.update(render_phase="sampling", status="recovery_render")
        self.assertFalse(self.m.phase_admission(job, "prompt")[0])

    async def test_10eros_cold_overlap_requires_fresh_measured_headroom(self):
        job = await self.batch("10eros_full")
        self.render_active("10eros_full")
        self.assertTrue(self.m.phase_admission(job, "prompt")[0])
        self.m.overlap_telemetry["sampled_at"] -= 4
        self.assertFalse(self.m.phase_admission(job, "prompt")[0])

    async def test_warm_review_cannot_bypass_phase_deferred_automatic_work(self):
        job = await self.batch()
        self.warm(job)
        for value in self.m.queue[:5]:
            value["status"] = "completed"
        cold = copy.deepcopy(job)
        cold.update(id="cold-next-batch", batch_id="next-batch", batch_seq=2,
                    status="prompt_queued", analysis_seed=job["analysis_seed"] + 1)
        self.m.queue.append(cold)
        self.render_active()
        self.assertFalse(self.m.phase_admission(cold, "prompt")[0])
        self.assertTrue(self.m.phase_admission(self.m.queue[5], "prompt")[0])
        self.assertIsNone(self.m.pick_next_prompt_job())
        cold["status"] = "completed"
        self.assertIs(self.m.pick_next_prompt_job(), self.m.queue[5])

    async def test_service_epoch_endpoint_invalidates_assumed_cache_without_discarding_text(self):
        job = await self.batch()
        self.warm(job)
        old = self.m.analysis_cache.epoch
        transport, _ = self.transport(lambda req: httpx.Response(200, json={"service_epoch": "2" * 32}))
        with transport:
            await self.m.refresh_prompt_epoch()
        self.assertNotEqual(self.m.analysis_cache.epoch, old)
        self.assertFalse(self.m.analysis_cache.resident)
        self.assertEqual(self.m.analysis_cache.classify(job, self.m.batch_for_job(job)), "durable_text")
        self.assertEqual(self.m.batch_for_job(job)["analysis"]["texts"], TEXTS)

    async def test_final_epoch_and_maintenance_races_cannot_arm_submission(self):
        job = await self.batch()
        for race in ("service_epoch", "maintenance"):
            reads = 0
            def epoch(req):
                nonlocal reads
                if req.url.path == "/aj/service_epoch":
                    reads += 1
                    if race == "maintenance" and reads == 2:
                        self.m.service_maintenance_epochs["prompt"] += 1
                    return httpx.Response(200, json={"service_epoch": ("2" if race == "service_epoch" and reads == 2 else "1") * 32})
            transport, calls = self.transport(epoch)
            with self.subTest(race=race), transport, \
                 patch.object(self.m, "verify_production_files", AsyncMock()), \
                 patch.object(self.m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                 patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
                with self.assertRaises(self.m.SubmissionDeferred):
                    await self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")
                post.assert_not_awaited()
                self.assertNotIn("prompt_prompt_id", job)
                self.assertEqual(len(calls), 2)

    async def test_render_reservation_during_prompt_preparation_defers_without_post(self):
        job = await self.batch()
        async def prepare(*args, **kwargs):
            self.render_active()
            return {}
        transport, _ = self.transport()
        with transport, patch.object(self.m, "verify_production_files", AsyncMock()), \
             patch.object(self.m, "_prepare_workflow_api", side_effect=prepare), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
            with self.assertRaises(self.m.SubmissionDeferred):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")
            post.assert_not_awaited()
            self.assertNotIn("prompt_prompt_id", job)

    async def test_real_api_adapter_reuses_text_across_profile_and_worker_epoch(self):
        job = await self.batch("10eros_full")
        self.warm(job)
        self.m.analysis_cache.invalidate()
        job["active_service"] = "prompt"
        path = self.m.patch_workflow(job, self.m.production_workflow(job))
        catalog = production_catalog()
        converted = exact_convert(json.loads(path.read_text()), catalog)
        transport, calls = self.transport()
        verdict = {"data": {"valid": True, "error_count": 0}}
        with transport, patch.object(self.m, "run_cli_envelope", AsyncMock(return_value=verdict)):
            prepared = await self.m._prepare_workflow_api(path, "prompt", preview_envelope={"data": {"status": "preview", "prompt": converted}})
        models = [node["inputs"]["model"] for node in prepared.values() if node["class_type"] in {"LLMTextProcessor", "AJCompilerTextProcessor"}]
        self.assertEqual(models, [self.m.PRODUCTION["profiles"]["10eros_full"]["writer"]] * 2)
        self.assertEqual(job["prompt_kind"], "durable_text")
        self.assertEqual(calls, [("GET", 8189, "/object_info")])

    async def test_unfrozen_manifest_rejects_before_any_submission(self):
        job = await self.batch()
        with patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
            with self.assertRaisesRegex(self.m.SubmissionRejected, "provenance"):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "prompt")
            post.assert_not_awaited()
            self.assertNotIn("prompt_prompt_id", job)

    async def test_config_and_diagnostics_are_read_only_and_report_pending_validation(self):
        self.m.save_state()
        before = self.m.QUEUE_FILE.read_bytes()
        healthy = {"available": False, "status": "WARN", "detail": "CPU fixture probe"}
        diagnostics = __import__(self.m.__package__ + ".diagnostics", fromlist=["service_health"])
        with patch.object(self.m, "save_state", side_effect=AssertionError("read-only")), \
             patch.object(self.m, "run_vast_cli", AsyncMock(side_effect=AssertionError("no Vast"))), \
             patch.object(diagnostics, "service_health", AsyncMock(return_value=healthy)), \
             patch.object(self.m, "persistent_storage_status", Mock(return_value=healthy)), \
             patch.object(self.m, "disk_status", Mock(return_value=healthy)), \
             patch.object(self.m, "gpu_status", Mock(return_value=healthy)):
            config = await self.m.config()
            result = await self.m.api_diagnostics()
        self.assertEqual([(p["id"], p["label"]) for p in config["profiles"]], [("h3_full", "H3 Full"), ("10eros_full", "10Eros Full")])
        self.assertTrue(all("id" in value and "defaults" in value for value in config["loras"]))
        self.assertEqual(result["production"]["gpu_validation"], "pending")
        self.assertEqual(result["models"]["manifest_status"], "blocked_source_provenance")
        self.assertEqual(self.m.QUEUE_FILE.read_bytes(), before)
