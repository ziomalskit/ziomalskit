"""Exercise the conversion/submission adapter with in-memory HTTP responses."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, call, patch
import uuid

import httpx

from tests.helpers import load_controller
from tests.test_workflows import APPROVED, exact_convert
from tests.step3_helpers import ADAPTER_CATALOG as CATALOG

REAL_ASYNC_CLIENT = httpx.AsyncClient


def client_factory(handler):
    def make_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs["trust_env"] = False
        return REAL_ASYNC_CLIENT(*args, **kwargs)
    return make_client


def fixture_job(module):
    module.COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    (module.COMFY_INPUT_DIR / "__h3_silence_1s.wav").write_bytes(b"test fixture; no inference")
    return {
        "id": "submission-fixture", "render_seed": 123, "analysis_seed": 456, "prompt_seed": 789,
        "context": {
            "prompt": "Test scene", "model": "native_int8",
            "soft_timeout_minutes": 8, "hard_restart_after_seconds": 90,
            "pictures": [f"reference_{i}.png" for i in range(1, 7)],
            "audio": [], "loras": [],
        },
    }


class SubmissionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_prints_only_then_uses_catalog_from_same_worker(self):
        with load_controller() as module:
            requests = []
            def respond(request):
                requests.append(request)
                self.assertEqual(request.method, "GET")
                self.assertEqual(str(request.url), module.RENDER_COMFY_URL + "/object_info")
                return httpx.Response(200, json=CATALOG)
            job = fixture_job(module)
            workflow = module.patch_workflow(job, module.MASTER, approved_prompt=APPROVED)
            converted = exact_convert(json.loads(workflow.read_text()), CATALOG)
            cli = AsyncMock(side_effect=[
                {"data": {"status": "preview", "workflow": "filename.json", "prompt": converted}},
                {"data": {"valid": True, "error_count": 0, "spends_credits": False, "partner_nodes": []}},
            ])
            with patch.object(module, "run_cli_envelope", cli), patch.object(
                module.httpx, "AsyncClient", side_effect=client_factory(respond)
            ):
                prepared = await module._prepare_workflow_api(workflow, "render")
            self.assertEqual(cli.await_args_list, [
                call("render", "run", "--workflow", str(workflow), "--print-prompt"),
                call("render", "workflow", "validate", "--workflow", str(workflow.with_suffix(".api.json")),
                     "--input", str(workflow.with_suffix(".catalog.json"))),
            ])
            self.assertEqual(len(requests), 1)
            self.assertFalse(any(node["class_type"] == "LLMTextProcessor" for node in prepared.values()))
            self.assertEqual(prepared["2632"]["inputs"]["positive"], APPROVED)

    async def test_non_preview_envelope_never_fetches_catalog_or_dispatches(self):
        with load_controller() as module:
            for data in ({"status": "queued", "prompt": {}}, {"status": "preview", "workflow": {}}, None):
                cli = AsyncMock(return_value={"data": data})
                with patch.object(module, "run_cli_envelope", cli), patch.object(module.httpx, "AsyncClient") as client:
                    with self.assertRaisesRegex(RuntimeError, "non-submitting preview"):
                        await module._prepare_workflow_api(module.MASTER, "render")
                    client.assert_not_called()

    async def test_catalog_unavailable_stops_before_any_submission(self):
        with load_controller() as module:
            calls = []
            def respond(request):
                calls.append(request.method)
                return httpx.Response(503, json={"error": "not ready"})
            job = fixture_job(module)
            path = module.patch_workflow(job, module.MASTER, approved_prompt=APPROVED)
            cli = AsyncMock(return_value={"data": {"status": "preview", "prompt": exact_convert(json.loads(path.read_text()), CATALOG)}})
            with patch.object(module, "run_cli_envelope", cli), patch.object(
                module.httpx, "AsyncClient", side_effect=client_factory(respond)
            ):
                with self.assertRaises(httpx.HTTPStatusError):
                    await module._submit_workflow(job, path, "render")
            self.assertEqual(calls, ["GET"])
            self.assertFalse(job.get("render_submission_attempted"))

    async def test_dispatch_sends_validated_graph_and_previously_persisted_uuid_once(self):
        with load_controller() as module:
            job = fixture_job(module)
            module.queue[:] = [job]
            planned = []
            def respond(request):
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), module.RENDER_COMFY_URL + "/prompt")
                body = json.loads(request.content)
                saved = json.loads(module.QUEUE_FILE.read_text())["queue"][0]
                self.assertEqual(saved["render_prompt_id"], body["prompt_id"])
                self.assertEqual(saved["status"], "render_submitting")
                self.assertTrue(saved["render_submission_attempted"])
                self.assertEqual(str(uuid.UUID(body["prompt_id"])), body["prompt_id"])
                self.assertEqual(body["prompt"], {"prepared": "fixture"})
                self.assertEqual(body["client_id"], body["prompt_id"])
                planned.append(body["prompt_id"])
                return httpx.Response(200, json={"prompt_id": body["prompt_id"], "node_errors": {}})
            with patch.object(module, "_prepare_workflow_api", AsyncMock(return_value={"prepared": "fixture"})), patch.object(
                module.httpx, "AsyncClient", side_effect=client_factory(respond)
            ):
                accepted = await module._submit_workflow(job, module.MASTER, "render")
            self.assertEqual(planned, [accepted])
            self.assertEqual(job["render_submission_state"], "accepted")

    async def test_timeout_after_transmission_retains_id_and_never_reposts(self):
        with load_controller() as module:
            job = fixture_job(module)
            module.queue[:] = [job]
            requests = []
            def respond(request):
                requests.append(request)
                raise httpx.ReadTimeout("lost acknowledgement", request=request)
            with patch.object(module, "_prepare_workflow_api", AsyncMock(return_value={"prepared": "fixture"})), patch.object(
                module.httpx, "AsyncClient", side_effect=client_factory(respond)
            ):
                with self.assertRaises(module.SubmissionUncertain):
                    await module._submit_workflow(job, module.MASTER, "render")
            self.assertEqual(len(requests), 1)
            planned_id = job["render_prompt_id"]
            with patch.object(module, "wait_service_ready", AsyncMock(return_value=True)), patch.object(
                module, "fetch_history", AsyncMock(return_value={})
            ), patch.object(module, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})), patch.object(
                module, "_dispatch_prepared_workflow", AsyncMock()
            ) as dispatch:
                await module._recover_one_job(job, "render")
                dispatch.assert_not_awaited()
            self.assertEqual(job["status"], "recovery_render")
            self.assertEqual(job["render_prompt_id"], planned_id)

    async def test_authoritative_rejection_is_distinct_from_unknown_acknowledgement(self):
        with load_controller() as module:
            for status, body in ((400, {"error": {"type": "prompt_outputs_failed_validation"}, "node_errors": {}}),
                                 (408, {"error": "timeout"}), (502, {"error": "gateway"}),
                                 (200, {"prompt_id": "unexpected-id"})):
                job = fixture_job(module)
                module.queue[:] = [job]
                def respond(request):
                    return httpx.Response(status, json=body)
                with patch.object(module, "_prepare_workflow_api", AsyncMock(return_value={})), patch.object(
                    module.httpx, "AsyncClient", side_effect=client_factory(respond)
                ):
                    exception = module.SubmissionRejected if status == 400 else module.SubmissionUncertain
                    with self.assertRaises(exception):
                        await module._submit_workflow(job, module.MASTER, "render")
                self.assertEqual(job["render_submission_state"], "rejected" if status == 400 else "uncertain")

    async def test_invalid_input_verdict_and_external_paid_nodes_block_before_dispatch(self):
        with load_controller() as module:
            job = fixture_job(module)
            path = module.patch_workflow(job, module.MASTER, approved_prompt=APPROVED)
            preview = {"data": {"status": "preview", "prompt": exact_convert(json.loads(path.read_text()), CATALOG)}}
            def respond(request):
                self.assertEqual(request.method, "GET")
                return httpx.Response(200, json=CATALOG)
            for verdict in ({"valid": False, "error_count": 1}, {"valid": True, "error_count": 0, "spends_credits": True}):
                cli = AsyncMock(side_effect=[preview, {"data": verdict}])
                with patch.object(module, "run_cli_envelope", cli), patch.object(
                    module.httpx, "AsyncClient", side_effect=client_factory(respond)
                ):
                    with self.assertRaises(RuntimeError):
                        await module._submit_workflow(job, path, "render")
                self.assertFalse(job.get("render_submission_attempted"))

    async def test_cli_uses_selected_python_and_workspace_and_rejects_nonzero_exit(self):
        with load_controller() as module:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (b'{"type":"envelope","ok":true,"data":{}}\n', b'')
            with patch.object(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as spawn:
                await module.run_cli_envelope("prompt", "run", "--workflow", "fixture.json", "--print-prompt")
            self.assertEqual(spawn.call_args.args[:6], (
                module.COMFY_PYTHON, "-m", "comfy_cli", "--workspace", str(module.COMFY_ROOT), "--json"
            ))
            self.assertEqual(spawn.call_args.kwargs["env"]["COMFY_LOCAL_URL"], module.PROMPT_COMFY_URL)
            proc.returncode = 1
            with patch.object(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
                with self.assertRaisesRegex(RuntimeError, "exited 1"):
                    await module.run_cli_envelope("prompt", "run", "--print-prompt")

    async def test_cancellation_is_atomic_and_scoped_to_one_job_on_pinned_server(self):
        with load_controller() as module:
            requests = []
            def respond(request):
                requests.append(request)
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), module.RENDER_COMFY_URL + "/aj/cancel/specific-job")
                return httpx.Response(200, json={"protocol": "aj-terminal-v1", "prompt_id": "specific-job", "state": "running_signalled"})
            with patch.object(module.asyncio, "create_subprocess_exec", AsyncMock()) as spawn, patch.object(
                module.httpx, "AsyncClient", side_effect=client_factory(respond)
            ):
                self.assertEqual(await module.cancel_prompt("render", "specific-job"),
                                 {"protocol": "aj-terminal-v1", "prompt_id": "specific-job", "state": "running_signalled"})
                spawn.assert_not_awaited()
            self.assertEqual(len(requests), 1)

    async def test_invalid_or_failed_cancel_acknowledgement_is_not_confirmation(self):
        with load_controller() as module:
            for status, body in ((500, {}), (200, {}), (200, {"cancelled": "yes"}), (200, {"cancelled": True}),
                                 (200, {"protocol": "aj-terminal-v1", "prompt_id": "other-job", "state": "pending_deleted"}),
                                 (200, {"protocol": "aj-terminal-v1", "prompt_id": "job", "state": "invalid"})):
                def respond(request):
                    return httpx.Response(status, json=body)
                with patch.object(module.httpx, "AsyncClient", side_effect=client_factory(respond)):
                    with self.assertRaises((RuntimeError, httpx.HTTPStatusError)):
                        await module.cancel_prompt("prompt", "job")
            with patch.object(module.httpx, "AsyncClient", side_effect=client_factory(
                lambda request: httpx.Response(200, json={"protocol": "aj-terminal-v1", "prompt_id": "finished-job", "state": "unknown"})
            )):
                self.assertEqual((await module.cancel_prompt("prompt", "finished-job"))["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
