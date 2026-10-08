"""Durable request replay, lifecycle generations, terminal EIO and PID cleanup."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.helpers import load_controller
from tests.test_controller import history
from h3.scripts import process_identity


def request(m, key=None):
    m.COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    pictures = [f"final-ref-{i}.png" for i in range(6)]
    for name in pictures + ["__h3_silence_1s.wav"]:
        (m.COMFY_INPUT_DIR / name).write_bytes(b"CPU fixture")
    model = m.model_path_for_preset("native_int8")
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"CPU fixture")
    return m.BatchRequest(request_id=key or uuid.uuid4(), prompt="confirmed scene", pictures=pictures)


def prior_review(m, req):
    job = dict(id="review", batch_id="prior", batch_seq=1, candidate_index=6, created_at=1,
               status="pending_review", review_required=True, final_h3_prompt="confirmed scene",
               render_seed=1, prompt_seed=2, analysis_seed=3, context=req.model_dump(mode="json"))
    m.queue.append(job)
    m.batches.append(dict(id="prior", seq=1))
    m.save_state()
    return job


def remote(m, calls):
    stack = ExitStack()

    async def post(_prepared, _workflow, service, pid):
        persisted = json.loads(m.QUEUE_FILE.read_text())["queue"]
        assert any(j.get(service + "_prompt_id") == pid for j in persisted)
        calls.append((service, pid))
        return pid

    async def terminal(service, _pid, **_options):
        return history(video=True) if service == "render" else {
            "status": {"status_str": "success", "completed": True},
            "prompt": {"5732": {"class_type": "PreviewAny", "inputs": {}}},
            "outputs": {"5732": {"text": ["confirmed terminal prompt"]}}}

    stack.enter_context(patch.object(m, "_prepare_workflow_api", AsyncMock(return_value={})))
    stack.enter_context(patch.object(m, "_dispatch_prepared_workflow", side_effect=post))
    stack.enter_context(patch.object(m, "watch_prompt", AsyncMock(return_value={"ok": True})))
    stack.enter_context(patch.object(m, "fetch_history", side_effect=terminal))
    return stack


async def run_workers(m, completed, calls):
    with remote(m, calls):
        tasks = [asyncio.create_task(m.prompt_worker()), asyncio.create_task(m.render_worker())]
        m.prompt_worker_task, m.render_worker_task = tasks
        m.controller_started = True
        try:
            async with asyncio.timeout(20):
                while sum(j["status"] == "completed" for j in m.queue) != completed:
                    for task in tasks:
                        if task.done():
                            task.result()
                    await asyncio.sleep(.01)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def crash_child(state, key, window, approval=False):
    with load_controller() as m:
        req = request(m, key)
        m.STATE, m.QUEUE_FILE = state, state / "queue.json"
        m.QUEUE_PUBLICATION_FILE = state / "queue.publication.json"
        if approval:
            prior_review(m, req)
        else:
            m.save_state()
        real_replace, real_write = os.replace, m._atomic_json_write

        def kill():
            os.kill(os.getpid(), signal.SIGKILL)

        def replace(src, dst):
            if Path(dst) == m.QUEUE_FILE and window == "before_replace":
                kill()
            real_replace(src, dst)
            if Path(dst) == m.QUEUE_FILE and window == "after_replace":
                kill()

        def write(path, payload):
            real_write(path, payload)
            if path == m.QUEUE_FILE and window == "after_durable":
                kill()

        with patch.object(os, "replace", side_effect=replace), patch.object(m, "_atomic_json_write", side_effect=write):
            asyncio.run(m.approve("review", m.ApprovalRequest()) if approval else m.create_batches(req))
            if window == "before_response":
                kill()
        raise AssertionError("crash boundary was not reached")


class ControllerFixture:
    def setUp(self):
        self.fixture = load_controller()
        self.m = self.fixture.__enter__()
        self.req = request(self.m)

    def tearDown(self):
        self.fixture.__exit__(None, None, None)


class FinalMutationTests(ControllerFixture, unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_fails_closed(self):
        req = self.req.model_copy(update={"request_id": None})
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.create_batches(req)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.m.queue, [])

    async def test_same_key_concurrent_retry_and_changed_payload_conflict(self):
        results = await asyncio.gather(*(self.m.create_batches(self.req) for _ in range(20)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.m.queue), 10)
        self.assertEqual(len(self.m.batches), 1)
        with self.assertRaises(self.m.HTTPException) as error:
            await self.m.create_batches(self.req.model_copy(update={"prompt": "different scene"}))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.m.queue), 10)
        self.assertEqual(json.loads(self.m.QUEUE_FILE.read_text())["requests"][str(self.req.request_id)]["result"], results[0])

    async def test_identical_body_different_keys_creates_two_batches(self):
        first = await self.m.create_batches(self.req)
        second = await self.m.create_batches(self.req.model_copy(update={"request_id": uuid.uuid4()}))
        self.assertNotEqual(first, second)
        calls = []
        await run_workers(self.m, 10, calls)
        self.assertEqual(len([pid for service, pid in calls if service == "render"]), 10)

    async def test_restart_replay_does_not_require_assets_or_mutate_armed_queue(self):
        result = await self.m.create_batches(self.req)
        self.m.load_state()
        for name in self.req.pictures:
            (self.m.COMFY_INPUT_DIR / name).unlink()
        await self.m.api_vast_action(self.m.VastActionRequest(action="stop_after_current"))
        self.assertEqual(await self.m.create_batches(self.req), result)
        self.assertEqual(len(self.m.queue), 10)

    async def test_sigkill_at_four_commit_windows_retries_one_batch_five_renders(self):
        for window in ("before_replace", "after_replace", "after_durable", "before_response"):
            with self.subTest(window=window), tempfile.TemporaryDirectory(prefix="aj-final-crash-") as tmp, load_controller() as m:
                state, key = Path(tmp), uuid.uuid4()
                child = subprocess.run([sys.executable, __file__, "--crash", str(state), str(key), window], capture_output=True, timeout=15)
                self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode())
                req = request(m, key)
                m.STATE, m.QUEUE_FILE, m.QUEUE_PUBLICATION_FILE = state, state / "queue.json", state / "queue.publication.json"
                m.load_state()
                first = await m.create_batches(req)
                self.assertEqual(await m.create_batches(req), first)
                self.assertEqual(len(m.queue), 10)
                self.assertEqual(len(m.batches), 1)
                calls = []
                await run_workers(m, 5, calls)
                renders = [pid for service, pid in calls if service == "render"]
                self.assertEqual(len(renders), 5)
                self.assertEqual(len(set(renders)), 5)
                m.load_state()
                self.assertEqual(await m.create_batches(req), first)
                self.assertEqual(len(m.queue), 10)

    async def test_approval_crash_and_retry_never_schedules_second_render(self):
        for window in ("before_replace", "after_replace", "after_durable", "before_response"):
            with self.subTest(window=window), tempfile.TemporaryDirectory(prefix="aj-final-approval-") as tmp, load_controller() as m:
                state, key = Path(tmp), uuid.uuid4()
                child = subprocess.run([sys.executable, __file__, "--crash", str(state), str(key), window, "approval"], capture_output=True, timeout=15)
                self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode())
                request(m, key)
                m.STATE, m.QUEUE_FILE, m.QUEUE_PUBLICATION_FILE = state, state / "queue.json", state / "queue.publication.json"
                m.load_state()
                result = await m.approve("review", m.ApprovalRequest())
                self.assertEqual(await m.approve("review", m.ApprovalRequest()), result)
                calls = []
                await run_workers(m, 1, calls)
                self.assertEqual([service for service, _pid in calls], ["render"])
                self.assertEqual(await m.approve("review", m.ApprovalRequest()), result)
                with self.assertRaises(m.HTTPException) as error:
                    await m.approve("review", m.ApprovalRequest(final_prompt="a new approval"))
                self.assertEqual(error.exception.status_code, 409)

    async def test_corrupt_ledger_fails_closed_without_overwrite(self):
        await self.m.create_batches(self.req)
        raw = json.loads(self.m.QUEUE_FILE.read_text())
        raw["requests"][str(self.req.request_id)]["result"]["created_batches"] = ["missing"]
        data = json.dumps(raw)
        self.m.QUEUE_FILE.write_text(data)
        self.m.load_state()
        self.assertFalse(self.m.state_diagnostics()["ready"])
        with self.assertRaises(self.m.HTTPException):
            await self.m.create_batches(self.req)
        self.assertEqual(self.m.QUEUE_FILE.read_text(), data)


class FinalLifecycleAndSuccessTests(ControllerFixture, unittest.IsolatedAsyncioTestCase):
    async def test_all_armed_plans_invalidated_until_explicit_rearm(self):
        for plan in ("stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data"):
            with self.subTest(plan=plan), load_controller() as m:
                calls, done = [], asyncio.Event()
                schedule = m._schedule_instance_action

                async def cli(*args):
                    calls.append(args)
                    done.set()
                    return "inert"

                with patch.object(m, "persistent_storage_status", return_value={"safe_for_destroy_keep_data": True}), \
                     patch.object(m, "instance_id_from_env", return_value="inert"), patch.object(m, "run_vast_cli", side_effect=cli), \
                     patch.object(m, "_schedule_instance_action", side_effect=lambda action, delay=1.5: schedule(action, 0)):
                    await m.api_vast_action(m.VastActionRequest(action=plan))
                    generation = m.vast_control["armed_generation"]
                    with patch.object(os, "fsync", side_effect=OSError(errno.EIO, "armed EIO")):
                        with self.assertRaises(OSError):
                            m.save_state()
                    self.assertEqual(m.vast_control["plan"], "action_failed")
                    m._resume_queue_persistence()
                    self.assertFalse(m.state_diagnostics()["ready"])
                    self.assertGreater(m.queue_persistence_epoch, generation)
                    with patch.object(m.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())):
                        with self.assertRaises(asyncio.CancelledError):
                            await m.vast_guard_worker()
                    self.assertEqual(calls, [])
                    await m.api_vast_action(m.VastActionRequest(action=plan))
                    guard = asyncio.create_task(m.vast_guard_worker())
                    m.vast_guard_task = guard
                    m.controller_started = True
                    try:
                        await asyncio.wait_for(done.wait(), 5)
                        self.assertEqual(len(calls), 1)
                    finally:
                        guard.cancel()
                        m.lifecycle_action_task.cancel()
                        await asyncio.gather(guard, m.lifecycle_action_task, return_exceptions=True)

    async def test_restart_never_continues_previously_armed_plan(self):
        for plan in ("stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data"):
            with self.subTest(plan=plan):
                self.m.vast_control.update(plan=plan, armed_generation=self.m.queue_persistence_epoch)
                self.m.save_vast_control()
                loaded = self.m.load_vast_control()
                self.assertEqual(loaded["plan"], "action_interrupted")

    async def test_stale_armed_generation_blocks_guard_before_executing(self):
        await self.m.api_vast_action(self.m.VastActionRequest(action="stop_after_queue"))
        self.m.vast_control["armed_generation"] -= 1
        with patch.object(self.m, "_schedule_instance_action") as schedule, \
             patch.object(self.m.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.m.vast_guard_worker()
        schedule.assert_not_called()
        self.assertEqual(self.m.vast_control["plan"], "action_failed")

    async def terminal_success(self, service):
        job = prior_review(self.m, self.req)
        job.update(review_required=False, status="prompt_queued" if service == "prompt" else "render_queued_auto")
        self.m.save_state()
        calls, fired = [], False
        real_fsync = os.fsync

        def sync(fd):
            nonlocal fired
            if not fired and ".queue.json." in os.readlink(f"/proc/self/fd/{fd}") and job.get(service + "_submission_state") == "settled":
                fired = True
                raise OSError(errno.EIO, "terminal one-shot EIO")
            real_fsync(fd)

        with remote(self.m, calls), patch.object(os, "fsync", side_effect=sync):
            task = asyncio.create_task(getattr(self.m, service + "_worker")())
            setattr(self.m, service + "_worker_task", task)
            self.m.controller_started = True
            try:
                async with asyncio.timeout(10):
                    while self.m.queue_persistence_error is None:
                        await asyncio.sleep(.01)
                self.assertFalse(self.m.state_diagnostics()["ready"])
                self.assertFalse(task.done())
                expected = "render_queued_auto" if service == "prompt" else "completed"
                self.assertEqual(job["status"], expected)
                async with asyncio.timeout(10):
                    while self.m.queue_persistence_error:
                        await asyncio.sleep(.01)
                self.assertTrue(self.m.state_diagnostics()["ready"])
                self.assertEqual(json.loads(self.m.QUEUE_FILE.read_text())["queue"][0]["status"], expected)
                self.assertEqual([s for s, _pid in calls], [service])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # A stopped task rightly blocks dispatch; the fixture's other
                # workers model the healthy controller for the next phase.
                self.m.controller_started = False
        if service == "prompt":
            self.m.prompt_worker_task = self.m.recovery_task
            await run_workers(self.m, 1, calls)
            self.assertEqual([s for s, _pid in calls], ["prompt", "render"])

    async def test_confirmed_prompt_success_survives_eio_and_resumes_render_once(self):
        await self.terminal_success("prompt")

    async def test_confirmed_render_success_survives_eio_without_resubmission(self):
        await self.terminal_success("render")


class FinalPidTests(unittest.TestCase):
    def test_launcher_keeps_spawned_pid_owned_until_registration_even_on_fast_exit(self):
        with tempfile.TemporaryDirectory(prefix="aj-final-launch-") as tmp:
            root = Path(tmp)
            args = argparse.Namespace(pid=None, pid_file=root / "render.pid", service="render", python=sys.executable,
                                      comfy_root=str(root), panel_root=str(root), port=8188, log_file=root / "worker.log")
            original = os.pidfd_open
            opened = []
            def open_child(pid):
                # launch_process is the parent and has not reaped the child;
                # /proc remains this owned instance even if it already exited.
                stat = Path(f"/proc/{pid}/stat").read_text()
                self.assertEqual(int(stat[stat.rindex(")") + 2:].split()[1]), os.getpid())
                opened.append(pid)
                return original(pid)
            with patch.object(os, "pidfd_open", side_effect=open_child):
                with self.assertRaises((ProcessLookupError, RuntimeError)):
                    process_identity.launch_process(args, [sys.executable, "-c", "pass"])
            self.assertEqual(opened, [args.pid])
            self.assertFalse(Path(f"/proc/{args.pid}").exists())
            self.assertFalse(args.pid_file.exists())

    def test_registration_errors_at_every_stage_reap_owned_child_and_remove_pidfile(self):
        for phase in ("snapshot", "mismatch_timeout", "tempfile", "write", "replace", "file_fsync", "directory_open", "directory_fsync"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(prefix="aj-final-pid-") as tmp:
                root = Path(tmp)
                (root / "main.py").write_text("import time; time.sleep(60)\n")
                child = subprocess.Popen([sys.executable, "main.py", "--port", "8188"], cwd=root)
                args = argparse.Namespace(pid=child.pid, pid_file=root / "render.pid", service="render", python=sys.executable,
                                          comfy_root=str(root), panel_root=str(root), port=8188)
                try:
                    with ExitStack() as faults:
                        error = OSError(errno.EIO, phase)
                        if phase == "snapshot":
                            faults.enter_context(patch.object(process_identity, "process_snapshot", side_effect=error))
                        elif phase == "mismatch_timeout":
                            faults.enter_context(patch.object(process_identity, "expected_service", return_value=False))
                            faults.enter_context(patch.object(time, "monotonic", side_effect=[0, 6]))
                        elif phase == "tempfile":
                            faults.enter_context(patch.object(tempfile, "mkstemp", side_effect=error))
                        elif phase == "write":
                            faults.enter_context(patch.object(json, "dump", side_effect=error))
                        elif phase == "replace":
                            faults.enter_context(patch.object(os, "replace", side_effect=error))
                        elif phase == "directory_open":
                            real_open = os.open
                            faults.enter_context(patch.object(os, "open", side_effect=lambda path, *a, **kw:
                                (_ for _ in ()).throw(error) if Path(path) == root else real_open(path, *a, **kw)))
                        else:
                            real_fsync = os.fsync
                            def fsync(fd):
                                is_directory = os.readlink(f"/proc/self/fd/{fd}") == str(root)
                                if is_directory == (phase == "directory_fsync"):
                                    raise error
                                return real_fsync(fd)
                            faults.enter_context(patch.object(os, "fsync", side_effect=fsync))
                        faults.enter_context(patch.object(os, "kill", side_effect=AssertionError("numeric PID signalling forbidden")))
                        with self.assertRaises((OSError, RuntimeError)):
                            process_identity.record_process(args)
                    child.wait(timeout=5)
                    self.assertFalse(args.pid_file.exists())
                    self.assertEqual(list(root.glob(".service-pid-*")), [])
                finally:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)

    def test_snapshot_error_escalates_ignored_term_to_kill_on_same_pidfd(self):
        with tempfile.TemporaryDirectory(prefix="aj-final-kill-") as tmp:
            root = Path(tmp)
            ready = root / "ready"
            (root / "main.py").write_text(f"import signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath({str(ready)!r}).touch()\ntime.sleep(60)\n")
            child = subprocess.Popen([sys.executable, "main.py", "--port", "8188"], cwd=root)
            args = argparse.Namespace(pid=child.pid, pid_file=root / "render.pid", service="render", python=sys.executable,
                                      comfy_root=str(root), panel_root=str(root), port=8188)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if time.monotonic() > deadline:
                        self.fail("child did not install TERM handler")
                    time.sleep(.01)
                with patch.object(process_identity, "process_snapshot", side_effect=OSError(errno.EIO, "snapshot")), \
                     patch.object(signal, "pidfd_send_signal", wraps=signal.pidfd_send_signal) as send:
                    with self.assertRaises(OSError):
                        process_identity.record_process(args)
                child.wait(timeout=5)
                self.assertEqual(child.returncode, -signal.SIGKILL)
                terms = [(call.args[0], call.args[1]) for call in send.call_args_list if call.args[1] in (signal.SIGTERM, signal.SIGKILL)]
                self.assertEqual([sig for _fd, sig in terms], [signal.SIGTERM, signal.SIGKILL])
                self.assertEqual(terms[0][0], terms[1][0])
                self.assertFalse(args.pid_file.exists())
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash":
        crash_child(Path(sys.argv[2]), uuid.UUID(sys.argv[3]), sys.argv[4], len(sys.argv) > 5)
    else:
        unittest.main()
