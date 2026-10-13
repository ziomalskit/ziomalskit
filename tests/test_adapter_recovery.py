"""Frozen adapter/render identity participates in the existing recovery fence."""
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests import test_production_controller as production_fixture
from tests.test_temporal_contract import CANONICAL, FINAL
from tests.test_workflows import exact_convert
from tests.test_production_api import production_catalog


class AdapterRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = production_fixture.ProductionControllerTests.setUp
    batch = production_fixture.ProductionControllerTests.batch
    transport = production_fixture.ProductionControllerTests.transport

    async def custom_job(self):
        self.req = self.req.model_copy(update={"render_settings": self.m.RenderSettings(target_mpx=0.8, first_steps=32, second_steps=8)})
        return await self.batch()

    async def test_workflow_identity_and_resolved_settings_frozen_in_all_jobs_and_batch(self):
        job = await self.custom_job()
        snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        identity = self.m.job_workflow_adapter(job).identity()
        for value in [*snapshot["queue"], *snapshot["batches"]]:
            for key, expected in identity.items():
                self.assertEqual(value["context"][key], expected)
            self.assertEqual(value["context"]["render_settings"], self.m.get_adapter("h3_v20", root=self.m.ROOT).resolve_render_settings(
                {"target_mpx": 0.8, "first_steps": 32, "second_steps": 8}))
            self.assertEqual(value["context"]["workflow_render_identity"], job["context"]["workflow_render_identity"])

    async def test_render_settings_and_workflow_identity_survive_restart_exactly(self):
        job = await self.custom_job()
        original = copy.deepcopy(job["context"])
        job.update(status="prompt_running", prompt_prompt_id="12345678-1234-4234-8234-123456789abc")
        self.m.save_state()
        self.m.queue.clear()
        self.m.batches.clear()
        self.m.load_state()
        recovered = self.m.find_job(job["id"])
        self.assertEqual(recovered["status"], "recovery_prompt")
        self.assertEqual(recovered["context"], original)
        self.assertEqual(recovered["prompt_prompt_id"], job["prompt_prompt_id"])
        workflow = json.loads(self.m.patch_workflow(recovered, self.m.production_workflow(recovered)).read_text())
        a = self.m.job_workflow_adapter(recovered)
        self.assertEqual(a.node(workflow, "duration")["widgets_values_named"]["value_1"], 0.8)
        self.assertEqual(a.node(workflow, "first_pass")["widgets_values_named"]["steps"], 32)

    async def test_descriptor_drift_requires_reconciliation_preserves_remote_ids(self):
        job = await self.batch()
        job.update(status="prompt_running", prompt_prompt_id="12345678-1234-4234-8234-123456789abc")
        self.m.save_state()
        path = self.m.ROOT / "workflow_adapters/h3_v20.json"
        data = json.loads(path.read_text())
        data["label"] += " changed identity"
        path.write_text(json.dumps(data))
        self.m.load_state()
        recovered = self.m.find_job(job["id"])
        self.assertEqual(recovered["status"], "reconciliation_required")
        self.assertIn("adapter identity", recovered["reconciliation_required"])
        self.assertEqual(recovered["prompt_prompt_id"], job["prompt_prompt_id"])
        self.assertTrue(self.m.service_recovering("prompt"))
        self.assertIn(job["id"], self.m.quarantined_execution_ids())

    async def test_hook_implementation_drift_requires_reconciliation(self):
        job = await self.batch()
        path = self.m.ROOT / "app/adapters/v20.py"
        path.write_text(path.read_text() + "\n# Simulated implementation release\n")
        self.m.load_state()
        self.assertEqual(self.m.find_job(job["id"])["status"], "reconciliation_required")

    async def test_missing_frozen_identity_never_silently_migrates_a_pr5_job(self):
        job = await self.batch()
        snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        for key in (*self.m.IDENTITY_FIELDS, "render_settings", "workflow_render_identity"):
            changed = copy.deepcopy(snapshot)
            changed["queue"][0]["context"].pop(key)
            self.m.QUEUE_FILE.write_text(json.dumps(changed))
            self.m.load_state()
            with self.subTest(field=key):
                recovered = self.m.find_job(job["id"])
                self.assertEqual(recovered["status"], "reconciliation_required")
                self.assertNotIn(key, recovered["context"])

    async def test_changed_settings_without_matching_functional_identity_are_quarantined(self):
        job = await self.batch()
        snapshot = json.loads(self.m.QUEUE_FILE.read_text())
        snapshot["queue"][0]["context"]["render_settings"]["first_steps"] = 32
        self.m.QUEUE_FILE.write_text(json.dumps(snapshot))
        self.m.load_state()
        self.assertEqual(self.m.find_job(job["id"])["status"], "reconciliation_required")

    async def test_default_profile_changes_do_not_mutate_old_jobs(self):
        job = await self.custom_job()
        original = copy.deepcopy(job["context"])
        self.m.PRODUCTION["default_profile"] = "10eros_full"
        with patch.object(self.m, "workflow_context", side_effect=AssertionError("recovery cannot resolve new-job defaults")):
            self.m.load_state()
            recovered = self.m.find_job(job["id"])
            self.assertEqual(recovered["context"], original)
            self.m.patch_workflow(recovered, self.m.production_workflow(recovered))

    async def test_changed_render_defaults_require_reconciliation_and_do_not_rewrite_old_settings(self):
        job = await self.batch()
        original = copy.deepcopy(job["context"]["render_settings"])
        path = self.m.ROOT / "workflow_adapters/h3_v20.json"
        data = json.loads(path.read_text())
        data["render_controls"]["target_mpx"]["default"] = 1.0
        path.write_text(json.dumps(data))
        self.m.load_state()
        recovered = self.m.find_job(job["id"])
        self.assertEqual(recovered["status"], "reconciliation_required")
        self.assertEqual(recovered["context"]["render_settings"], original)

    async def test_incompatible_completed_jobs_keep_video_catalogue_and_status(self):
        job = await self.batch()
        video = "/api/proxy/render/view?filename=finished.mp4&type=output"
        job.update(status="completed", video_outputs=[video])
        self.m.save_state()
        path = self.m.ROOT / "app/adapters/v20.py"
        path.write_text(path.read_text() + "\n# New release\n")
        self.m.load_state()
        recovered = self.m.find_job(job["id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(self.m.public_job(recovered)["video_outputs"], [video])
        self.assertIn("reconciliation_required", recovered)

    async def test_identity_drift_blocks_composition_before_any_dispatch(self):
        job = await self.batch()
        job["context"]["workflow_adapter_identity"] = "0" * 64
        with patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                self.m.patch_workflow(job, self.m.WORKFLOWS / self.m.PRODUCTION["profiles"]["h3_full"]["render_template"])
            post.assert_not_awaited()

    async def test_capability_schema_matches_both_profile_adapters(self):
        config = await self.m.config()
        for profile in config["profiles"]:
            expected = self.m.get_adapter(profile["workflow_id"], root=self.m.ROOT).render_capabilities()
            self.assertEqual(profile["workflow"], expected)

    async def test_browser_cannot_submit_graph_addresses_or_unknown_workflows(self):
        body = self.req.model_dump(mode="json")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel.test",
                                     auth=("h3", "test-only-password")) as client:
            for extra in ({"workflow_id": "evil"}, {"node_id": 1731}, {"widgets": {}},
                          {"render_settings": {"node_id": 1731}}, {"render_settings": {"widget_path": "1731.value_1"}},
                          {"render_settings": {"on_steps": 5}}, {"render_settings": {"off_steps": 1}}):
                response = await client.post("/api/batches", json={**body, **extra})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.m.queue)

    async def test_http_boundary_rejects_invalid_types_and_ranges_before_publication(self):
        body = self.req.model_dump(mode="json")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel.test",
                                     auth=("h3", "test-only-password")) as client:
            for values in ({"target_mpx": 0.0}, {"first_steps": 101}, {"second_steps": -1},
                           {"first_steps": 24.0}, {"first_steps": 10**400}, {"target_mpx": "0.4"}, {"target_mpx": True}, {"target_mpx": None}):
                response = await client.post("/api/batches", json={**body, "render_settings": values})
                self.assertIn(response.status_code, (400, 422), response.text)
        self.assertFalse(self.m.queue)

    async def test_http_valid_settings_are_persisted_and_retry_body_remains_idempotent(self):
        body = self.req.model_dump(mode="json")
        body["render_settings"] = {"target_mpx": 0.8, "first_steps": 32, "second_steps": 8}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel.test",
                                     auth=("h3", "test-only-password")) as client:
            first = await client.post("/api/batches", json=body)
            self.assertEqual(first.status_code, 200, first.text)
            retry = await client.post("/api/batches", json=body)
            self.assertEqual(retry.json(), first.json())
            changed = await client.post("/api/batches", json={**body, "render_settings": {**body["render_settings"], "first_steps": 33}})
            self.assertEqual(changed.status_code, 409)
        self.assertEqual(len(self.m.queue), 10)
        self.assertEqual(self.m.queue[0]["context"]["render_settings"], self.m.get_adapter("h3_v20", root=self.m.ROOT).resolve_render_settings(body["render_settings"]))

    async def test_http_nonfinite_and_overflow_numbers_fail_closed(self):
        body = self.req.model_dump(mode="json")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel.test",
                                     auth=("h3", "test-only-password")) as client:
            for value in (float("nan"), float("inf"), -float("inf")):
                response = await client.post("/api/batches", content=json.dumps({**body, "render_settings": {"target_mpx": value}}),
                                             headers={"content-type": "application/json"})
                self.assertEqual(response.status_code, 422, response.text)
            payload = json.dumps({**body, "render_settings": {"target_mpx": "overflow-placeholder"}}).replace('"overflow-placeholder"', '1e400')
            response = await client.post("/api/batches", content=payload, headers={"content-type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.m.queue)

    async def prepared_render(self):
        job = await self.custom_job()
        job.update(active_service="render", creative_plan=CANONICAL)
        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=FINAL)
        converted = exact_convert(json.loads(path.read_text()), production_catalog())
        transport, _ = self.transport()
        with transport, patch.object(self.m, "run_cli_envelope", AsyncMock(return_value={"data": {"valid": True, "error_count": 0}})):
            graph = await self.m._prepare_workflow_api(path, "render", preview_envelope={"data": {"status": "preview", "prompt": converted}})
        return job, graph

    async def test_nondefault_controls_reach_final_graph_without_changing_memory_policy(self):
        job, graph = await self.prepared_render()
        adapter = self.m.job_workflow_adapter(job)
        self.assertEqual(graph["1731:1735"]["inputs"]["megapixels"], 0.8)
        self.assertEqual(graph["7334:6771"]["inputs"]["steps"], 32)
        self.assertEqual(graph["7335:360"]["inputs"]["steps"], 8)
        self.assertEqual(job["context"]["profile_identity"]["memory_policy"], self.m.profile_identity("h3_full")["memory_policy"])
        adapter.validate_final_graph(graph, job["context"], phase="render")

    async def test_final_graph_control_drift_fails_closed(self):
        job, graph = await self.prepared_render()
        graph["7334:6771"]["inputs"]["steps"] = 24
        with self.assertRaisesRegex(ValueError, "frozen setting"):
            self.m.job_workflow_adapter(job).validate_final_graph(graph, job["context"], phase="render")

    async def test_final_video_path_or_conditioning_guard_drift_fails_closed(self):
        job, graph = await self.prepared_render()
        adapter = self.m.job_workflow_adapter(job)
        for key, field, value in (("aj_video_complete", "video", ["7331", 0]),
                                  ("7335:29", "conditioning", ["7362", 0]),
                                  ("4595:4529", "after_conditioning", ["aj_conditioning_complete", 0])):
            changed = copy.deepcopy(graph)
            changed[key]["inputs"][field] = value
            with self.subTest(node=key), self.assertRaises(ValueError):
                adapter.validate_final_graph(changed, job["context"], phase="render")

    async def test_canonical_sampler_and_fps_drift_fail_closed(self):
        job, graph = await self.prepared_render()
        adapter = self.m.job_workflow_adapter(job)
        for key, field, value in (("7334:6771", "denoise", 0.5), ("7335:360", "scheduler", "simple"),
                                  ("7334:6782", "sampler_name", "euler"), ("6182", "fps", 30)):
            changed = copy.deepcopy(graph)
            changed[key]["inputs"][field] = value
            with self.subTest(node=key), self.assertRaisesRegex(ValueError, "canonical render parameter"):
                adapter.validate_final_graph(changed, job["context"], phase="render")

    async def test_converted_conditioning_stage_swap_fails_before_runtime_guards(self):
        job = await self.batch()
        job.update(active_service="render", creative_plan=CANONICAL)
        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=FINAL)
        converted = exact_convert(json.loads(path.read_text()), production_catalog())
        one, two = converted["7334:6769"]["inputs"], converted["7335:29"]["inputs"]
        one["conditioning"], two["conditioning"] = two["conditioning"], one["conditioning"]
        with self.assertRaisesRegex(ValueError, "converted graph edge"):
            self.m.job_workflow_adapter(job).prepare_api_prompt(converted, production_catalog(), phase="render", approved_prompt=FINAL)

    async def test_removed_production_metadata_cannot_fallback_to_diagnostics(self):
        job = await self.batch()
        job.update(active_service="render", creative_plan=CANONICAL)
        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=FINAL)
        workflow = json.loads(path.read_text())
        workflow["extra"].pop("aj_production")
        path.write_text(json.dumps(workflow))
        with patch.object(self.m, "run_cli_envelope", AsyncMock(side_effect=AssertionError("no conversion or POST"))):
            with self.assertRaisesRegex(ValueError, "adapter metadata missing"):
                await self.m._prepare_workflow_api(path, "render")

    async def test_analysis_cache_cannot_guess_missing_workflow_identity(self):
        job = await self.batch()
        job["context"].pop("workflow_id")
        with self.assertRaisesRegex(ValueError, "workflow analysis identity missing"):
            self.m.analysis_cache.classify(job, self.m.batch_for_job(job))

    async def test_non_submitting_smoke_materializes_both_adapter_profiles_and_phases(self):
        from h3.scripts.smoke_test import patched_workflows
        for index in range(1, 7):
            (self.m.COMFY_INPUT_DIR / f"__h3_preflight_{index}.png").write_bytes(b"CPU fixture")
        paths = patched_workflows(self.m)
        self.assertEqual(len(paths), 4)
        transport, calls = self.transport()
        with transport, patch.object(self.m, "run_cli_envelope", AsyncMock(return_value={"data": {"valid": True, "error_count": 0}})), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
            for (profile, phase), path in paths.items():
                workflow = json.loads(path.read_text())
                converted = exact_convert(workflow, production_catalog())
                graph = await self.m._prepare_workflow_api(path, phase, preview_envelope={"data": {"status": "preview", "prompt": converted}})
                self.assertEqual(graph["aj_frozen_duration"]["inputs"]["legal_frame_count"], 481)
                self.assertEqual(workflow["extra"]["aj_production"]["profile"], profile)
            post.assert_not_awaited()
        self.assertTrue(all(method == "GET" and path == "/object_info" for method, _, path in calls))

    async def test_recovery_prompt_graph_cannot_bypass_guarded_creative_plan(self):
        job = await self.batch()
        job["active_service"] = "prompt"
        path = self.m.patch_workflow(job, self.m.production_workflow(job))
        adapter = self.m.job_workflow_adapter(job)
        catalog = production_catalog()
        graph = adapter.prepare_api_prompt(exact_convert(json.loads(path.read_text()), catalog), catalog, phase="prompt")
        guarded, _ = adapter.install_runtime_guards(graph, catalog, job["context"], phase="prompt")
        guarded["2551:2640:4275"]["inputs"]["prompt"] = ["2551:2640:2445", 0]
        with self.assertRaisesRegex(ValueError, "guarded creative timeline|bypasses temporal guard"):
            adapter.validate_final_graph(guarded, job["context"], phase="prompt")
