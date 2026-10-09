"""Read-only, bounded infrastructure tools, isolated from Vast and ComfyUI."""
import asyncio
import copy
import importlib
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import load_controller
from tests.test_step3_api import api


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_reports_all_checks_without_writes_or_control_calls(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            m.queue[:] = [{"id": "ready", "status": "render_queued_auto"}, {"id": "review", "status": "pending_review"}]
            m.save_state(); m.save_vast_control()
            snapshot = m.QUEUE_FILE.read_bytes(), m.VAST_CONTROL_FILE.read_bytes(), copy.deepcopy(m.queue), copy.deepcopy(m.vast_control)
            with patch.object(d, "service_health", AsyncMock(return_value={"status": "PASS", "detail": "Healthy"})), \
                 patch.object(m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": False, "reason": "Not verified"}), \
                 patch.object(m, "disk_status", return_value={"free_gb": 25}), \
                 patch.object(m, "gpu_status", return_value={"name": "fake CPU fixture", "vram_total_mb": 98304, "vram_used_mb": 1024}), \
                 patch.object(m, "save_state") as save, patch.object(m, "save_vast_control") as save_control, \
                 patch.object(m, "run_vast_cli", AsyncMock()) as vast, \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch, \
                 patch.object(m, "restart_local_service", AsyncMock()) as restart:
                async with api(m) as c:
                    response = await c.get("/api/diagnostics")
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual({check["id"] for check in result["checks"]},
                             {"controller", "render", "prompt", "gpu", "vram", "storage", "disk", "queue", "prefetch", "vast", "models", "memory", "prompt_cache"})
            self.assertEqual(result["prefetch"], {"target": 3, "ready": 1, "preparing": 0, "buffer": 1})
            self.assertEqual(result["queue"]["pending_review"], 1)
            self.assertTrue(result["models"]["provisional"])
            self.assertEqual(result["vram"]["total_mb"], 98304)
            self.assertEqual(result["vast"]["remote_authorization"], "not_probed")
            for function in (save, save_control, vast, dispatch, restart):
                function.assert_not_called()
            self.assertEqual(snapshot, (m.QUEUE_FILE.read_bytes(), m.VAST_CONTROL_FILE.read_bytes(), m.queue, m.vast_control))

    async def test_dead_service_has_short_timeout_and_does_not_block_other_probes(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            original_client = httpx.AsyncClient
            cancelled = asyncio.Event()

            async def handler(request):
                self.assertEqual((request.method, request.url.path), ("GET", "/system_stats"))
                if request.url.port == 8188:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return httpx.Response(200, json={"system": {}})

            def client(**options):
                self.assertEqual(options["timeout"], 1.0)
                return original_client(transport=httpx.MockTransport(handler), **options)

            start = time.monotonic()
            with patch.object(d, "PROBE_SECONDS", 0.05), patch.object(d.httpx, "AsyncClient", side_effect=client), \
                 patch.object(m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": False}), \
                 patch.object(m, "gpu_status", return_value={"error": "No GPU"}):
                result = await m.api_diagnostics()
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(result["services"]["render"]["status"], "FAIL")
            self.assertEqual(result["services"]["prompt"]["status"], "PASS")
            self.assertFalse(result["vram"]["available"])

    async def test_health_rejects_invalid_and_oversized_responses(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            original_client = httpx.AsyncClient
            for body in (b"invalid secret error", b"[]", b"x" * (d.MAX_STATS_BYTES + 1)):
                with patch.object(d.httpx, "AsyncClient", side_effect=lambda **options: original_client(
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **options
                )):
                    result = await d.service_health(m.PROMPT_COMFY_URL)
                self.assertEqual(result["status"], "FAIL")
                self.assertNotIn("secret", result["detail"])

    async def test_model_presence_remains_provisional_with_alternative_bridge_location(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            manifest_path = m.CONFIG / "legacy_models_manifest.json"
            initial = d.model_status(manifest_path, m.COMFY_MODELS_DIR, m.COMFY_ROOT)
            self.assertGreater(initial["missing"], 0)
            manifest = json.loads(manifest_path.read_text())
            paths = [m.COMFY_MODELS_DIR / item["dir"] / item["file"] for item in manifest["required_primary"]]
            paths.extend(m.COMFY_MODELS_DIR / "loras" / item["file"] for item in manifest["loras_default_required"])
            paths.extend(m.COMFY_MODELS_DIR / "semantic_bridge" / item["file"] for item in manifest["special_required_models"])
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"presence fixture, not real weights")
            result = d.model_status(manifest_path, m.COMFY_MODELS_DIR, m.COMFY_ROOT)
            self.assertEqual(result["missing"], 0)
            self.assertEqual(result["status"], "WARN")
            self.assertTrue(result["provisional"])
            paths[0].write_bytes(b"")
            self.assertEqual(d.model_status(manifest_path, m.COMFY_MODELS_DIR, m.COMFY_ROOT)["missing"], 1)

    async def test_controller_failure_and_storage_probe_error_are_visible(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            m.queue_persistence_error = "Injected persistence fence"
            with patch.object(d, "service_health", AsyncMock(return_value={"status": "PASS", "detail": "Healthy"})), \
                 patch.object(m, "persistent_storage_status", side_effect=OSError("probe failed")), \
                 patch.object(m, "gpu_status", return_value={"error": "Unavailable"}):
                result = await m.api_diagnostics()
            checks = {check["id"]: check for check in result["checks"]}
            self.assertEqual(checks["controller"]["status"], "FAIL")
            self.assertEqual(checks["storage"]["status"], "WARN")
            self.assertEqual(m.queue_persistence_error, "Injected persistence fence")

    async def test_diagnostics_and_logs_require_panel_auth(self):
        with load_controller() as m:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url="http://fixture") as c:
                for path in ("/api/diagnostics", "/api/logs/panel"):
                    self.assertEqual((await c.get(path)).status_code, 401)

    async def test_nonfinite_gpu_telemetry_is_json_safe_and_vram_unavailable(self):
        with load_controller() as m:
            d = importlib.import_module(m.__package__ + ".diagnostics")
            with patch.object(d, "service_health", AsyncMock(return_value={"status": "PASS", "detail": "Healthy"})), \
                 patch.object(m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": False}), \
                 patch.object(m, "gpu_status", return_value={"name": "fixture", "vram_total_mb": float("nan"), "vram_used_mb": float("inf")}):
                async with api(m) as c:
                    response = await c.get("/api/diagnostics")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["vram"]["available"])
            self.assertIsNone(response.json()["vram"]["total_mb"])


class LogTailTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_logs_are_normal_not_created_responses(self):
        with load_controller() as m:
            async with api(m) as c:
                for service in ("render", "prompt", "panel"):
                    response = await c.get("/api/logs/" + service)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "not_created")
                    self.assertFalse(response.json()["available"])

    async def test_small_tail_line_limit_and_utf8_replacement(self):
        with load_controller() as m:
            m.WORKSPACE.mkdir()
            (m.WORKSPACE / "comfyui-render.log").write_bytes("one\nżółć\n".encode() + b"\xff\n")
            async with api(m) as c:
                result = (await c.get("/api/logs/render?lines=2")).json()
            self.assertEqual(result["text"], "żółć\n�")
            self.assertEqual(result["line_count"], 2)
            self.assertTrue(result["truncated"])

    async def test_large_file_reads_only_bounded_tail(self):
        with load_controller() as m:
            logs = importlib.import_module(m.__package__ + ".logs")
            m.WORKSPACE.mkdir()
            (m.WORKSPACE / "comfyui-prompt.log").write_bytes(b"old\n" * logs.MAX_LOG_BYTES + b"latest1\nlatest2\n")
            original_read = os.read
            sizes = []

            def read(descriptor, limit):
                sizes.append(limit)
                return original_read(descriptor, limit)

            with patch.object(logs.os, "read", side_effect=read):
                result = logs.tail_log(m.WORKSPACE, "prompt", 2)
            self.assertEqual(result["text"], "latest1\nlatest2")
            self.assertTrue(result["truncated"])
            self.assertEqual(sizes, [logs.MAX_LOG_BYTES])

    async def test_invalid_service_and_line_count_never_disclose_arbitrary_file(self):
        with load_controller() as m:
            m.WORKSPACE.mkdir()
            (m.WORKSPACE / "h3-mobile.log").write_text("panel log")
            (m.WORKSPACE / "private.txt").write_text("private fixture")
            async with api(m) as c:
                self.assertEqual((await c.get("/api/logs/unknown")).status_code, 400)
                for value in ("0", "-1", "1001", "abc"):
                    self.assertEqual((await c.get("/api/logs/panel?lines=" + value)).status_code, 422)
                result = (await c.get("/api/logs/panel?path=private.txt")).json()
            self.assertEqual(result["text"], "panel log")

    async def test_symlink_and_fifo_fail_closed_without_reading_or_hanging(self):
        with load_controller() as m:
            m.WORKSPACE.mkdir()
            private = m.WORKSPACE / "private.txt"; private.write_text("private fixture")
            (m.WORKSPACE / "comfyui-render.log").symlink_to(private)
            os.mkfifo(m.WORKSPACE / "comfyui-prompt.log")
            async with api(m) as c:
                for service in ("render", "prompt"):
                    response = await asyncio.wait_for(c.get("/api/logs/" + service), timeout=1)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["status"], "unavailable")
                    self.assertEqual(response.json()["text"], "")
