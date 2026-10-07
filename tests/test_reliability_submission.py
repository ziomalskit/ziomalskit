"""Pre-dispatch storage failures retain the known absence of remote effects."""
from __future__ import annotations

import asyncio
import errno
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import load_controller
from tests.test_controller import history


def queued_job(service, identifier, candidate=1):
    return {
        "id": identifier, "status": "prompt_queued" if service == "prompt" else "render_queued_auto",
        "batch_seq": 1, "candidate_index": candidate, "created_at": candidate,
        "review_required": service == "prompt", "final_h3_prompt": "approved reference scene",
        "context": {"soft_timeout_minutes": 8, "hard_restart_after_seconds": 90},
    }


class PredispatchPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_shot_real_file_fsync_failure_resumes_both_jobs_exactly_once(self):
        for service in ("prompt", "render"):
            with self.subTest(service=service), load_controller() as m:
                first, second = queued_job(service, "first"), queued_job(service, "second", 2)
                m.queue[:] = [first, second]
                m.save_state()
                real_fsync, failed, dispatched = os.fsync, False, []

                def fsync(fd):
                    nonlocal failed
                    if not failed and first.get(service + "_submission_state") == "armed" and ".queue.json." in os.readlink(f"/proc/self/fd/{fd}"):
                        failed = True
                        self.assertEqual(dispatched, [])
                        raise OSError(errno.EIO, "one-shot pre-dispatch file fsync EIO")
                    return real_fsync(fd)

                async def dispatch(_prepared, _workflow, _service, prompt_id):
                    stored = json.loads(m.QUEUE_FILE.read_text())["queue"]
                    submitted = next(job for job in stored if job.get(service + "_prompt_id") == prompt_id)
                    self.assertEqual(submitted[service + "_submission_state"], "armed")
                    self.assertEqual(next(job for job in m.queue if job["id"] == submitted["id"])[service + "_submission_state"], "attempting")
                    dispatched.append((submitted["id"], prompt_id))
                    return prompt_id

                result = history(video=True) if service == "render" else {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"5732": {"text": ["generated reference scene"]}},
                }
                with patch.object(os, "fsync", side_effect=fsync), \
                     patch.object(m, "patch_workflow", return_value=m.MASTER), \
                     patch.object(m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                     patch.object(m, "_dispatch_prepared_workflow", side_effect=dispatch), \
                     patch.object(m, "watch_prompt", AsyncMock(return_value={"ok": True})), \
                     patch.object(m, "fetch_history", AsyncMock(return_value=result)):
                    worker = asyncio.create_task(getattr(m, service + "_worker")())
                    setattr(m, service + "_worker_task", worker)
                    m.controller_started = True
                    expected = "completed" if service == "render" else "pending_review"
                    try:
                        async with asyncio.timeout(5):
                            while any(job["status"] != expected for job in m.queue):
                                if worker.done():
                                    worker.result()
                                await asyncio.sleep(.01)
                        self.assertTrue(failed)
                        self.assertEqual([identifier for identifier, _pid in dispatched], ["first", "second"])
                        self.assertEqual(len({pid for _identifier, pid in dispatched}), 2)
                        self.assertIsNone(m.queue_persistence_error)
                        self.assertFalse(m.service_recovering(service))
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)

    async def test_permanent_arm_write_failure_is_safe_to_requeue_after_storage_repair(self):
        with load_controller() as m:
            job = queued_job("render", "first")
            m.queue[:] = [job, queued_job("render", "second", 2)]
            m.save_state()
            real_fsync = os.fsync

            def fsync(fd):
                if job.get("render_submission_state") in {"armed", "prepared"} and ".queue.json." in os.readlink(f"/proc/self/fd/{fd}"):
                    raise OSError(errno.ENOSPC, "persistent pre-dispatch file fsync failure")
                return real_fsync(fd)

            with patch.object(os, "fsync", side_effect=fsync), \
                 patch.object(m, "patch_workflow", return_value=m.MASTER), \
                 patch.object(m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
                with self.assertRaises(OSError) as failure:
                    await m.render_job(job)
                self.assertEqual(failure.exception.errno, errno.ENOSPC)
                m._handle_worker_error(job, "render", failure.exception)
                dispatch.assert_not_awaited()
            self.assertIsNotNone(m.queue_persistence_error)
            self.assertIsNone(m.pick_next_render_job())
            self.assertEqual(job["status"], "render_queued_auto")
            self.assertNotIn("render_prompt_id", job)
            self.assertNotIn("render_submission_attempted", job)
            self.assertFalse(m.service_recovering("render"))
            m._resume_queue_persistence()
            self.assertIsNone(m.queue_persistence_error)
            self.assertIs(m.pick_next_render_job(), job)
            # A restart after the repair sees a known unsubmitted queue, too.
            with load_controller() as restarted:
                restarted.QUEUE_FILE = m.QUEUE_FILE
                restarted.load_state()
                self.assertEqual(restarted.queue[0]["status"], "render_queued_auto")
                self.assertFalse(restarted.service_recovering("render"))
                self.assertEqual(restarted.pick_next_render_job()["id"], "first")
                async def accept(_prepared, _workflow, _service, prompt_id):
                    return prompt_id
                with patch.object(restarted, "_prepare_workflow_api", AsyncMock(return_value={})), \
                     patch.object(restarted, "_dispatch_prepared_workflow", AsyncMock(side_effect=accept)) as post:
                    await restarted._submit_workflow(restarted.queue[0], restarted.MASTER, "render")
                    restarted._apply_history_outcome(restarted.queue[0], "render", history(video=True))
                    post.assert_awaited_once()
                with load_controller() as settled_restart:
                    settled_restart.QUEUE_FILE = m.QUEUE_FILE
                    settled_restart.load_state()
                    self.assertEqual(settled_restart.queue[0]["status"], "completed")
                    self.assertFalse(settled_restart.service_recovering("render"))
                    self.assertEqual(settled_restart.pick_next_render_job()["id"], "second")

    async def test_post_replace_directory_fsync_failure_rolls_back_live_state_but_boot_fails_closed(self):
        with load_controller() as m:
            job = queued_job("render", "first")
            m.queue[:] = [job]
            m.save_state()
            real_fsync, failed = os.fsync, False

            def fsync(fd):
                nonlocal failed
                if not failed and job.get("render_submission_state") == "armed" and os.readlink(f"/proc/self/fd/{fd}") == str(m.STATE):
                    failed = True
                    raise OSError(errno.EIO, "post-replace directory fsync EIO")
                return real_fsync(fd)

            with patch.object(os, "fsync", side_effect=fsync), \
                 patch.object(m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
                with self.assertRaises(OSError) as failure:
                    await m._submit_workflow(job, m.MASTER, "render")
                dispatch.assert_not_awaited()
            self.assertTrue(failed)
            self.assertEqual(job["render_submission_state"], "prepared")
            self.assertNotIn("render_prompt_id", job)
            disk = json.loads(m.QUEUE_FILE.read_text())["queue"][0]
            self.assertEqual(disk["render_submission_state"], "armed")
            # A fresh process cannot know whether the arm was followed by POST.
            # It must never turn an absent remote id into automatic resubmission.
            with load_controller() as restarted:
                restarted.QUEUE_FILE = m.QUEUE_FILE
                restarted.load_state()
                restored = restarted.queue[0]
                with patch.object(restarted, "wait_service_ready", AsyncMock(return_value=True)), \
                     patch.object(restarted, "fetch_history", AsyncMock(return_value={})), \
                     patch.object(restarted, "fetch_service_queue", AsyncMock(return_value={"queue_running": [], "queue_pending": []})), \
                     patch.object(restarted, "_dispatch_prepared_workflow", AsyncMock()) as repost:
                    await restarted._recover_one_job(restored, "render")
                    repost.assert_not_awaited()
                self.assertEqual(restored["status"], "recovery_render")
            # The original live process still has authoritative no-POST knowledge.
            m._handle_worker_error(job, "render", failure.exception)
            self.assertEqual(job["status"], "render_queued_auto")
            self.assertIs(m.pick_next_render_job(), job)
            self.assertIsNone(m.queue_persistence_error)

    async def test_validation_failure_remains_terminal_and_is_not_requeued(self):
        with load_controller() as m:
            job = queued_job("render", "invalid")
            m.queue[:] = [job]
            with patch.object(m, "patch_workflow", return_value=m.MASTER), \
                 patch.object(m, "_prepare_workflow_api", AsyncMock(side_effect=ValueError("invalid workflow"))), \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as dispatch:
                with self.assertRaises(ValueError) as failure:
                    await m.render_job(job)
                m._handle_worker_error(job, "render", failure.exception)
                dispatch.assert_not_awaited()
            self.assertEqual(job["status"], "render_failed")
            self.assertFalse(m.service_recovering("render"))


if __name__ == "__main__":
    unittest.main()
