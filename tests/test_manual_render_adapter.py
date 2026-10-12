"""Real converter/adapter and render-only provenance gates with synthetic CPU files."""
import copy
import hashlib
import json
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx

from tests.test_manual_prompt import MANUAL
from tests import test_manual_prompt_controller as manual_fixture
from tests import test_production_controller as production_fixture
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert
from tests.test_temporal_contract import CANONICAL, FINAL


class ManualRenderAdapterTests(unittest.IsolatedAsyncioTestCase):
    setUp = production_fixture.ProductionControllerTests.setUp
    manual_request = manual_fixture.ManualPromptControllerTests.manual_request
    job = manual_fixture.ManualPromptControllerTests.job
    transport = production_fixture.ProductionControllerTests.transport

    async def cli(self, service, *args):
        self.assertEqual(service, "render")
        if args[0] == "run":
            path = self.m.Path(args[args.index("--workflow") + 1])
            return {"data": {"status": "preview", "prompt": exact_convert(json.loads(path.read_text()), production_catalog())}}
        self.assertEqual(args[:2], ("workflow", "validate"))
        return {"data": {"valid": True, "error_count": 0}}

    async def graph(self, job):
        job["active_service"] = "render"
        text = self.m.final_prompt_for_render(job)
        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=text)
        http, calls = self.transport()
        with http, patch.object(self.m, "run_cli_envelope", side_effect=self.cli):
            graph = await self.m._prepare_workflow_api(path, "render")
        self.assertEqual(calls, [("GET", 8188, "/object_info")])
        return graph

    def assert_direct_text(self, graph, text=MANUAL):
        self.assertEqual(graph["2632"]["inputs"]["positive"], text)
        self.assertEqual(graph["6546:6354"]["inputs"]["prompt"], ["2632", 0])
        classes = {node["class_type"] for node in graph.values()}
        self.assertFalse(classes & {"LLMTextProcessor", "AJCompilerTextProcessor", "AJAnalysisText", "AJCreativeTimelineGuard", "AJFinalPromptTimeGuard"})
        self.assertEqual(graph["aj_frozen_duration"]["inputs"], {"legal_frame_count": 481, "effective_duration_seconds": 20.04})

    async def test_h3_manual_prepared_graph_has_exact_direct_text_and_no_writers(self):
        job = await self.job()
        graph = await self.graph(job)
        self.assert_direct_text(graph)
        self.assertEqual(graph["4595:4529"]["inputs"]["unet_name"], self.m.PRODUCTION["profiles"]["h3_full"]["checkpoint"])

    async def test_teneros_manual_graph_uses_its_renderer_without_davidau_generation(self):
        job = await self.job(profile="10eros_full")
        graph = await self.graph(job)
        self.assert_direct_text(graph)
        self.assertEqual(graph["4595:4529"]["inputs"]["unet_name"], self.m.PRODUCTION["profiles"]["10eros_full"]["checkpoint"])
        self.assertNotIn("2551:2640:2445", graph)
        self.assertNotIn("2551:2640:4275", graph)
        self.assertNotIn("creative_plan", job)

    async def test_manual_defaults_have_identical_active_api_graph_to_autoprompt_render(self):
        for profile in ("h3_full", "10eros_full"):
            with self.subTest(profile=profile):
                manual = await self.job(profile=profile, request_id=uuid.uuid4(), seed=101, manual_final_prompt=FINAL)
                start = len(self.m.queue)
                await self.m.create_batches(self.req.model_copy(update={"profile": profile, "request_id": uuid.uuid4(), "seed": 101}))
                auto = self.m.queue[start]
                auto.update(status="render_queued_auto", final_h3_prompt=FINAL, creative_plan=CANONICAL)
                self.assertEqual(await self.graph(manual), await self.graph(auto))

    async def test_custom_area_steps_upscaler_second_pass_and_both_lora_stacks_share_adapter(self):
        path = self.m.COMFY_MODELS_DIR / "loras" / self.m.LORA_REGISTRY["hm-breasts-v2"]["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic LoRA; never loaded")
        settings = {"target_mpx": .6, "first_steps": 32, "latent_upscaler_enabled": True, "latent_upscaler_mpx": 1.2,
                    "second_pass_enabled": True, "second_steps": 9,
                    "second_pass_loras": [{"id": "hm-breasts-v2", "enabled": True, "strength": .75}]}
        job = await self.job(render_settings=settings, loras=[{"id": "hm-breasts-v2", "enabled": True, "strength": .5}])
        graph = await self.graph(job)
        self.assert_direct_text(graph)
        self.assertEqual(graph["1731:1735"]["inputs"]["megapixels"], .6)
        self.assertEqual(graph["7334:6771"]["inputs"]["steps"], 32)
        self.assertEqual(graph["6175"]["inputs"]["mode.megapixels"], 1.2)
        self.assertEqual(graph["7335:360"]["inputs"]["steps"], 9)
        self.assertEqual(graph["6164"]["inputs"]["lora_1"]["strength"], .5)
        self.assertEqual(graph["6165"]["inputs"]["lora_1"]["strength"], .75)
        self.assertEqual(graph["7334:6769"]["inputs"]["model"], ["6164", 0])
        self.assertEqual(graph["7335:6179"]["inputs"]["anything"], ["6165", 0])
        self.assertTrue(job["context"]["workflow_render_identity"])

    async def test_all_four_latent_routes_work_in_manual_mode_without_implicit_toggles(self):
        for upscaler, second in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(upscaler=upscaler, second=second):
                settings = {"latent_upscaler_enabled": upscaler, "second_pass_enabled": second, "second_steps": 7}
                job = await self.job(request_id=uuid.uuid4(), render_settings=settings)
                graph = await self.graph(job)
                incoming = ["6178", 0] if upscaler else ["7334:6773", 0]
                self.assertEqual(graph["7335:6173"]["inputs"]["on_false"], incoming)
                self.assertEqual(graph["7335:469"]["inputs"]["latent_image"], incoming)
                self.assertEqual(graph["7335:6173"]["inputs"]["on_true"], ["7335:469", 0])
                self.assertEqual(graph["7335:6266"]["inputs"], {"anything": ["6263", 0]} if second else {})
                self.assertEqual("6175" in graph, upscaler)
                self.assert_direct_text(graph)

    async def test_second_lora_only_affects_second_model_with_or_without_upscaler(self):
        for upscaler in (False, True):
            with self.subTest(upscaler=upscaler):
                base = {"latent_upscaler_enabled": upscaler, "second_pass_enabled": True}
                plain = await self.graph(await self.job(request_id=uuid.uuid4(), render_settings=base))
                chosen = await self.graph(await self.job(request_id=uuid.uuid4(), render_settings={**base,
                    "second_pass_loras": [{"id": "hm-breasts-v2", "enabled": True, "strength": .75}]}))
                self.assertEqual(chosen["6164"], plain["6164"])
                self.assertEqual(chosen["7334:6769"], plain["7334:6769"])
                self.assertEqual(chosen["7335:6179"]["inputs"]["anything"], ["6165", 0])

    async def test_disabled_second_lora_and_upscale_values_do_not_activate_routes(self):
        job = await self.job(render_settings={"latent_upscaler_mpx": 1.6, "second_steps": 8,
            "second_pass_loras": [{"id": "hm-breasts-v2", "enabled": True, "strength": .75}]})
        graph = await self.graph(job)
        self.assertNotIn("6175", graph)
        self.assertNotIn("6165", graph)
        self.assertEqual(graph["7335:6266"]["inputs"], {})
        self.assertEqual(graph["7335:6173"]["inputs"]["on_false"], ["7334:6773", 0])

    async def test_real_http_submission_is_only_to_render_8188_for_each_profile(self):
        for profile in ("h3_full", "10eros_full"):
            job = await self.job(profile=profile, request_id=uuid.uuid4())
            job["active_service"] = "render"
            path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=MANUAL)
            submitted = []
            def accepted(req):
                if req.method == "POST" and req.url.path == "/prompt":
                    body = json.loads(req.content)
                    submitted.append(body)
                    self.assert_direct_text(body["prompt"])
                    return httpx.Response(200, json={"prompt_id": body["prompt_id"]})
            http, calls = self.transport(accepted)
            with http, patch.object(self.m, "verify_production_files", AsyncMock()), \
                    patch.object(self.m, "run_cli_envelope", side_effect=self.cli), \
                    patch.object(self.m.analysis_cache, "classify", side_effect=AssertionError("analysis classified")), \
                    patch.object(self.m, "refresh_prompt_epoch", AsyncMock(side_effect=AssertionError("Prompt Worker contacted"))):
                pid = await self.m._submit_workflow(job, path, "render")
            self.assertEqual(len(submitted), 1)
            self.assertEqual(pid, job["render_prompt_id"])
            self.assertTrue(all(port == 8188 for _, port, _ in calls))
            self.assertEqual(sum(method == "POST" and route == "/prompt" for method, _, route in calls), 1)
            job["status"] = "completed"  # Release this synthetic renderer reservation before the next profile.

    async def test_adapter_rejects_manual_prompt_phase_and_override_drift(self):
        job = await self.job()
        adapter = self.m.job_workflow_adapter(job)
        for phase, text in (("prompt", None), ("render", MANUAL.strip())):
            with self.assertRaises(ValueError):
                adapter.compose_job(self.m.production_workflow(job), self.m.PRODUCTION, self.m.LORA_REGISTRY,
                                    job, ["__h3_silence_1s.wav"] * 3, text, phase)
        graph = await self.graph(job)
        graph["2632"]["inputs"]["positive"] = MANUAL.strip()
        with self.assertRaises(ValueError):
            adapter.validate_final_graph(graph, job["context"], phase="render")

    async def test_extra_writer_node_cannot_enter_validated_manual_graph(self):
        job = await self.job()
        graph = await self.graph(job)
        graph["injected_writer"] = {"class_type": "LLMTextProcessor", "inputs": {}}
        with self.assertRaisesRegex(ValueError, "prompt generation"):
            self.m.job_workflow_adapter(job).validate_final_graph(graph, job["context"], phase="render")

    async def test_plain_text_template_shell_and_html_content_reaches_api_without_interpretation(self):
        text = "  <Picture 1> literal {$x|other} $(never_execute) <script>alert(1)</script> żółć 🎬\n"
        graph = await self.graph(await self.job(manual_final_prompt=text))
        self.assert_direct_text(graph, text)

    async def test_invalid_recovered_manual_prompt_causes_zero_comfyui_posts(self):
        job = await self.job()
        job["context"]["manual_final_prompt"] = "The clock reads 01:30.000."
        with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")):
            with self.assertRaises(ValueError):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "render")


class ManualRenderProvenanceTests(unittest.IsolatedAsyncioTestCase):
    setUp = production_fixture.ProductionControllerTests.setUp
    manual_request = manual_fixture.ManualPromptControllerTests.manual_request
    job = manual_fixture.ManualPromptControllerTests.job

    def fixture_manifest(self):
        """Write tiny render files + authentic checksum receipts only in temp fixtures."""
        from h3.app.artifacts import verify_file
        path = self.m.CONFIG / "models_manifest.json"
        manifest = json.loads(path.read_text())
        for item in manifest["artifacts"]:
            if item["destination"].startswith(("diffusion_models/", "text_encoders/", "vae/", "latent_upscale_models/", "semantic_bridge/")):
                body = b"Synthetic CPU render artifact " + item["id"].encode()
                dest = self.m.COMFY_MODELS_DIR / item["destination"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                item.update(size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                if item["id"] == self.m.CONDITIONING_BRIDGE["id"]:
                    bridge = self.m.registry_json(self.m.CONDITIONING_BRIDGE)
                    bridge.update(size_bytes=item["size_bytes"], sha256=item["sha256"])
                    self.m.CONDITIONING_BRIDGE = self.m.immutable_registry(bridge)
                receipt = {**{key: item[key] for key in ("repository", "revision", "repository_path", "destination")},
                           **verify_file(self.m.COMFY_MODELS_DIR, item["destination"], item)}
                record = self.m.COMFY_MODELS_DIR / ".verification" / (item["id"] + ".json")
                record.parent.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps(receipt))
        path.write_text(json.dumps(manifest))
        return manifest

    async def test_h3_manual_verifies_real_render_receipts_without_any_llm_files(self):
        self.fixture_manifest()
        job = await self.job(profile="h3_full", loras=[{"id": key, "enabled": False, "strength": entry["defaults"]["h3_full"]["strength"]}
                                                     for key, entry in self.m.LORA_REGISTRY.items()])
        await self.m.verify_production_files(job, "render")
        self.assertFalse((self.m.COMFY_MODELS_DIR / "LLM").exists())

    async def test_teneros_manual_verifies_renderer_without_davidau_provenance_being_resolved(self):
        manifest = self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        await self.m.verify_production_files(job, "render")
        self.assertEqual(manifest["status"], "blocked_source_provenance")
        self.assertEqual(manifest["pending_artifacts"][0]["id"], "10eros-writer")
        self.assertFalse((self.m.COMFY_MODELS_DIR / "LLM").exists())

    async def test_autoprompt_keeps_complete_manifest_blocker_and_does_not_inherit_exception(self):
        self.fixture_manifest()
        await self.m.create_batches(self.req)
        for service in ("prompt", "render"):
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(self.m.queue[0], service)

    async def test_missing_wrong_size_or_unverified_render_file_still_fails_closed(self):
        manifest = self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        encoder = next(item for item in manifest["artifacts"] if item["id"] == "h3-encoder")
        path = self.m.COMFY_MODELS_DIR / encoder["destination"]
        original = path.read_bytes()
        for change in (lambda: path.unlink(), lambda: path.write_bytes(b"wrong"), lambda: path.write_bytes(original)):
            change()
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, "render")

    async def test_active_upscaler_requires_verified_artifact_but_off_does_not(self):
        manifest = self.fixture_manifest()
        target = next(item for item in manifest["artifacts"] if item["id"] == "latent-upscaler")
        (self.m.COMFY_MODELS_DIR / target["destination"]).unlink()
        off = await self.job(profile="10eros_full")
        await self.m.verify_production_files(off, "render")
        on = await self.job(profile="10eros_full", request_id=uuid.uuid4(), render_settings={"latent_upscaler_enabled": True})
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(on, "render")

    async def test_pending_render_artifact_or_wrong_ownership_is_not_exempt(self):
        manifest = self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        adapter = self.m.job_workflow_adapter(job)
        changed = copy.deepcopy(manifest)
        encoder = next(item for item in changed["artifacts"] if item["id"] == "h3-encoder")
        changed["pending_artifacts"].append(copy.deepcopy(encoder))
        with self.assertRaisesRegex(ValueError, "pending provenance"):
            adapter.required_render_artifacts(job["context"], changed)
        changed["pending_artifacts"].pop()
        encoder["profiles"] = ["h3_full"]
        with self.assertRaisesRegex(ValueError, "ownership"):
            adapter.required_render_artifacts(job["context"], changed)

    async def test_selected_manual_loras_still_require_enrollment_and_integrity(self):
        self.fixture_manifest()
        job = await self.job(profile="10eros_full", render_settings={"second_pass_enabled": True,
            "second_pass_loras": [{"id": "movement-v1", "enabled": True, "strength": .75}]})
        with self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, "render")
