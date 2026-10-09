"""Final-audit regressions: lifecycle persistence, fair recovery and API contracts."""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
from unittest.mock import AsyncMock, Mock, patch
import unittest

from h3.scripts import preflight
from tests.helpers import load_controller
from tests.test_controller import history


class RuntimeBlockerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.m.vast_control.update(plan="none", idle_minutes=0, cost_guard_usd=0)
        self.m.queue[:] = []

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def executing(self, action):
        self.m.vast_control.update(plan="executing_" + action, reason=None)
        self.m.vast_control.pop("persistence_error", None)

    async def guard_ticks(self, count=3):
        ticks = 0

        async def tick(_seconds):
            nonlocal ticks
            ticks += 1
            self.assertEqual(self.m.vast_control["plan"], "action_failed")
            self.assertTrue(self.m.lifecycle_dispatch_blocked())
            if ticks == count:
                raise asyncio.CancelledError()

        with patch.object(self.m.asyncio, "sleep", side_effect=tick):
            with self.assertRaises(asyncio.CancelledError):
                await self.m.vast_guard_worker()
        self.assertEqual(ticks, count)

    async def test_guard_survives_permanent_write_failure_without_scheduling(self):
        self.m.vast_control["plan"] = "stop_after_current"
        self.m.vast_control["armed_generation"] = self.m.queue_persistence_epoch
        with patch.object(self.m, "_atomic_json_write", side_effect=OSError("ENOSPC")), \
             patch.object(self.m, "_schedule_instance_action") as schedule:
            await self.guard_ticks()
        schedule.assert_not_called()
        self.assertFalse(self.m.state_diagnostics()["ready"])
        self.assertIn("ENOSPC", self.m.vast_control["persistence_error"])

    async def test_guard_survives_one_write_failure_until_explicit_cancel(self):
        self.m.vast_control["plan"] = "stop_after_current"
        self.m.vast_control["armed_generation"] = self.m.queue_persistence_epoch
        calls = 0

        def write(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient write failure")

        with patch.object(self.m, "_atomic_json_write", side_effect=write), \
             patch.object(self.m, "_schedule_instance_action") as schedule:
            await self.guard_ticks()
        schedule.assert_not_called()
        self.assertGreaterEqual(calls, 2)
        self.assertFalse(self.m.state_diagnostics()["ready"])
        await self.m.api_vast_action(self.m.VastActionRequest(action="cancel_plan"))
        self.assertEqual(self.m.vast_control["plan"], "none")
        self.assertTrue(self.m.state_diagnostics()["ready"])

    async def test_stop_and_destroy_do_not_dispatch_after_write_failure(self):
        for action in ("stop", "destroy"):
            with self.subTest(action=action):
                self.executing(action)
                with patch.object(self.m, "instance_id_from_env", return_value="test-instance"), \
                     patch.object(self.m, "_atomic_json_write", side_effect=OSError("EIO")), \
                     patch.object(self.m, "run_vast_cli", AsyncMock()) as cli:
                    await self.m.delayed_instance_action(action, 0)
                cli.assert_not_awaited()
                self.assertEqual(self.m.vast_control["plan"], "action_failed")
                self.assertFalse(self.m.state_diagnostics()["ready"])

    async def test_transient_stop_and_destroy_write_failure_does_not_resume_action(self):
        for action in ("stop", "destroy"):
            with self.subTest(action=action):
                self.executing(action)
                real_write = self.m._atomic_json_write
                writes = 0

                def write(*args):
                    nonlocal writes
                    writes += 1
                    if writes == 1:
                        raise OSError("transient EIO")
                    real_write(*args)

                with patch.object(self.m, "instance_id_from_env", return_value="test-instance"), \
                     patch.object(self.m, "_atomic_json_write", side_effect=write), \
                     patch.object(self.m, "run_vast_cli", AsyncMock()) as cli, \
                     patch.object(self.m, "_schedule_instance_action") as schedule:
                    await self.m.delayed_instance_action(action, 0)
                    await self.guard_ticks()
                self.assertEqual(writes, 2)
                cli.assert_not_awaited()
                schedule.assert_not_called()
                self.assertFalse(self.m.state_diagnostics()["ready"])

    async def test_cancelled_stop_and_destroy_with_failed_persistence_never_retry(self):
        for action in ("stop", "destroy"):
            with self.subTest(action=action):
                self.executing(action)
                writes = 0

                def write(*_args):
                    nonlocal writes
                    writes += 1
                    if writes > 1:
                        raise OSError("interruption state write failed")

                with patch.object(self.m, "instance_id_from_env", return_value="test-instance"), \
                     patch.object(self.m, "_atomic_json_write", side_effect=write), \
                     patch.object(self.m, "run_vast_cli", AsyncMock(side_effect=asyncio.CancelledError())) as cli, \
                     patch.object(self.m, "_schedule_instance_action") as schedule:
                    with self.assertRaises(asyncio.CancelledError):
                        await self.m.delayed_instance_action(action, 0)
                    await self.guard_ticks()
                cli.assert_awaited_once()
                schedule.assert_not_called()
                self.assertFalse(self.m.state_diagnostics()["ready"])

    async def test_uncertain_stop_and_destroy_with_failed_diagnostics_never_retry(self):
        for action in ("stop", "destroy"):
            with self.subTest(action=action):
                self.executing(action)
                writes = 0

                def write(*_args):
                    nonlocal writes
                    writes += 1
                    if writes > 1:
                        raise OSError("diagnostic persistence failure")

                with patch.object(self.m, "instance_id_from_env", return_value="test-instance"), \
                     patch.object(self.m, "_atomic_json_write", side_effect=write), \
                     patch.object(self.m, "run_vast_cli", AsyncMock(side_effect=TimeoutError("ack unknown"))) as cli, \
                     patch.object(self.m, "_schedule_instance_action") as schedule:
                    await self.m.delayed_instance_action(action, 0)
                    await self.guard_ticks()
                cli.assert_awaited_once_with(action, "instance", "test-instance", *(["-y"] if action == "destroy" else []))
                schedule.assert_not_called()

    async def test_storage_inspection_failure_is_reported_before_destroy(self):
        self.m.vast_control["plan"] = "executing_destroy"
        with patch.object(self.m.asyncio, "to_thread", AsyncMock(side_effect=OSError("mount inspection failed"))), \
             patch.object(self.m, "run_vast_cli", AsyncMock()) as cli:
            await self.m.delayed_instance_action("destroy", 0, require_persistence=True)
        cli.assert_not_awaited()
        self.assertEqual(self.m.vast_control["plan"], "action_failed")
        self.assertIn("mount inspection failed", self.m.vast_control["reason"])

    async def test_pending_action_is_blocked_when_settings_write_fails_during_await(self):
        for action, phase in (("stop", "delay"), ("destroy", "delay"), ("destroy", "storage")):
            with self.subTest(action=action, phase=phase):
                self.executing(action)

                async def fail_settings(*_args):
                    with patch.object(self.m, "_atomic_json_write", side_effect=OSError("settings EIO")):
                        with self.assertRaises(OSError):
                            await self.m.api_vast_action(self.m.VastActionRequest(action="set_idle_timer", idle_minutes=1))
                    return {"safe_for_destroy_keep_data": True}

                sleep = AsyncMock(side_effect=fail_settings) if phase == "delay" else AsyncMock()
                storage = AsyncMock(side_effect=fail_settings) if phase == "storage" else AsyncMock(
                    return_value={"safe_for_destroy_keep_data": True})
                with patch.object(self.m, "instance_id_from_env", return_value="test-instance"), \
                     patch.object(self.m.asyncio, "sleep", sleep), \
                     patch.object(self.m.asyncio, "to_thread", storage), \
                     patch.object(self.m, "run_vast_cli", AsyncMock()) as cli, \
                     patch.object(self.m, "_schedule_instance_action") as schedule:
                    await self.m.delayed_instance_action(action, 0, require_persistence=action == "destroy")
                    await self.guard_ticks()
                cli.assert_not_awaited()
                schedule.assert_not_called()
                self.assertIn("became unavailable", self.m.vast_control["reason"])

    async def test_immediate_stop_and_destroy_write_failure_leave_safe_plan(self):
        for action, confirmation in (("stop_now", "STOP"), ("destroy_now", "DESTROY")):
            with self.subTest(action=action):
                self.m.vast_control["plan"] = "none"
                with patch.object(self.m, "_atomic_json_write", side_effect=OSError("ENOSPC")), \
                     patch.object(self.m, "_schedule_instance_action") as schedule:
                    with self.assertRaises(OSError):
                        await self.m.api_vast_action(self.m.VastActionRequest(action=action, confirm=confirmation))
                self.assertEqual(self.m.vast_control["plan"], "action_failed")
                schedule.assert_not_called()

    async def test_jobs_keeps_public_contract_and_config_carries_diagnostics(self):
        self.m.state_load_error = "queue unreadable"
        self.assertEqual(await self.m.jobs(), {"jobs": [], "batches": []})
        config = await self.m.config()
        self.assertFalse(config["state"]["ready"])
        self.assertEqual(config["state"]["queue_error"], "queue unreadable")

    async def fair_recovery(self, *, long_watcher=False):
        for service in ("prompt", "render"):
            with self.subTest(service=service):
                first = {"id": "manual", "status": f"recovery_{service}", "active_service": service,
                         f"{service}_prompt_id": "first-id", f"{service}_submission_state": "uncertain"}
                second = {**first, "id": "terminal", f"{service}_prompt_id": "second-id", "review_required": True}
                self.m.queue[:] = [first, second]
                reached = asyncio.Event()
                watcher_started = asyncio.Event()
                blocked = asyncio.Event()
                real_sleep = asyncio.sleep
                reads = []

                async def fetch(_service, pid, **_kwargs):
                    reads.append(pid)
                    if pid == "first-id":
                        return {}
                    entry = history(video=service == "render")
                    if service == "prompt":
                        fallback = str(self.m.MAP["prompt_capture"]["fallback_top_level_final_node_id"])
                        entry["outputs"] = {fallback: {"text": ["final candidate"]}}
                    return entry

                async def watcher(*_args):
                    watcher_started.set()
                    # The status can change while a recovered watcher is alive.
                    first["status"] = "soft_timeout_cancelling"
                    await blocked.wait()

                async def tick(_seconds):
                    if second["status"] in {"completed", "pending_review"}:
                        reached.set()
                        await blocked.wait()
                    await real_sleep(0)

                remote = {"queue_running": [[0, "first-id", {}]] if long_watcher else [], "queue_pending": []}
                with patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)), \
                     patch.object(self.m, "fetch_history", side_effect=fetch), \
                     patch.object(self.m, "fetch_service_queue", AsyncMock(return_value=remote)), \
                     patch.object(self.m, "watch_prompt", side_effect=watcher) as watch, \
                     patch.object(self.m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch, \
                     patch.object(self.m.asyncio, "sleep", side_effect=tick):
                    runner = asyncio.create_task(self.m.recover_jobs_after_controller_restart())
                    try:
                        await asyncio.wait_for(reached.wait(), 1)
                        self.assertIn("second-id", reads)
                        if long_watcher:
                            await asyncio.wait_for(watcher_started.wait(), 1)
                            self.assertTrue(self.m.service_recovering(service))
                            watch.assert_awaited_once()
                        else:
                            self.assertEqual(first["status"], f"recovery_{service}")
                        dispatch.assert_not_awaited()
                    finally:
                        runner.cancel()
                        await asyncio.gather(runner, return_exceptions=True)
                self.assertFalse(self.m.recovering_services)
                self.assertFalse(self.m.recovery_counts)

    async def test_manual_recovery_does_not_starve_terminal_job_on_same_service(self):
        await self.fair_recovery()

    async def test_long_recovery_watcher_does_not_starve_same_service(self):
        await self.fair_recovery(long_watcher=True)


class PreflightControllerTests(unittest.TestCase):
    def test_unavailable_controller_is_rejected_even_when_http_and_gpu_are_ready(self):
        critical = ["LLMTextProcessor", "BunnyH3ConditioningBridge", "MinimaxH3LatentUpscaler3D",
                    "MergeImageBatchAndAudioList", "Power Lora Loader (rgthree)", "Seed (rgthree)"]
        states = [None, {"ready": False}, {"ready": True, "queue_error": "corrupt queue"},
                  {"ready": True, "lifecycle_error": "write failed"}, {"ready": True, "state_error": "unavailable"}]
        healthy = {"ready": True, "queue_error": None, "lifecycle_error": None}
        for state in states + [healthy]:
            with self.subTest(state=state):
                def get(url, *_args):
                    return {"batch": {}, "state": state} if url.endswith("/api/config") else {cls: {} for cls in critical}

                with patch.object(preflight, "get_json", side_effect=get), \
                     patch.dict(os.environ, {"H3_ALLOW_DIAGNOSTIC_SUBMISSIONS": "1"}), \
                     patch.object(preflight, "verify_llama"), \
                     patch.object(preflight, "validate_gpu"), \
                     patch.object(preflight, "model_requirements", return_value=[]), \
                     patch.object(preflight.subprocess, "check_output", side_effect=[preflight.COMFY_COMMIT, "1.21.0", "release 13.0", "{}"]), \
                     patch.object(preflight.json, "loads", return_value={}), \
                     patch("pathlib.Path.read_text", return_value="{}"), contextlib.redirect_stdout(io.StringIO()):
                    errors = preflight.run_checks()
                self.assertEqual(bool(errors), state != healthy)
                if errors:
                    self.assertTrue(all(error.startswith("panel service:") for error in errors), errors)
