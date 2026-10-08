"""Durable watchdog deadlines with real local watcher children; no remote work."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests.helpers import load_controller


REAL_SPAWN = asyncio.create_subprocess_exec
REAL_WAIT_FOR = asyncio.wait_for
WATCHER = """import json, sys
for line in sys.stdin:
    print(json.dumps({'type': 'heartbeat'}), flush=True)
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.clock = Clock()
        self.job = {
            "id": "watchdog", "status": "render_running", "active_service": "render",
            "render_prompt_id": "owned-render-id", "render_submission_state": "accepted",
            "render_submission_attempted": True,
            "context": {"soft_timeout_minutes": 1, "hard_restart_after_seconds": 30},
        }
        self.m.queue.append(self.job)
        self.m.save_state()
        self.children = []
        self.watcher_code = WATCHER
        self.spawned = asyncio.Queue()
        self.cancelled = asyncio.Event()
        self.restarted = asyncio.Event()
        self.cancel = AsyncMock(side_effect=self.cancel_failure)
        self.restart = AsyncMock(side_effect=self.restart_success)
        self.dispatch = AsyncMock()
        self.patches = [
            patch.object(self.m, "time", self.clock),
            patch.object(self.m.asyncio, "create_subprocess_exec", side_effect=self.spawn),
            patch.object(self.m, "cancel_prompt", self.cancel),
            patch.object(self.m, "restart_comfy", self.restart),
            patch.object(self.m, "_dispatch_prepared_workflow", self.dispatch),
            patch.object(self.m, "wait_service_ready", AsyncMock(return_value=True)),
            patch.object(self.m, "fetch_history", AsyncMock(return_value={})),
            patch.object(self.m, "fetch_service_queue", AsyncMock(return_value={
                "queue_running": [[0, "owned-render-id", {}]], "queue_pending": []})),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for process in self.children:
            if process.returncode is None:
                process.kill()
            await process.wait()
            self.assertFalse(Path(f"/proc/{process.pid}").exists())
        for item in reversed(self.patches):
            item.stop()
        self.fixture.__exit__(None, None, None)

    async def spawn(self, *_args, **_kwargs):
        process = await REAL_SPAWN(
            sys.executable, "-u", "-c", self.watcher_code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.children.append(process)
        await self.spawned.put(process)
        return process

    async def cancel_failure(self, *_args):
        self.cancelled.set()
        raise httpx.ReadTimeout("injected cancellation response loss")

    async def restart_success(self, _service):
        self.restarted.set()

    async def pulse(self, process, seconds):
        self.clock.advance(seconds)
        process.stdin.write(b"tick\n")
        await process.stdin.drain()

    async def wait_for(self, condition):
        async with asyncio.timeout(2):
            while not condition():
                await asyncio.sleep(.001)

    async def start_watcher(self, *, recovery=False):
        operation = self.m._recover_one_job(self.job, "render") if recovery else self.m.watch_prompt(
            self.job, "owned-render-id", "render"
        )
        task = asyncio.create_task(operation)
        process = await REAL_WAIT_FOR(self.spawned.get(), 2)
        return task, process

    async def stop_watcher(self, task, process):
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.returncode)
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    async def start_timeout(self):
        task, process = await self.start_watcher()
        await self.pulse(process, 61)
        await REAL_WAIT_FOR(self.cancelled.wait(), 2)
        await self.wait_for(lambda: self.job.get("render_timeout_cancel_state") == "failed")
        self.assertEqual(self.job["render_hard_timeout_deadline"], 1091)
        self.assertIn("ReadTimeout", self.job["cancel_warning"])
        return task, process

    async def test_cancel_failure_keeps_original_deadline_and_restarts_exactly_once(self):
        task, process = await self.start_timeout()
        await self.pulse(process, 29)
        await asyncio.sleep(.01)
        self.restart.assert_not_awaited()
        await self.pulse(process, 1)
        result = await REAL_WAIT_FOR(task, 2)
        self.assertTrue(result["stuck"])
        self.restart.assert_awaited_once_with("render")
        self.cancel.assert_awaited_once_with("render", "owned-render-id")
        self.dispatch.assert_not_awaited()
        self.assertEqual(self.job["render_hard_restart_state"], "completed")
        raw = json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]
        self.assertEqual(raw["render_hard_timeout_deadline"], 1091)
        self.assertEqual(raw["render_hard_restart_state"], "completed")
        self.assertFalse(Path(f"/proc/{process.pid}").exists())
        # Even a remote queue still showing the owned attempt cannot replay restart.
        self.m._preserve_recovery(self.job, "render", "history not visible yet")
        await self.m._recover_one_job(self.job, "render")
        self.restart.assert_awaited_once()
        self.dispatch.assert_not_awaited()
        self.assertEqual(len(self.children), 1)

    async def test_multiple_restart_recovery_cycles_do_not_move_deadline(self):
        task, process = await self.start_timeout()
        await self.stop_watcher(task, process)
        for _cycle in range(3):
            self.m.load_state()
            self.job = self.m.queue[0]
            self.assertEqual(self.job["status"], "recovery_render")
            task, process = await self.start_watcher(recovery=True)
            await self.pulse(process, 5)
            await asyncio.sleep(.01)
            self.assertEqual(self.job["render_hard_timeout_deadline"], 1091)
            self.restart.assert_not_awaited()
            await self.stop_watcher(task, process)
        self.m.load_state()
        self.job = self.m.queue[0]
        task, process = await self.start_watcher(recovery=True)
        await self.pulse(process, 15)
        await REAL_WAIT_FOR(task, 2)
        self.restart.assert_awaited_once_with("render")
        self.cancel.assert_awaited_once()
        self.dispatch.assert_not_awaited()
        self.assertEqual(self.job["render_hard_timeout_deadline"], 1091)
        self.assertEqual(self.job["status"], "recovery_render")

    async def test_unknown_restart_outcome_is_not_retried(self):
        task, process = await self.start_timeout()
        self.restart.side_effect = RuntimeError("restart acknowledgement lost")
        await self.pulse(process, 30)
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await REAL_WAIT_FOR(task, 2)
        self.assertEqual(self.job["render_hard_restart_state"], "uncertain")
        self.m.load_state()
        self.job = self.m.queue[0]
        await self.m._recover_one_job(self.job, "render")
        self.restart.assert_awaited_once()
        self.dispatch.assert_not_awaited()
        self.assertEqual(len(self.children), 1)

    async def test_success_visible_during_lost_cancel_reply_prevents_restart(self):
        success = {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"video": {"videos": [{"filename": "paid-result.mp4", "type": "output"}]}},
        }

        async def cancel_race(*_args):
            self.m.fetch_history.return_value = success
            self.clock.advance(30)
            self.cancelled.set()
            raise httpx.ReadTimeout("success appeared while cancel response was lost")

        self.cancel.side_effect = cancel_race
        task, process = await self.start_watcher()
        await self.pulse(process, 61)
        result = await REAL_WAIT_FOR(task, 2)
        self.assertTrue(result["settled"])
        self.assertEqual(self.job["status"], "completed")
        self.assertIn("paid-result.mp4", self.job["video_outputs"][0])
        self.restart.assert_not_awaited()
        self.dispatch.assert_not_awaited()
        # A later vanished history cannot erase the result already reconciled.
        self.m.fetch_history.side_effect = RuntimeError("history became unavailable")
        await self.m._finish_watched_job(self.job, "render", result)
        self.assertEqual(self.job["status"], "completed")
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    async def test_absent_or_unreadable_remote_id_does_not_restart_other_work(self):
        task, process = await self.start_timeout()
        self.m.fetch_service_queue.return_value = {"queue_running": [[0, "different-job", {}]], "queue_pending": []}
        await self.pulse(process, 30)
        result = await REAL_WAIT_FOR(task, 2)
        self.assertTrue(result["uncertain"])
        self.assertEqual(self.job["status"], "recovery_render")
        self.assertEqual(self.job["render_hard_timeout_deadline"], 1091)
        self.assertNotIn("render_hard_restart_state", self.job)
        self.restart.assert_not_awaited()
        self.dispatch.assert_not_awaited()

        self.m.fetch_service_queue.return_value = {"queue_running": [[0, "owned-render-id", {}]], "queue_pending": []}
        self.m.fetch_history.side_effect = httpx.ReadTimeout("history not authoritative")
        task, process = await self.start_watcher()
        result = await REAL_WAIT_FOR(task, 2)
        self.assertTrue(result["uncertain"])
        self.restart.assert_not_awaited()

    async def test_invalid_durable_progress_time_fails_closed_before_spawn(self):
        self.job.update(render_watchdog_prompt_id="owned-render-id", render_watchdog_last_progress_at="invalid")
        with self.assertRaisesRegex(RuntimeError, "progress time is invalid"):
            await self.m.watch_prompt(self.job, "owned-render-id", "render")
        self.assertEqual(self.children, [])
        self.restart.assert_not_awaited()

    async def test_failed_pre_restart_commit_recovers_without_false_uncertainty(self):
        task, process = await self.start_timeout()
        write = self.m._atomic_json_write
        injected = False

        def fail_fence(path, payload):
            nonlocal injected
            if not injected and path == self.m.QUEUE_FILE and self.job.get("render_hard_restart_state") == "attempting":
                injected = True
                raise OSError("pre-restart durable commit EIO")
            return write(path, payload)

        with patch.object(self.m, "_atomic_json_write", side_effect=fail_fence):
            await self.pulse(process, 30)
            with self.assertRaisesRegex(OSError, "durable commit EIO"):
                await REAL_WAIT_FOR(task, 2)
        self.restart.assert_not_awaited()
        self.assertNotIn("render_hard_restart_state", self.job)
        self.assertEqual(self.job["render_hard_timeout_deadline"], 1091)
        self.m._handle_worker_error(self.job, "render", OSError("pre-restart commit failed"))
        self.m._resume_queue_persistence()
        task, process = await self.start_watcher(recovery=True)
        await REAL_WAIT_FOR(task, 2)
        self.restart.assert_awaited_once_with("render")
        self.dispatch.assert_not_awaited()

    async def test_secondary_cleanup_failure_retains_authoritative_success(self):
        task, process = await self.start_timeout()
        self.m.fetch_history.return_value = {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"video": {"videos": [{"filename": "preserved.mp4", "type": "output"}]}},
        }
        cleanup = self.m._terminate_subprocess

        async def failing_cleanup(child, **options):
            await cleanup(child, **options)
            raise OSError("secondary successful-render cleanup EIO")

        with patch.object(self.m, "_terminate_subprocess", side_effect=failing_cleanup):
            await self.pulse(process, 30)
            result = await REAL_WAIT_FOR(task, 2)
        self.assertTrue(result["settled"])
        self.assertEqual(self.job["status"], "completed")
        self.assertIn("preserved.mp4", self.job["video_outputs"][0])
        self.assertIn("cleanup EIO", self.job["watcher_cleanup_warning"])
        self.restart.assert_not_awaited()

    async def test_watchdog_is_bound_to_the_service_attempt(self):
        self.job.update(render_watchdog_prompt_id="previous-id", render_watchdog_last_progress_at=1,
                        render_timeout_prompt_id="previous-id", render_hard_timeout_deadline=2,
                        render_hard_restart_state="completed")
        task, process = await self.start_watcher()
        self.assertEqual(self.job["render_watchdog_prompt_id"], "owned-render-id")
        self.assertEqual(self.job["render_watchdog_last_progress_at"], 1000)
        await self.pulse(process, 1)
        await asyncio.sleep(.01)
        self.restart.assert_not_awaited()
        self.cancel.assert_not_awaited()
        await self.stop_watcher(task, process)

    async def test_cleanup_failure_does_not_mask_primary_cancellation(self):
        task, process = await self.start_watcher()
        cleanup = self.m._terminate_subprocess

        async def failing_cleanup(child, **options):
            await cleanup(child, **options)
            raise OSError("secondary cleanup EIO")

        with patch.object(self.m, "_terminate_subprocess", side_effect=failing_cleanup):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError) as cancelled:
                await task
        self.assertIn("secondary cleanup EIO", " ".join(cancelled.exception.__notes__))
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    async def test_repeated_cancellation_reaps_term_ignoring_watcher_before_propagating(self):
        self.watcher_code = "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + WATCHER
        task, process = await self.start_timeout()
        cleanup_waiting, release = asyncio.Event(), asyncio.Event()
        real_wait, cleanup = process.wait, self.m._terminate_subprocess
        waits = 0

        async def waiting():
            nonlocal waits
            waits += 1
            if waits == 1:
                cleanup_waiting.set()
                await release.wait()
            return await real_wait()

        async def quick_cleanup(child, **options):
            await cleanup(child, **options, grace_seconds=.2, reap_seconds=.5)

        try:
            with patch.object(process, "wait", side_effect=waiting), \
                    patch.object(self.m, "_terminate_subprocess", side_effect=quick_cleanup):
                task.cancel("primary shutdown")
                await REAL_WAIT_FOR(cleanup_waiting.wait(), 2)
                task.cancel("secondary shutdown")
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertIsNone(process.returncode)
                release.set()
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await REAL_WAIT_FOR(task, 2)
            self.assertEqual(raised.exception.args, ("primary shutdown",))
            self.assertIn("secondary shutdown", " ".join(raised.exception.__notes__))
            self.assertFalse(Path(f"/proc/{process.pid}").exists())
        finally:
            release.set()

    async def test_cancellation_during_settled_success_cleanup_is_not_swallowed(self):
        task, process = await self.start_timeout()
        self.m.fetch_history.return_value = {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"video": {"videos": [{"filename": "settled.mp4", "type": "output"}]}},
        }
        cleanup_waiting, release = asyncio.Event(), asyncio.Event()
        real_wait = process.wait

        async def waiting():
            cleanup_waiting.set()
            await release.wait()
            return await real_wait()

        try:
            with patch.object(process, "wait", side_effect=waiting):
                await self.pulse(process, 30)
                await REAL_WAIT_FOR(cleanup_waiting.wait(), 2)
                task.cancel("shutdown after confirmed success")
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await REAL_WAIT_FOR(task, 2)
            self.assertEqual(raised.exception.args, ("shutdown after confirmed success",))
            self.assertEqual(self.job["status"], "completed")
            self.assertIn("settled.mp4", self.job["video_outputs"][0])
            self.assertFalse(Path(f"/proc/{process.pid}").exists())
            self.restart.assert_not_awaited()
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
