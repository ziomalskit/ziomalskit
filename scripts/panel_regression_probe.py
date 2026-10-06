#!/usr/bin/env python3
"""Read-only RC5 controller audit: isolated copy, mocks, no external requests.

These are defect probes, not tests certifying the release. Confirmed means the
original defect was reproduced. The source panel and its state remain unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("panel", type=Path, help="Original panel directory")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit nonzero when a defect is reproduced")
    args = parser.parse_args()
    results = []

    def record(identifier, severity, confirmed, expected, observed, location):
        results.append({"id": identifier, "severity": severity,
                        "status": "confirmed" if confirmed else "not_reproduced",
                        "expected": expected, "observed": observed,
                        "location": location})

    with tempfile.TemporaryDirectory(prefix="aj-panel-probe-") as temporary:
        root = Path(temporary) / "panel"
        root.mkdir()
        for directory in ("app", "static", "workflows", "config"):
            shutil.copytree(args.panel / directory, root / directory)
        (root / "state").mkdir()
        os.environ["H3_PANEL_PASSWORD"] = "audit-test-only"
        spec = importlib.util.spec_from_file_location("audited_panel", root / "app/main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        gpu = module.gpu_status()
        mount = module._mount_info(Path("/"))
        missing = "subprocess" not in module.__dict__
        record("missing_subprocess_import", "high", missing and "NameError" in gpu.get("error", "") and mount == (None, None),
               "GPU inspection and filesystem verification can invoke subprocess.run",
               {"subprocess_imported": not missing, "gpu": gpu, "root_mount": mount},
               "app/main.py:2,838,933")

        module.queue[:] = [
            {"id": "prompt", "status": "prompt_queued", "review_required": False,
             "batch_seq": 1, "candidate_index": 1, "created_at": 1},
            {"id": "render", "status": "render_queued_auto",
             "batch_seq": 1, "candidate_index": 2, "created_at": 2},
        ]
        observed = {}
        for plan in ("stop_after_current", "executing_stop", "executing_destroy", "executing_idle_stop"):
            module.vast_control["plan"] = plan
            prompt = module.pick_next_prompt_job()
            render = module.pick_next_render_job()
            observed[plan] = {"prompt": prompt["id"] if prompt else None,
                              "render": render["id"] if render else None}
        record("dispatch_during_shutdown", "high",
               observed["stop_after_current"] == {"prompt": None, "render": None}
               and all(observed[p] == {"prompt": "prompt", "render": "render"}
                       for p in ("executing_stop", "executing_destroy", "executing_idle_stop")),
               "Dispatch remains blocked while a delayed lifecycle action executes",
               observed, "app/main.py:1047-1051,1097,1120")

        async def recovery_probes():
            async def ready(*_args, **_kwargs):
                return True

            async def failed_history(*_args, **_kwargs):
                return {"outputs": {"7367": {"text": ["Intermediate preview"]}},
                        "status": {"status_str": "error", "completed": False,
                                   "messages": [["execution_error", {"exception_type": "RuntimeError"}]]}}

            async def empty_queue(*_args, **_kwargs):
                return {"queue_running": [], "queue_pending": []}

            module.wait_service_ready = ready
            module.fetch_history = failed_history
            module.fetch_service_queue = empty_queue
            job = {"id": "failed", "status": "recovery_render", "render_prompt_id": "mock-id",
                   "approved_final_prompt": "Scene", "context": {}}
            await module._recover_one_job(job, "render")
            record("recovery_accepts_failed_render", "high",
                   job["status"] == "completed" and not job.get("outputs"),
                   "An execution_error history is failed and never completed with no video",
                   {"job_status": job["status"], "outputs": job.get("outputs", []),
                    "history_status": "error", "history_completed": False},
                   "app/main.py:1239-1247")

            async def unavailable_history(*_args, **_kwargs):
                raise ConnectionError("Mock history request failed")

            async def unavailable_queue(*_args, **_kwargs):
                raise ConnectionError("Mock queue request failed")

            module.fetch_history = unavailable_history
            module.fetch_service_queue = unavailable_queue
            job = {"id": "unknown", "status": "recovery_render", "render_prompt_id": "mock-id",
                   "approved_final_prompt": "Scene", "context": {}}
            await module._recover_one_job(job, "render")
            record("recovery_requeues_on_unknown_remote_state", "high",
                   job["status"] == "render_queued_review",
                   "Failed history/queue reads preserve recovery state until absence is verified",
                   {"job_status": job["status"], "both_remote_requests": "failed"},
                   "app/main.py:1224-1227,1252-1255,1293-1299")

            module.vast_control["plan"] = "none"
            module.queue[:] = [{"id": "unreconciled", "status": "recovery_render",
                               "render_prompt_id": "previous-render", "created_at": 1,
                               "batch_seq": 1, "candidate_index": 1},
                              {"id": "next", "status": "render_queued_auto", "created_at": 2,
                               "batch_seq": 1, "candidate_index": 2}]
            next_job = module.pick_next_render_job()
            record("dispatch_before_recovery_reconciliation", "medium",
                   next_job is not None and next_job["id"] == "next",
                   "New dispatch waits for unresolved execution on the same service",
                   {"unresolved_job": "recovery_render", "selected_job": next_job["id"] if next_job else None},
                   "app/main.py:1119-1131,1317-1322")

        asyncio.run(recovery_probes())

        async def lifecycle_probe():
            original_delayed = module.delayed_instance_action

            async def immediately_attempt(action, delay):
                await original_delayed(action, delay=0)

            async def failed_cli(*_args, **_kwargs):
                raise RuntimeError("Mock lifecycle failure")

            module.delayed_instance_action = immediately_attempt
            module.instance_id_from_env = lambda: "audit-mock-instance"
            module.run_vast_cli = failed_cli
            module.vast_control.update({"plan": "stop_after_current", "idle_minutes": 0,
                                        "cost_guard_usd": 0})
            module.queue[:] = []
            guard_task = asyncio.create_task(module.vast_guard_worker())
            await guard_task
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            failed_plan = module.vast_control["plan"]
            await module.api_vast_action(module.VastActionRequest(action="cancel_plan"))
            record("guard_dies_after_failed_lifecycle_action", "high",
                   guard_task.done() and failed_plan == "action_failed" and module.vast_control["plan"] == "none",
                   "Idle/cost/lifecycle guard continues after a failed instance action or cancellation",
                   {"guard_task_done": guard_task.done(), "failed_plan": failed_plan,
                    "after_cancel": module.vast_control["plan"]},
                   "app/main.py:1051,1065,1070,1079,1390-1393")

        asyncio.run(lifecycle_probe())

        authorization = "Basic " + base64.b64encode("h3:zażółć".encode()).decode()
        unicode_exception = None
        try:
            module._authorized(authorization)
        except TypeError as error:
            unicode_exception = str(error)
        record("non_ascii_basic_auth_raises", "low", unicode_exception is not None,
               "Invalid credentials return 401; supported configured passwords can authenticate",
               {"exception": unicode_exception}, "app/main.py:62")

    payload = {"source_panel": str(args.panel.resolve()), "network_calls": 0,
               "original_files_modified": False, "findings": results}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    if args.fail_on_findings and any(item["status"] == "confirmed" for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
