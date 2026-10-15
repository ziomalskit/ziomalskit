"""Independent adversarial F1–F8 regressions; no real worker or provider I/O.

The initial 20 tests preceded production fixes and reproduced F1–F8 against
reviewed 44e47699. The 32-test suite also covers nearby attacks and delta review
findings. The BASE fixture came from c6f033ab's actual controller implementation.
"""
import ast
import copy
import hashlib
import importlib
import inspect
from itertools import product
import json
from pathlib import Path
import subprocess
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import ROOT, load_controller
from tests import test_manual_prompt_controller as manual_fixture
from tests import test_manual_render_adapter as render_fixture
from tests import test_production_controller as production_fixture
from h3.scripts.install_owned_nodes import install
from h3.scripts.enroll_external_loras import enroll

REVIEW_BAD_TIMING = (
    "At −00:10.000, change the camera angle.",
    "Range 00:20.000–00:01.000.",
    "Range 00:00.000-00:10.000-00:05.000.",
    "The clock reads 00:06.680.123.",
    "At ００:９０.０００, change the camera angle.",
)


class RemediationControllerTests(unittest.IsolatedAsyncioTestCase):
    manual_request = manual_fixture.ManualPromptControllerTests.manual_request
    job = manual_fixture.ManualPromptControllerTests.job
    graph = render_fixture.ManualRenderAdapterTests.graph
    cli = render_fixture.ManualRenderAdapterTests.cli
    transport = production_fixture.ProductionControllerTests.transport
    fixture_manifest = render_fixture.ManualRenderProvenanceTests.fixture_manifest

    def setUp(self):
        production_fixture.ProductionControllerTests.setUp(self)
        install(self.m.ROOT / "custom_nodes/aj_production", self.m.COMFY_ROOT / "custom_nodes")

    async def selected_lora_job(self):
        self.fixture_manifest()
        name = self.m.LORA_REGISTRY["movement-v1"]["filename"]
        path = self.m.COMFY_MODELS_DIR / "loras" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Enrolled synthetic CPU LoRA; never loaded")
        enroll(self.m.COMFY_MODELS_DIR, self.m.CONFIG / "lora_registry.json", ["movement-v1"])
        return await self.job(profile="10eros_full", render_settings={
            "second_pass_enabled": True,
            "second_pass_loras": [{"id": "movement-v1", "enabled": True, "strength": .25}],
        })

    def change_registry_filename(self):
        path = self.m.CONFIG / "lora_registry.json"
        data = json.loads(path.read_text())
        row = next(item for item in data["loras"] if item["id"] == "movement-v1")
        row["filename"] = "changed_unverified.safetensors"
        path.write_text(json.dumps(data))
        (self.m.COMFY_MODELS_DIR / "loras" / row["filename"]).write_bytes(b"Wrong unenrolled bytes")
        return row["filename"]

    async def test_f1_live_existing_filename_drift_never_loads_unverified_file(self):
        job = await self.selected_lora_job()
        original = self.m.LORA_REGISTRY["movement-v1"]["filename"]
        frozen = copy.deepcopy(job["context"])
        await self.m.verify_production_files(job, "render")
        changed = self.change_registry_filename()
        await self.m.verify_production_files(job, "render")  # The REAL file/enrollment gate.
        graph = await self.graph(job)
        self.assertEqual(graph["6165"]["inputs"]["lora_1"]["lora"], original)
        self.assertNotEqual(graph["6165"]["inputs"]["lora_1"]["lora"], changed)
        self.assertEqual(job["context"], frozen)

    async def test_f1_restart_functional_registry_drift_quarantines_old_job(self):
        job = await self.selected_lora_job()
        self.m.save_state()
        self.change_registry_filename()
        with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
            restarted.load_state()
            self.assertIsNone(restarted.state_load_error)
            recovered = restarted.find_job(job["id"])
            self.assertEqual(recovered["status"], "reconciliation_required")
            self.assertIn("registry", recovered["reconciliation_required"].lower())

    async def test_f1_registry_snapshot_is_deeply_immutable(self):
        with self.assertRaises(TypeError):
            self.m.LORA_REGISTRY["movement-v1"]["filename"] = "changed.safetensors"
        with self.assertRaises(TypeError):
            self.m.LORA_REGISTRY["movement-v1"]["defaults"]["h3_full"]["strength"] = .2
        with self.assertRaises(TypeError):
            self.m.CONDITIONING_BRIDGE["filename"] = "changed.safetensors"

    async def test_f1_restart_strength_compatibility_and_provenance_drift_quarantine(self):
        job = await self.job()
        path = self.m.CONFIG / "lora_registry.json"
        original = json.loads(path.read_text())
        # Only a used artifact is a dependency. HM is active in this job;
        # unrelated optional registry entries are covered by flexibility tests.
        changes = (("hm-breasts-v2", {"strength_range": [-1, 1]}),
                   ("hm-breasts-v2", {"compatibility": {"h3_full": "incompatible", "10eros_full": "pending"}}),
                   ("hm-breasts-v2", {"provenance_status": "external/user-provided"}))
        for identifier, change in changes:
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                row = next(row for row in data["loras"] if row["id"] == identifier)
                row.update(change)
                if row["compatibility"]["h3_full"] == "incompatible":
                    row["defaults"]["h3_full"]["enabled"] = False
                path.write_text(json.dumps(data))
                with load_controller(source=self.m.ROOT, production_probes=True) as restarted:
                    restarted.load_state()
                    self.assertEqual(restarted.find_job(job["id"])["status"], "reconciliation_required")
        path.write_text(json.dumps(original))

    async def test_f1_frozen_main_lora_order_survives_registry_reordering(self):
        from h3.app.production import registry_json, resolve_loras, registry_identity
        registry = registry_json(self.m.LORA_REGISTRY)
        selections = [{"id": key, "enabled": True, "strength": .25}
                      for key in ("hm-breasts-v2", "movement-v1")]
        before = resolve_loras(registry, "h3_full", selections, frozen=True)
        reordered = dict(reversed(list(registry.items())))
        self.assertEqual(registry_identity(registry), registry_identity(reordered))
        after = resolve_loras(reordered, "h3_full", selections, frozen=True)
        self.assertEqual(after, before, "frozen stack order must come from persisted rows")
        self.assertEqual([row["id"] for row in after], [row["id"] for row in selections])

    async def test_functional_model_manifest_capture_cannot_split_from_frozen_provenance(self):
        self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        manifest = self.m.CONFIG / "models_manifest.json"
        original = manifest.read_text()
        changed = json.loads(original)
        artifact = next(row for row in changed["artifacts"] if row["destination"].endswith(
            job["context"]["profile_identity"]["checkpoint"]))
        model = self.m.COMFY_MODELS_DIR / artifact["destination"]
        model.write_bytes(b"Different synthetic model generation; never loaded")
        artifact.update(size_bytes=model.stat().st_size, sha256=hashlib.sha256(model.read_bytes()).hexdigest())
        from h3.app.artifacts import verify_file
        receipt = {**{key: artifact[key] for key in ("repository", "revision", "repository_path", "destination")},
                   **verify_file(self.m.COMFY_MODELS_DIR, artifact["destination"], artifact)}
        (self.m.COMFY_MODELS_DIR / ".verification" / (artifact["id"] + ".json")).write_text(json.dumps(receipt))
        altered = json.dumps(changed)
        read = self.m.Path.read_text
        observed = []
        def capture(path, *args, **kwargs):
            if path == manifest and inspect.currentframe().f_back.f_code.co_name == "verify":
                # Actual on-disk B at the verifier's read, then A at separate
                # job_profile checks. File B matches B's provenance, never A's.
                manifest.write_text(altered)
                try:
                    observed.append(True)
                    return read(path, *args, **kwargs)
                finally:
                    manifest.write_text(original)
            return read(path, *args, **kwargs)
        with patch.object(self.m.Path, "read_text", capture):
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, "render")
        self.assertEqual(observed, [True])

    async def test_f2_every_native_python_dependency_changes_identity_and_fences(self):
        job = await self.job()
        original_state = self.m.QUEUE_FILE.read_bytes()
        adapter = self.m.job_workflow_adapter(job)
        identity = adapter.identity()
        package = self.m.ROOT / "custom_nodes/aj_production"
        sources = sorted(path for path in package.rglob("*.py") if "__pycache__" not in path.parts)
        self.assertGreaterEqual(len(sources), 4)
        for path in sources:
            with self.subTest(module=path.relative_to(package).as_posix()):
                body = path.read_bytes()
                if path.name == "__init__.py":
                    changed = body.replace(b"if release_encoder:\n            release_managed_models()",
                                           b"if release_encoder:\n            pass")
                    self.assertNotEqual(changed, body)
                else:
                    # Change real behavior while preserving the public function
                    # signature, instead of using a comment-only hash probe.
                    lines = body.decode().splitlines(keepends=True)
                    function = next(node for node in ast.walk(ast.parse(body)) if isinstance(node, ast.FunctionDef))
                    first = function.body[0].lineno - 1
                    indent = lines[first][:len(lines[first]) - len(lines[first].lstrip())]
                    lines.insert(first, indent + "raise RuntimeError('functional native drift probe')\n")
                    changed = "".join(lines).encode()
                    compile(changed, path.name, "exec")
                try:
                    path.write_bytes(changed)
                    self.assertNotEqual(adapter.identity(), identity)
                    self.m.QUEUE_FILE.write_bytes(original_state)
                    self.m.load_state()
                    self.assertEqual(self.m.find_job(job["id"])["status"], "reconciliation_required")
                finally:
                    path.write_bytes(body)
        self.m.QUEUE_FILE.write_bytes(original_state)
        self.m.load_state()

    async def test_f2_new_nested_native_module_is_in_identity(self):
        adapter = self.m.get_adapter("h3_v20", root=self.m.ROOT)
        before = adapter.identity()
        path = self.m.ROOT / "custom_nodes/aj_production/extra/submodule.py"
        path.parent.mkdir()
        path.write_text("VALUE = 1\n")
        self.assertNotEqual(adapter.identity(), before)

    async def test_f2_installer_and_verifier_include_future_nested_modules(self):
        path = self.m.ROOT / "custom_nodes/aj_production/nested/helper.py"
        path.parent.mkdir()
        path.write_text("def native_behavior():\n    return 42\n")
        install(self.m.ROOT / "custom_nodes/aj_production", self.m.COMFY_ROOT / "custom_nodes")
        job = await self.job()
        self.m.verify_production_native(job)
        installed = self.m.COMFY_ROOT / "custom_nodes/aj_production/nested/helper.py"
        installed.write_text("def native_behavior():\n    return 0\n")
        with self.assertRaises(self.m.SubmissionRejected):
            self.m.verify_production_native(job)

    async def test_f2_cache_bytes_are_not_functional_source_but_extra_loader_is_ambiguous(self):
        adapter = self.m.get_adapter("h3_v20", root=self.m.ROOT)
        original = adapter.identity()
        cache = self.m.ROOT / "custom_nodes/aj_production/__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "unrelated.pyc").write_bytes(b"discardable Python cache")
        self.assertEqual(adapter.identity(), original)
        job = await self.job()
        extra = self.m.COMFY_ROOT / "custom_nodes/aj_production/temporal.so"
        extra.write_bytes(b"ambiguous implementation source")
        with self.assertRaises(self.m.SubmissionRejected):
            self.m.verify_production_native(job)

    async def test_f2_changed_deployed_native_implementation_blocks_before_post(self):
        self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        await self.m.verify_production_files(job, "render")
        path = self.m.COMFY_ROOT / "custom_nodes/aj_production/__init__.py"
        body = path.read_text()
        path.write_text(body.replace("if release_encoder:\n            release_managed_models()",
                                    "if release_encoder:\n            pass"))
        with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Network before native verification")):
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, "render")

    async def test_f2_missing_extra_and_symlinked_deployed_modules_block(self):
        self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        package = self.m.COMFY_ROOT / "custom_nodes/aj_production"
        for label in ("missing", "extra", "symlink"):
            with self.subTest(case=label):
                path = package / ("unexpected.py" if label == "extra" else "temporal.py")
                body = path.read_bytes() if path.exists() else None
                if label == "extra":
                    path.write_text("VALUE = 1\n")
                else:
                    path.unlink()
                    if label == "symlink":
                        path.symlink_to(self.m.ROOT / "custom_nodes/aj_production/temporal.py")
                try:
                    with self.assertRaises(self.m.SubmissionRejected):
                        await self.m.verify_production_files(job, "render")
                finally:
                    path.unlink(missing_ok=True)
                    if body is not None:
                        path.write_bytes(body)

    async def test_f2_deployed_check_is_bound_to_the_frozen_job_identity(self):
        job = await self.job()
        resolve = self.m.job_workflow_adapter
        source = self.m.ROOT / "custom_nodes/aj_production/compiler.py"
        deployed = self.m.COMFY_ROOT / "custom_nodes/aj_production/compiler.py"
        changed = source.read_bytes() + b"\n# Different implementation generation\n"
        def drift_after_fence(item):
            adapter = resolve(item)  # Real frozen adapter validation under A.
            source.write_bytes(changed)
            deployed.write_bytes(changed)
            return adapter
        with patch.object(self.m, "job_workflow_adapter", side_effect=drift_after_fence):
            with self.assertRaises(self.m.SubmissionRejected):
                self.m.verify_production_native(job)

    async def test_f2_deployed_drift_during_preparation_cannot_cross_durable_arm(self):
        self.fixture_manifest()
        job = await self.job(profile="10eros_full")
        path = self.m.COMFY_ROOT / "custom_nodes/aj_production/compiler.py"
        async def prepare(*args, **kwargs):
            path.write_bytes(path.read_bytes() + b"\n# Drift while preparation awaited\n")
            return {}
        http, calls = self.transport()
        with http, patch.object(self.m, "_prepare_workflow_api", side_effect=prepare), \
             patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m._submit_workflow(job, self.m.production_workflow(job), "render")
        post.assert_not_awaited()
        self.assertNotIn("render_prompt_id", job)
        self.assertNotIn("render_prompt_id", json.loads(self.m.QUEUE_FILE.read_text())["queue"][0])

    def base_snapshot(self):
        fixture = json.loads((ROOT / "tests/fixtures/pr5_batch_retry.json").read_text())
        self.assertEqual(fixture["origin_commit"], "c6f033ab41a4456fd55558e652a563d8c31418d1")
        self.m.QUEUE_FILE.write_text(json.dumps(fixture["snapshot"]))
        self.m.load_state()
        self.assertIsNone(self.m.state_load_error)
        return fixture

    async def test_f3_actual_base_snapshot_exact_body_returns_original_receipt(self):
        fixture = self.base_snapshot()
        before = copy.deepcopy(self.m.request_results)
        actual = await self.m.create_batches(self.m.BatchRequest(**fixture["body"]))
        self.assertEqual(actual, fixture["accepted"])
        self.assertEqual(self.m.request_results, before)
        self.assertTrue(all(job["status"] == "reconciliation_required" for job in self.m.queue))

    async def test_actual_reviewed_pr6_state_keeps_receipt_without_migrating_render(self):
        fixture = json.loads((ROOT / "tests/fixtures/pr6_reviewed_retry.json").read_text())
        self.assertEqual(fixture["origin_commit"], "44e47699cd2bb00898055a3c9011a43cecd1d019")
        self.m.QUEUE_FILE.write_text(json.dumps(fixture["snapshot"]))
        self.m.load_state()
        self.assertIsNone(self.m.state_load_error)
        self.assertEqual(self.m.queue[0]["status"], "reconciliation_required")
        self.assertEqual(await self.m.create_batches(self.m.BatchRequest(**fixture["body"])), fixture["accepted"])
        self.assertNotIn("lora_registry_identity", self.m.queue[0]["context"])

    async def test_f3_changed_or_explicit_new_controls_never_match_legacy_hash(self):
        fixture = self.base_snapshot()
        cases = [
            {"render_settings": {"target_mpx": .8}},
            {"render_settings": {"first_steps": 32}},
            {"render_settings": {}}, {"prompt_mode": "autoprompt"},
            {"manual_final_prompt": None}, {"prompt": "Changed scene"},
            {"profile": "10eros_full", "model": "10eros_full"},
            {"pictures": list(reversed(fixture["body"]["pictures"]))},
            {"loras": [{"id": "movement-v1", "enabled": True, "strength": .25}]},
            {"audio": ["changed.wav"]}, {"seed": 42}, {"duration_seconds": 19},
            {"soft_timeout_minutes": 9}, {"random_each": False}, {"batches": 2},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(self.m.HTTPException) as raised:
                await self.m.create_batches(self.m.BatchRequest(**{**fixture["body"], **change}))
            self.assertEqual(raised.exception.status_code, 409)
        manual = dict(fixture["body"], prompt_mode="manual_final", manual_final_prompt="A continuous shot.")
        manual.pop("prompt")
        with self.assertRaises(self.m.HTTPException) as raised:
            await self.m.create_batches(self.m.BatchRequest(**manual))
        self.assertEqual(raised.exception.status_code, 409)

    async def test_f4_malformed_and_healthy_catalogue_remains_visible_and_read_only(self):
        original = await self.job()
        data = json.loads(self.m.QUEUE_FILE.read_text())
        healthy = copy.deepcopy(original)
        completed = copy.deepcopy(original)
        completed.update(id="completed", status="completed", video_outputs=["/api/proxy/render/view?filename=healthy.mp4&type=output"])
        missing = copy.deepcopy(original)
        missing["id"] = "missing-manual"
        missing["context"].pop("manual_final_prompt")
        null = dict(copy.deepcopy(original), id="null-context", context=None)
        array = dict(copy.deepcopy(original), id="list-context", context=[])
        unknown = dict(copy.deepcopy(original), id="unknown-source", context=None)
        unknown.pop("prompt_mode")
        bad_memory = dict(copy.deepcopy(original), id="bad-memory")
        bad_memory["context"]["profile_identity"]["memory_policy"] = []
        bad_overlap = dict(copy.deepcopy(original), id="bad-overlap")
        bad_overlap["context"]["profile_identity"]["overlap_policy"] = None
        bad_lora = dict(copy.deepcopy(original), id="bad-lora-identity")
        bad_lora["context"]["lora_identity"] = []
        erased = dict(copy.deepcopy(original), id="erased-source")
        erased.pop("prompt_mode")
        erased["context"].pop("prompt_mode")
        erased["context"].pop("manual_final_prompt")
        data["queue"] = [healthy, completed, missing, null, array, unknown, bad_memory, bad_overlap, bad_lora, erased]
        self.m.QUEUE_FILE.write_text(json.dumps(data))
        self.m.load_state()
        self.assertIsNone(self.m.state_load_error)
        before_queue = copy.deepcopy(self.m.queue)
        before_disk = self.m.QUEUE_FILE.read_bytes()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                     auth=("h3", "test-only-password")) as client:
            response = await client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        jobs = result["jobs"] if isinstance(result, dict) else result
        by_id = {job["id"]: job for job in jobs}
        self.assertEqual(len(by_id), 10)
        self.assertEqual(by_id[healthy["id"]]["status"], "render_queued_auto")
        self.assertEqual(by_id["completed"]["video_outputs"], completed["video_outputs"])
        for key in ("missing-manual", "null-context", "list-context", "unknown-source", "bad-memory", "bad-overlap", "bad-lora-identity", "erased-source"):
            self.assertEqual(by_id[key]["status"], "reconciliation_required")
            self.assertTrue(by_id[key]["reconciliation_required"])
        self.assertNotEqual(by_id["unknown-source"].get("prompt_mode"), "autoprompt")
        self.assertIsNone(by_id["erased-source"]["prompt_mode"])
        self.assertEqual(self.m.queue, before_queue)
        self.assertEqual(self.m.QUEUE_FILE.read_bytes(), before_disk)

    async def test_f4_quarantined_context_cannot_break_diagnostics_or_prompt_view(self):
        await self.job()
        data = json.loads(self.m.QUEUE_FILE.read_text())
        data["queue"][0]["context"] = None
        self.m.QUEUE_FILE.write_text(json.dumps(data))
        self.m.load_state()
        before = copy.deepcopy(self.m.queue)
        diagnostic = importlib.import_module(self.m.__package__ + ".diagnostics")
        with patch.object(diagnostic, "service_health", AsyncMock(return_value={"status": "PASS", "detail": "CPU fixture"})), \
             patch.object(self.m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": False}), \
             patch.object(self.m, "gpu_status", return_value={"error": "CPU fixture"}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                         auth=("h3", "test-only-password")) as client:
                response = await client.get("/api/diagnostics")
                self.assertEqual(response.status_code, 200)
                response = await client.get(f"/api/jobs/{self.m.queue[0]['id']}/prompts")
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["manual_final_prompt"])
        self.assertEqual(self.m.queue, before)

    async def test_f5_all_review_timing_tokens_fail_before_any_http(self):
        for text in REVIEW_BAD_TIMING:
            with self.subTest(text=text), patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")):
                with self.assertRaises(self.m.HTTPException) as raised:
                    await self.m.create_batches(self.manual_request(request_id=uuid.uuid4(), manual_final_prompt=text))
                self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.m.queue, [])

    async def test_f5_ratio_prose_remains_exact_without_clock_interpretation(self):
        for ratio in ("16:10", "16:9", "4:3", "3:2", "21:9", "9:16", "21:10"):
            text = f"  Use an aspect ratio of {ratio}.\r\n"
            with self.subTest(ratio=ratio):
                job = await self.job(request_id=uuid.uuid4(), manual_final_prompt=text)
                self.assertEqual(job["context"]["manual_final_prompt"], text)
                self.assertEqual(job["context"]["manual_final_prompt_sha256"], hashlib.sha256(text.encode()).hexdigest())

    async def test_f5_nearby_clock_suffix_unicode_and_interval_attacks_fail(self):
        bad = ("Clock 00:20.041.", "Clock -00:10.000.", "Clock +00:10.000.",
               "Clock 00：10.000.", "Clock 00:10．000.", "Clock ００:１０.０００.",
               "Range 00:00.000—00:10.000.", "Range 00:00.000–00:10.000.",
               "Clock 00:06.680.123.456.", "Range 00:00.000-00:05.000-00:10.000.",
               "Range 00:10.000-00:00.000.")
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.m.freeze_prompt_source("manual_final", None, text, 20.04)
        for text in ("Clock 00:00.000.", "Clock 00:20.040.", "Range 00:00.000-00:20.040."):
            with self.subTest(text=text):
                self.assertEqual(self.m.freeze_prompt_source("manual_final", None, text, 20.04)["manual_final_prompt"], text)

    async def test_f5_numbered_prose_is_not_a_clock(self):
        for text in ("1: Subject is centered. 2: Camera stays still.",
                     "Reference 1: A person. Reference 2: A room.",
                     "[Shot 1] Reference 1: Move continuously."):
            with self.subTest(text=text):
                self.assertEqual(self.m.freeze_prompt_source("manual_final", None, text, 20.04)["manual_final_prompt"], text)

    async def test_f5_spaced_signs_and_malformed_numeric_tokens_fail(self):
        for text in ("Clock - 00:10.000.", "Clock − 00:10.000.", "Clock −\n00:10.000.",
                     "Clock + 00:10.000.", "Range 00:00.000--00:10.000.",
                     "Clock 00:10.NaN.", "Clock 00:10.Infinity.",
                     "Clock 00:10.000:123.", "Clock 00:10.000e0."):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.m.freeze_prompt_source("manual_final", None, text, 20.04)

    async def test_f6_durable_render_ownership_never_defaults_to_prompt_recovery(self):
        job = await self.job()
        original = json.loads(self.m.QUEUE_FILE.read_text())
        for status in ("cancelling", "soft_timeout_cancelling", "hard_timeout_restarting_comfy"):
            for owner, submission in product((None, "invalid-service"), ("armed", "attempting", "accepted", "uncertain")):
                with self.subTest(status=status, owner=owner, submission=submission):
                    data = copy.deepcopy(original)
                    item = data["queue"][0]
                    pid = str(uuid.uuid4())
                    item.update(status=status, render_prompt_id=pid, prompt_id=pid, render_submission_state=submission)
                    if owner is not None:
                        item["active_service"] = owner
                    self.m.QUEUE_FILE.write_text(json.dumps(data))
                    with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP during load")):
                        self.m.load_state()
                    recovered = self.m.find_job(job["id"])
                    self.assertEqual(recovered["status"], "recovery_render")
                    self.assertFalse(self.m.service_recovering("prompt"))

    async def test_f6_ambiguous_manual_ownership_is_quarantined(self):
        await self.job()
        original = json.loads(self.m.QUEUE_FILE.read_text())
        for change in ({}, {"render_prompt_id": "not-a-uuid", "render_submission_state": "accepted"},
                       {"render_prompt_id": str(uuid.uuid4()), "render_submission_state": "accepted", "active_service": "prompt"},
                       {"render_prompt_id": str(uuid.uuid4()), "render_submission_state": "accepted", "prompt_prompt_id": str(uuid.uuid4())}):
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                data["queue"][0].update(status="cancelling", **change)
                self.m.QUEUE_FILE.write_text(json.dumps(data))
                self.m.load_state()
                self.assertEqual(self.m.queue[0]["status"], "reconciliation_required")
                self.assertNotEqual(self.m.queue[0]["status"], "recovery_prompt")
                if "prompt_prompt_id" not in change:
                    self.assertFalse(self.m.service_recovering("prompt"))
                # Contradictory remote UUID evidence retains PR #5's quarantine
                # safety barrier; it must not be erased just to unblock a service.

    async def test_f6_recovery_entrypoint_rejects_prompt_before_network(self):
        job = await self.job()
        job.update(status="recovery_prompt", prompt_id=str(uuid.uuid4()))
        for entry in (self.m._recover_one_job, self.m._recover_one_job_impl):
            with self.subTest(entry=entry.__name__), \
                 patch.object(self.m, "wait_service_ready", side_effect=AssertionError("Prompt contacted")), \
                 patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("HTTP invoked")):
                with self.assertRaises(self.m.SubmissionRejected):
                    await entry(job, "prompt")

    async def test_duplicate_manual_posts_remain_one_uuid_one_attempt_through_recovery(self):
        self.fixture_manifest()
        original = self.m
        try:
            for failure in ("timeout", "mismatched_uuid", "http_503", "invalid_json"):
                with self.subTest(failure=failure), load_controller(source=original.ROOT, production_probes=True) as controller:
                    self.m = controller
                    self.fixture_manifest()  # New inode/stat receipts for this disposable generation.
                    job = await self.job(profile="10eros_full", request_id=uuid.uuid4())
                    posts = []
                    def response(request):
                        if request.method != "POST" or request.url.path != "/prompt":
                            return None
                        body = json.loads(request.content)
                        durable = json.loads(controller.QUEUE_FILE.read_text())["queue"][0]
                        self.assertEqual(durable["render_submission_state"], "armed")
                        self.assertEqual(durable["render_prompt_id"], body["prompt_id"])
                        posts.append(body["prompt_id"])
                        if failure == "timeout":
                            raise httpx.ReadTimeout("Accepted POST lost its acknowledgement", request=request)
                        if failure == "mismatched_uuid":
                            return httpx.Response(200, json={"prompt_id": str(uuid.uuid4())})
                        if failure == "http_503":
                            return httpx.Response(503, json={"error": "uncertain remote result"})
                        return httpx.Response(200, content=b"invalid acknowledgement JSON")
                    transport, calls = self.transport(response)
                    workflow = controller.patch_workflow(job, controller.production_workflow(job),
                        approved_prompt=controller.final_prompt_for_render(job))
                    with transport, patch.object(controller, "run_cli_envelope", side_effect=self.cli):
                        with self.assertRaises(controller.SubmissionUncertain):
                            await controller._submit_workflow(job, workflow, "render")
                    self.assertEqual(len(posts), 1)
                    pid = posts[0]
                    self.assertEqual(job["render_prompt_id"], pid)
                    controller.load_state()
                    recovered = controller.queue[0]
                    with patch.object(controller, "wait_service_ready", AsyncMock(return_value=True)), \
                         patch.object(controller, "fetch_history", AsyncMock(return_value={})), \
                         patch.object(controller, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})), \
                         patch.object(controller, "_dispatch_prepared_workflow", AsyncMock()) as repost:
                        for _ in range(3):
                            await controller._recover_one_job(recovered, "render")
                        repost.assert_not_awaited()
                    self.assertEqual(recovered["status"], "recovery_render")
                    self.assertEqual(recovered["render_prompt_id"], pid)
                    self.assertEqual(sum(method == "POST" and path == "/prompt" for method, _, path in calls), 1)
        finally:
            self.m = original

    async def test_f7_exact_reference_visual_substitution_is_rejected(self):
        job = await self.job()
        graph = await self.graph(job)
        self.assertIn("7335:469", graph)  # Audio leaves samplers structurally present.
        graph["7456"]["inputs"]["image"] = ["4646", 0]
        with self.assertRaises(ValueError):
            self.m.job_workflow_adapter(job).validate_final_graph(graph, job["context"], phase="render")

    async def test_f7_visual_and_audio_type_compatible_substitutions_are_separate(self):
        job = await self.job()
        original = await self.graph(job)
        mutations = (
            ("7463", "image", ["4646", 0]),
            ("6960:6959", "samples", ["7334:6773", 0]),
            ("6182", "images", ["4646", 0]),
            ("7457", "audio", ["3876:3541", 0]),
            ("6960:6958", "samples", ["7334:6773", 0]),
        )
        for node, field, wrong in mutations:
            with self.subTest(node=node, field=field):
                graph = copy.deepcopy(original)
                graph[node]["inputs"][field] = wrong
                with self.assertRaises(ValueError):
                    self.m.job_workflow_adapter(job).validate_final_graph(graph, job["context"], phase="render")

    async def test_f7_all_four_selected_latents_reach_both_output_branches(self):
        from h3.app.workflow_conversion import _ancestors
        from tests.test_v20_render_features import selected_latent_route
        for upscaler in (False, True):
            for second in (False, True):
                with self.subTest(upscaler=upscaler, second=second):
                    job = await self.job(request_id=uuid.uuid4(), render_settings={
                        "latent_upscaler_enabled": upscaler, "second_pass_enabled": second})
                    graph = await self.graph(job)
                    self.assertEqual(graph["6960:6959"]["inputs"]["samples"], ["7335:6173", 0])
                    self.assertEqual(graph["6960:6958"]["inputs"]["samples"], ["7335:6173", 0])
                    selected = "7335:469" if second else "6178" if upscaler else "7334:6773"
                    self.assertEqual(selected_latent_route(graph)[0], (selected, 0))
                    self.assertIn(selected, _ancestors(graph, {graph["6182"]["inputs"]["images"][0]}))
                    self.assertIn(selected, _ancestors(graph, {graph["6182"]["inputs"]["audio"][0]}))


class RemediationFrontendTests(unittest.TestCase):
    def test_f8_narrow_registry_actual_javascript(self):
        from h3.app.production import read_loras
        from h3.app.workflow_adapters import get_adapter
        from h3.app.prompt_modes import PROMPT_MODES
        registry = [{"id": key, **value} for key, value in read_loras(ROOT / "h3/config/lora_registry.json").items()]
        registry[0]["strength_range"] = [-.5, .5]
        for value in registry[0]["defaults"].values():
            value["strength"] = .5
        config = {"default_profile": "h3_full", "prompt_modes": PROMPT_MODES, "loras": registry,
                  "profiles": [{"id": key, "label": key, "workflow": get_adapter("h3_v20").render_capabilities()}
                               for key in ("h3_full", "10eros_full")]}
        result = subprocess.run(["node", str(ROOT / "tests/fixtures/remediation_frontend.js"),
                                 str(ROOT / "h3/static/index.html"), json.dumps(config)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
