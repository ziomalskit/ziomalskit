"""CPU export with the real adapter/guards; all worker responses are synthetic."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from h3.scripts.prepare_prompt_acceptance import prepare
from tests import test_production_controller as controller_fixture
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert


class PromptAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    setUp = controller_fixture.ProductionControllerTests.setUp
    transport = controller_fixture.ProductionControllerTests.transport

    def output(self):
        return self.m.ROOT.parent / ("probe-" + uuid.uuid4().hex)

    async def export(self, output=None):
        body = self.req.model_dump(mode="json")
        body["seed"] = 123
        return await prepare(self.m, body, output or self.output(), str(uuid.uuid4()), 456, 789)

    async def test_pending_davidau_blocks_before_conversion_or_network(self):
        with patch.dict(os.environ, H3_ALLOW_SUBMISSIONS="0"), \
             patch.object(self.m, "run_cli_envelope", AsyncMock()) as cli, \
             patch.object(self.m, "verify_production_files", AsyncMock()) as verify:
            with self.assertRaisesRegex(ValueError, "manifest not frozen|provenance"):
                await self.export()
            cli.assert_not_awaited()
            verify.assert_not_awaited()

    def approved_fixture(self):
        # Synthetic future approval exists only in the disposable CPU fixture.
        manifest = self.m.CONFIG / "models_manifest.json"
        value = json.loads(manifest.read_text())
        value["pending_artifacts"] = []
        value["status"] = "production_pinned_gpu_pending"
        manifest.write_text(json.dumps(value))

    async def test_both_profiles_export_prompt_only_exact_context_without_queue_or_post(self):
        self.approved_fixture()
        queue_before = self.m.QUEUE_FILE.read_bytes() if self.m.QUEUE_FILE.exists() else None
        for profile in self.m.PRODUCTION["profiles"]:
            with self.subTest(profile=profile):
                self.req = self.req.model_copy(update={"profile": profile, "prompt": "Dokładny tekst 🦊\nSześć referencji."})
                model = self.m.model_path_for_preset(profile)
                model.parent.mkdir(parents=True, exist_ok=True)
                model.write_bytes(b"synthetic CPU model fixture")
                async def cli(service, *args):
                    self.assertEqual(service, "prompt")
                    if args[0] == "run":
                        self.assertEqual(args[-1], "--print-prompt")
                        workflow = json.loads(Path(args[2]).read_text())
                        return {"data": {"status": "preview", "prompt": exact_convert(workflow, production_catalog())}}
                    self.assertEqual(args[:2], ("workflow", "validate"))
                    return {"data": {"valid": True, "error_count": 0}}
                transport, calls = self.transport()
                output = self.output()
                original_state = self.m.STATE
                with transport, patch.dict(os.environ, H3_ALLOW_SUBMISSIONS="0"), \
                     patch.object(self.m, "run_cli_envelope", side_effect=cli), \
                     patch.object(self.m, "verify_production_files", AsyncMock()) as verify, \
                     patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as post:
                    result = await self.export(output)
                    post.assert_not_awaited()
                    self.assertEqual(verify.await_count, 2)
                self.assertEqual(calls, [("GET", 8189, "/object_info")])
                request = json.loads((output / "request.json").read_text())
                evidence = json.loads((output / "evidence.json").read_text())
                context = evidence["job"]["context"]
                self.assertEqual(context["prompt"].encode(), self.req.prompt.encode())
                self.assertEqual(context["legal_frame_count"], 481)
                self.assertEqual(context["effective_duration_seconds"], 20.04)
                self.assertEqual(context["profile"], profile)
                self.assertEqual(request["prompt_id"], request["client_id"])
                self.assertFalse(result["submitted"])
                self.assertEqual(self.m.STATE, original_state)
                self.assertEqual(self.m.queue, [])
                self.assertEqual(self.m.batches, [])
                self.assertEqual((output / "request.json").stat().st_mode & 0o777, 0o600)
                self.assertFalse(any(node["class_type"] in {"CreateVideo", "SaveVideo", "KSampler"}
                                     for node in request["prompt"].values()))
        self.assertEqual(self.m.QUEUE_FILE.read_bytes() if self.m.QUEUE_FILE.exists() else None, queue_before)

    async def test_enabled_submissions_and_active_controller_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "flags OFF"):
            await self.export()
        with patch.dict(os.environ, H3_ALLOW_SUBMISSIONS="0"):
            self.m.queue.append({"id": "existing"})
            before = copy.deepcopy(self.m.queue)
            with self.assertRaisesRegex(ValueError, "fresh process"):
                await self.export()
            self.assertEqual(self.m.queue, before)

    async def test_provenance_failure_does_not_publish_request(self):
        self.approved_fixture()
        output = self.output()
        with patch.dict(os.environ, H3_ALLOW_SUBMISSIONS="0"), \
             patch.object(self.m, "verify_production_files", AsyncMock(side_effect=RuntimeError("integrity"))):
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                await self.export(output)
        self.assertFalse(output.exists())

    async def test_export_failure_restores_context_and_cannot_overwrite_evidence(self):
        self.approved_fixture()
        output = self.output()
        original_state = self.m.STATE
        with patch.dict(os.environ, H3_ALLOW_SUBMISSIONS="0"), \
             patch.object(self.m, "verify_production_files", AsyncMock()), \
             patch.object(self.m, "_prepare_workflow_api", AsyncMock(side_effect=RuntimeError("catalog"))):
            with self.assertRaisesRegex(RuntimeError, "catalog"):
                await self.export(output)
            self.assertFalse((output / "request.json").exists())
            self.assertEqual(self.m.STATE, original_state)
            self.assertEqual(self.m.queue, [])
            with self.assertRaises(FileExistsError):
                await self.export(output)


if __name__ == "__main__":
    unittest.main()
