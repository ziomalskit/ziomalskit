#!/usr/bin/env python3
"""Export one strictly verified prompt-only request; never starts or submits work.

Run in a separate process with submissions OFF, after production provenance is
approved. Uses the production converter/catalog/final validator. No panel batch,
background worker, service control, network POST or acceptance PASS is created.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import uuid


async def prepare(module, body, directory: Path, execution_id: str, analysis_seed: int, prompt_seed: int):
    if any(os.getenv(key, "0") != "0" for key in ("H3_ALLOW_SUBMISSIONS", "H3_ALLOW_DIAGNOSTIC_SUBMISSIONS")):
        raise ValueError("export requires both submission flags OFF")
    if module.queue or module.batches:
        raise ValueError("export must run in a fresh process, never a running controller")
    from importlib import import_module
    artifacts = import_module(module.__package__ + ".artifacts")
    # No acceptance exception: the retained DavidAU production gate blocks this.
    artifacts.validate_manifest(json.loads((module.CONFIG / "models_manifest.json").read_text()))
    req = module.BatchRequest.model_validate(body)
    if req.prompt_mode != "autoprompt" or req.batches != 1 or req.seed is None or not (req.prompt or "").strip():
        raise ValueError("one explicit-seed autoprompt probe is required")
    if str(uuid.UUID(execution_id)) != execution_id or req.request_id is None:
        raise ValueError("canonical execution and request UUIDs are required")
    if any(type(value) is not int or not 0 <= value <= 0x7FFFFFFF for value in (req.seed, analysis_seed, prompt_seed)):
        raise ValueError("explicit nonnegative 31-bit prompt/analysis seeds required")
    profile = req.profile or req.model or module.PRODUCTION["default_profile"]
    if profile not in module.PRODUCTION["profiles"] or (req.profile and req.model and req.profile != req.model):
        raise ValueError("unknown or contradictory profile")
    pictures, audio = module.validate_requested_assets(req)
    duration = module.duration_identity(req.duration_seconds)
    context = {
        **module.freeze_prompt_source("autoprompt", req.prompt, None, duration["effective_duration_seconds"]),
        "profile": profile, "model": profile, "profile_identity": module.profile_identity(profile),
        **module.workflow_context(profile, req.render_settings.model_dump(exclude_unset=True)),
        "lora_identity": {key: item["filename"] for key, item in module.LORA_REGISTRY.items()},
        "conditioning_bridge": {"id": module.CONDITIONING_BRIDGE["id"], "filename": module.CONDITIONING_BRIDGE["filename"],
                                **module.CONDITIONING_BRIDGE["defaults"][profile]},
        "duration_seconds": req.duration_seconds, **duration, "pictures": pictures, "audio": audio,
        "loras": module.resolve_loras(module.LORA_REGISTRY, profile, [item.model_dump() for item in req.loras]),
        "soft_timeout_minutes": req.soft_timeout_minutes, "hard_restart_after_seconds": req.hard_restart_after_seconds,
    }
    job = {"id": execution_id, "profile": profile, "active_service": "prompt", "prompt_mode": "autoprompt",
           "render_seed": req.seed, "analysis_seed": analysis_seed, "prompt_seed": prompt_seed, "context": context}
    module.job_profile(job)
    await module.verify_production_files(job, "prompt")
    directory = directory.absolute()
    if directory.resolve() != directory:
        raise ValueError("linked evidence directory is not allowed")
    directory.mkdir(mode=0o700)  # Existing evidence is never overwritten.
    original_state = module.STATE
    module.STATE = directory
    module.queue.append(job)  # Converter lookup only; no durable queue publication.
    try:
        path = module.patch_workflow(job, module.production_workflow(job))
        graph = await module._prepare_workflow_api(path, "prompt")
        module.job_workflow_adapter(job).validate_final_graph(graph, context, phase="prompt")
        await module.verify_production_files(job, "prompt")
        request = {"prompt": graph, "prompt_id": execution_id, "client_id": execution_id}
        artifacts.atomic_json(directory, "request.json", request)
        digest = hashlib.sha256((directory / "request.json").read_bytes()).hexdigest()
        artifacts.atomic_json(directory, "evidence.json", {"job": job, "request_sha256": digest,
            "request_id": str(req.request_id), "submitted": False, "gpu_acceptance": "not_run"})
        return {"directory": str(directory), "execution_id": execution_id, "request_sha256": digest,
                "submitted": False, "gpu_acceptance": "not_run"}
    finally:
        module.queue.clear()
        module.STATE = original_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--analysis-seed", type=int, required=True)
    parser.add_argument("--prompt-seed", type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    module = importlib.import_module("app.main")  # No ASGI startup/lifespan.
    result = asyncio.run(prepare(module, json.loads(args.request.read_text()), args.output,
                                 args.execution_id, args.analysis_seed, args.prompt_seed))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
