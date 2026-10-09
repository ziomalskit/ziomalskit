"""Bounded Parallel scheduling without real ComfyUI or paid submissions."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from tests.helpers import load_controller
from tests.test_controller import history
from tests.test_submission import fixture_job


def candidate(index, batch=1, status="prompt_queued"):
    return {"id": f"{batch}-{index}", "batch_seq": batch, "candidate_index": index,
            "created_at": batch, "review_required": index > 5, "status": status}


class PrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def check_target(self, target):
        with load_controller() as m:
            m.H3_PROMPT_PREFETCH = target
            m.queue[:] = [candidate(index) for index in range(1, 11)]
            for index in range(1, target + 1):
                job = m.pick_next_prompt_job()
                self.assertEqual(job["candidate_index"], index)
                job.update(status="render_queued_auto", prompt_ready_at=index)
            self.assertIsNone(m.pick_next_prompt_job())
            self.assertEqual(m.prompt_prefetch_status(), {"target": target, "ready": target, "preparing": 0, "buffer": target})
            render = m.pick_next_render_job()
            render.update(status="render_running", active_service="render")
            next_prompt = m.pick_next_prompt_job()
            self.assertEqual(next_prompt["candidate_index"], target + 1)
            next_prompt.update(status="prompt_preparing", active_service="prompt")
            self.assertEqual(m.prompt_prefetch_status()["buffer"], target)
            self.assertIsNone(m.pick_next_prompt_job())
            self.assertIsNone(m.pick_next_render_job())

    async def test_target_1(self):
        await self.check_target(1)

    async def test_target_2(self):
        await self.check_target(2)

    async def test_target_3(self):
        await self.check_target(3)

    async def test_config_default_clamping_and_invalid_values(self):
        with load_controller() as m:
            for value, expected in ((None, 3), ("", 3), ("garbage", 3), ("2.5", 3),
                                    ("0", 1), ("-9", 1), ("99", 5), ("2", 2)):
                self.assertEqual(m.prompt_prefetch_target(value), expected)
            self.assertEqual((await m.config())["prompt_prefetch"], 3)

    async def test_all_auto_candidates_outrank_review_across_batches(self):
        with load_controller() as m:
            old_review, new_auto = candidate(6), candidate(1, batch=2)
            m.queue[:] = [old_review, new_auto]
            self.assertIs(m.pick_next_prompt_job(), new_auto)
            new_auto["status"] = "render_queued_auto"
            self.assertIs(m.pick_next_prompt_job(), old_review)

    async def test_all_review_candidates_eventually_generated_and_do_not_fill_render_buffer(self):
        with load_controller() as m:
            m.H3_PROMPT_PREFETCH = 1
            m.queue[:] = [candidate(index) for index in range(1, 11)]
            seen = []
            while job := m.pick_next_prompt_job():
                seen.append(job["candidate_index"])
                job["status"] = "pending_review" if job["review_required"] else "completed"
            self.assertEqual(seen, list(range(1, 11)))
            self.assertEqual(m.prompt_prefetch_status()["buffer"], 0)
            self.assertTrue(m.queue_finished_for_shutdown())

    async def test_approved_reviews_consume_buffer_and_retain_top_render_priority(self):
        with load_controller() as m:
            m.H3_PROMPT_PREFETCH = 2
            auto, review, pending = candidate(1, status="render_queued_auto"), candidate(6, status="render_queued_review"), candidate(2)
            m.queue[:] = [auto, review, pending]
            self.assertIsNone(m.pick_next_prompt_job())
            self.assertIs(m.pick_next_render_job(), review)
            review["status"] = "render_running"
            self.assertIs(m.pick_next_prompt_job(), pending)

    async def test_draining_preserves_auto_work_and_skips_unstarted_reviews(self):
        with load_controller() as m:
            for plan in ("stop_after_queue", "destroy_after_queue_keep_data"):
                with self.subTest(plan=plan):
                    m.H3_PROMPT_PREFETCH = 1
                    m.vast_control["plan"] = plan
                    auto, review = candidate(1), candidate(6)
                    m.queue[:] = [auto, review, candidate(7, status="pending_review"), candidate(8, status="rejected")]
                    self.assertEqual(m.skip_unstarted_review_prompts_for_shutdown(), 1)
                    self.assertIs(m.pick_next_prompt_job(), auto)
                    auto["status"] = "render_queued_auto"
                    self.assertIsNone(m.pick_next_prompt_job())
                    self.assertFalse(m.queue_finished_for_shutdown())
                    auto["status"] = "completed"
                    self.assertTrue(m.queue_finished_for_shutdown())
                    self.assertIsNone(m.pick_next_prompt_job())

    async def test_stop_current_and_recovery_fences_survive_prefetch(self):
        with load_controller() as m:
            m.queue[:] = [candidate(1), candidate(2, status="render_queued_auto")]
            m.vast_control["plan"] = "stop_after_current"
            self.assertIsNone(m.pick_next_prompt_job()); self.assertIsNone(m.pick_next_render_job())
            m.vast_control["plan"] = "none"
            prior = candidate(3, status="recovery_render")
            m.queue.append(prior)
            self.assertIsNone(m.pick_next_render_job())
            self.assertIs(m.pick_next_prompt_job(), m.queue[0])
            prior["status"] = "recovery_prompt"
            self.assertIsNone(m.pick_next_prompt_job())

    async def test_render_and_prompt_execution_overlap_with_one_dispatch_each(self):
        with load_controller() as m:
            render = fixture_job(m)
            render.update(id="render", status="render_queued_auto", final_h3_prompt="scene",
                          batch_seq=1, candidate_index=1, created_at=1)
            prompt = {**render, "id": "prompt", "status": "prompt_queued", "candidate_index": 2}
            next_render = {**render, "id": "next-render", "candidate_index": 3}
            m.queue[:] = [render, prompt, next_render]
            started = {service: asyncio.Event() for service in ("render", "prompt")}
            release = asyncio.Event()

            async def dispatch(_prepared, _workflow, _service, pid):
                return pid

            async def watch(_job, _pid, service):
                started[service].set()
                await release.wait()
                return {"ok": True}

            async def fetch(service, _pid):
                entry = history(video=service == "render")
                if service == "prompt":
                    final = str(m.MAP["prompt_capture"]["fallback_top_level_final_node_id"])
                    entry["outputs"] = {final: {"text": ["next scene"]}}
                return entry

            with patch.object(m, "patch_workflow", return_value=m.MASTER), \
                 patch.object(m, "_prepare_workflow_api", AsyncMock(return_value={})), \
                 patch.object(m, "_dispatch_prepared_workflow", side_effect=dispatch) as post, \
                 patch.object(m, "watch_prompt", side_effect=watch), \
                 patch.object(m, "fetch_history", side_effect=fetch):
                tasks = [asyncio.create_task(m.render_job(render)), asyncio.create_task(m.generate_prompt_candidate(prompt))]
                try:
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 2)
                    self.assertEqual(render["status"], "render_running")
                    self.assertEqual(prompt["status"], "prompt_running")
                    self.assertIsNone(m.pick_next_render_job())
                    self.assertEqual(post.await_count, 2)
                finally:
                    release.set()
                    await asyncio.gather(*tasks)
            self.assertEqual(render["status"], "completed")
            self.assertEqual(prompt["status"], "render_queued_auto")
            self.assertEqual({call.args[2] for call in post.await_args_list}, {"render", "prompt"})

    async def test_buffer_is_reconstructed_from_durable_queue_after_restart(self):
        with load_controller() as m:
            m.H3_PROMPT_PREFETCH = 2
            m.queue[:] = [candidate(1, status="render_queued_auto"), candidate(2, status="render_queued_auto"), candidate(3)]
            m.save_state(); m.queue.clear(); m.load_state()
            self.assertEqual(m.prompt_prefetch_status()["ready"], 2)
            self.assertIsNone(m.pick_next_prompt_job())

    async def test_immediate_prompt_completion_does_not_starve_available_renderer(self):
        with load_controller() as m:
            m.queue[:] = [candidate(index) for index in range(1, 11)]
            events, rendered = [], asyncio.Event()

            async def prompt(job):
                events.append(("prompt", job["candidate_index"]))
                job["status"] = "pending_review" if job["review_required"] else "render_queued_auto"

            async def render(job):
                events.append(("render", job["candidate_index"]))
                job["status"] = "completed"
                rendered.set()

            with patch.object(m, "generate_prompt_candidate", side_effect=prompt), \
                 patch.object(m, "render_job", side_effect=render):
                tasks = [asyncio.create_task(m.prompt_worker()), asyncio.create_task(m.render_worker())]
                try:
                    await asyncio.wait_for(rendered.wait(), 1)
                    self.assertEqual(events[:2], [("prompt", 1), ("render", 1)])
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def test_approval_during_prompt_preparation_defers_before_post(self):
        with load_controller() as m:
            m.H3_PROMPT_PREFETCH = 1
            prompt = fixture_job(m)
            prompt.update(status="prompt_queued", batch_seq=1, candidate_index=1, created_at=1)
            review = {**prompt, "id": "review", "status": "pending_review", "review_required": True,
                      "candidate_index": 6, "final_h3_prompt": "review scene"}
            m.queue[:] = [prompt, review]
            entered, release = asyncio.Event(), asyncio.Event()

            async def prepare(*_args):
                entered.set(); await release.wait(); return {}

            with patch.object(m, "patch_workflow", return_value=m.PROMPT_ONLY), \
                 patch.object(m, "_prepare_workflow_api", side_effect=prepare), \
                 patch.object(m, "_dispatch_prepared_workflow", AsyncMock()) as post:
                task = asyncio.create_task(m.generate_prompt_candidate(prompt))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    await m.approve("review", m.ApprovalRequest())
                    self.assertEqual(m.prompt_prefetch_status()["ready"], 1)
                finally:
                    release.set()
                    await task
            post.assert_not_awaited()
            self.assertEqual(prompt["status"], "prompt_queued")
            self.assertEqual(prompt["prompt_submission_state"], "deferred")
            self.assertIsNone(m.pick_next_prompt_job())
