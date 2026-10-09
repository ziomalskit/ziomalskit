#!/usr/bin/env python3
"""Read-only service checks and exact API conversion; never submits a prompt."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import uuid

try:
    from .preflight import get_json, service_urls
except ImportError:
    from preflight import get_json, service_urls


def all_nodes(document):
    yield from document.get("nodes", [])
    for subgraph in document.get("definitions", {}).get("subgraphs", []):
        yield from all_nodes(subgraph)


def patched_workflows(module):
    images = [f"__h3_preflight_{i}.png" for i in range(1, 7)]
    for name in images:
        if not (module.COMFY_INPUT_DIR / name).is_file():
            raise RuntimeError(f"Missing preflight image: {name}")
    workflows = {}
    for profile in module.PRODUCTION["profiles"]:
        for phase in ("prompt", "render"):
            job = {
                "id": "smoke-" + uuid.uuid4().hex, "profile": profile, "active_service": phase,
                "render_seed": 12345, "prompt_seed": 67890, "analysis_seed": 24680,
                "context": {
                    "prompt": "smoke test", "model": profile, "profile": profile, "duration_seconds": 20,
                    "soft_timeout_minutes": 8, "hard_restart_after_seconds": 90,
                    "pictures": images, "audio": [], "loras": [],
                },
            }
            # Local lookup only; never publish a batch or write queue state.
            module.queue.append(job)
            path = module.patch_workflow(job, module.production_workflow(job),
                                         approved_prompt="smoke final prompt" if phase == "render" else None)
            nodes = {node["id"]: node for node in all_nodes(json.loads(path.read_text()))}
            assert nodes[1854]["widgets_values"][0] == 12345
            assert nodes[6067]["widgets_values"][0] == 12346
            assert nodes[7360]["widgets_values"][0] == 24680
            control = nodes[4022]
            for key, index, value in (("value_4", 5, 24680), ("value_4_1", 9, 24681),
                                     ("value_4_2", 13, 67890), ("value_4_3", 17, 67891)):
                assert control["widgets_values_named"][key] == value
                assert control["widgets_values"][index] == value
            assert nodes[3423]["widgets_values_named"]["value_3"] is False
            workflows[(profile, phase)] = path
    return workflows


async def validate_without_submission(module, service, path):
    preview = await module.run_cli_envelope(service, "run", "--workflow", str(path), "--print-prompt")
    prepared = await module._prepare_workflow_api(path, service, preview_envelope=preview)
    # Independently check the exported API file that an operator will inspect,
    # against the captured catalog; validation does not contact /prompt.
    api_path = path.with_suffix(".api.json")
    assert json.loads(api_path.read_text()) == prepared
    result = await module.run_cli_envelope(
        service,"workflow","validate","--workflow",str(api_path),
        "--input", str(path.with_suffix(".catalog.json")),
    )
    verdict = result.get("data") or {}
    if verdict.get("valid") is not True or verdict.get("error_count") != 0:
        raise RuntimeError(f"{service}: exported API failed validation")
    print(f"{service}: {len(prepared)} API nodes validated; no submission")


async def run_smoke(module, environment):
    config = get_json(service_urls(environment)["panel"] + "/api/config", environment, auth=True)
    assert config["batch"] == {"prompts_per_batch": 10, "auto_approved": 5, "review": 5}
    original_queue = list(module.queue)
    try:
        for (profile, service), path in patched_workflows(module).items():
            print(profile, service)
            await validate_without_submission(module, service, path)
    finally:
        module.queue[:] = original_queue
    print("SMOKE/CONVERSION PASSED — no real render submitted")


def main():
    panel = Path(os.getenv("PANEL_ROOT") or "/workspace/H3_VAST_MOBILE")
    sys.path.insert(0, str(panel))
    module = importlib.import_module("app.main")
    asyncio.run(run_smoke(module, dict(os.environ)))


if __name__ == "__main__":
    main()
