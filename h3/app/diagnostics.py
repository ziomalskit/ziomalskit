"""Small, read-only probes. No workflow, lifecycle or provisioning actions."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path

import httpx

PROBE_SECONDS = 2.0
MAX_STATS_BYTES = 64 * 1024


async def service_health(base_url: str) -> dict:
    async def probe():
        async with httpx.AsyncClient(timeout=1.0) as client:
            async with client.stream("GET", base_url + "/system_stats") as response:
                response.raise_for_status()
                body = bytearray()
                async for block in response.aiter_bytes():
                    if len(body) + len(block) > MAX_STATS_BYTES:
                        raise ValueError("oversized health response")
                    body.extend(block)
                if not isinstance(json.loads(body), dict):
                    raise ValueError("invalid health response")
        return {"status": "PASS", "detail": "Responding to /system_stats"}

    try:
        return await asyncio.wait_for(probe(), timeout=PROBE_SECONDS)
    except Exception:
        # Never echo service response bodies/URLs into the panel.
        return {"status": "FAIL", "detail": "Service unavailable or health response invalid"}


async def local_probe(function) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(function), timeout=PROBE_SECONDS)
    except Exception:
        return {"error": "Probe unavailable or timed out"}


def model_status(manifest_path: Path, models_root: Path, comfy_root: Path) -> dict:
    """Presence only, using the server's provisional manifest, never client paths."""
    result = {"provisional": True, "detail": "Provisional manifest: presence only; production models are not frozen",
              "files": []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") == 2:
            from .model_health import manifest_health
            return manifest_health(manifest_path, models_root)
        required = [(item, [models_root / item["dir"] / item["file"]])
                    for item in manifest["required_primary"]]
        required.extend((item, [models_root / "loras" / item["file"]])
                        for item in manifest["loras_default_required"])
        for item in manifest.get("special_required_models", []):
            paths = []
            for location in item["accepted_locations"]:
                relative = Path(location)
                paths.append(models_root / relative.relative_to("models")
                             if relative.parts[0] == "models" else comfy_root / relative)
            required.append((item, paths))
        for item, paths in required:
            present = any(path.is_file() and path.stat().st_size > 0 for path in paths)
            result["files"].append({"file": item["file"], "role": item.get("role", "Default LoRA"),
                                    "present": present, "status": "PASS" if present else "FAIL"})
        result["missing"] = sum(not item["present"] for item in result["files"])
        # Even complete file presence cannot prove integrity, loader readiness
        # or final model selection. Keep that limitation visible.
        result["status"] = "FAIL" if result["missing"] else "WARN"
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        result.update(status="WARN", error="Provisional model manifest could not be checked")
    return result


def queue_summary(jobs: list[dict]) -> dict:
    counts = dict(sorted(Counter(job.get("status", "unknown") for job in jobs).items()))
    return {"total": len(jobs), "by_status": counts,
            "prompt_queued": counts.get("prompt_queued", 0),
            "render_queued": counts.get("render_queued_auto", 0) + counts.get("render_queued_review", 0),
            "pending_review": counts.get("pending_review", 0)}


def checks_for(data: dict) -> list[dict]:
    checks = []

    def add(identifier, label, status, detail):
        checks.append({"id": identifier, "label": label, "status": status, "detail": detail})

    controller = data["controller"]
    add("controller", "AJ / controller", "PASS" if controller["ready"] else "FAIL",
        next((controller[key] for key in ("queue_error", "persistence_error", "worker_error", "lifecycle_error")
              if controller.get(key)), "Workers running; durable queue available"))
    for service in ("render", "prompt"):
        health = data["services"][service]
        add(service, service.title() + " ComfyUI", health["status"], health["detail"])
    gpu = data["gpu"]
    add("gpu", "GPU", "WARN" if gpu.get("error") else "PASS", gpu.get("error") or gpu.get("name", "Available"))
    vram = data["vram"]
    add("vram", "VRAM", "PASS" if vram["available"] else "WARN",
        f"{vram['used_mb']} / {vram['total_mb']} MB" if vram["available"] else "Telemetry unavailable")
    storage = data["storage"]
    add("storage", "Persistent storage", "PASS" if storage.get("safe_for_destroy_keep_data") else "WARN",
        storage.get("reason") or storage.get("error") or "Retained volume VERIFIED")
    disk = data["disk"]
    add("disk", "Disk free", "WARN" if disk.get("error") else "FAIL" if disk.get("free_gb", 0) <= 0 else "PASS",
        disk.get("error") or f"{disk.get('free_gb')} GB free")
    summary = data["queue"]
    add("queue", "Queue", "PASS", f"{summary['prompt_queued']} awaiting prompt; {summary['render_queued']} awaiting render; {summary['pending_review']} in review")
    prefetch = data["prefetch"]
    add("prefetch", "Parallel prompt prefetch", "PASS",
        f"{prefetch['ready']} ready + {prefetch['preparing']} preparing / target {prefetch['target']}")
    vast = data["vast"]
    add("vast", "Vast CLI / control availability", "PASS" if vast["control_available"] else "WARN",
        "Local CLI and instance ID available; remote authorization not probed" if vast["control_available"]
        else "CLI, instance ID or runtime control unavailable")
    models = data["models"]
    add("models", "Production model integrity", models["status"],
        models.get("error") or models.get("detail") or f"{models.get('missing', 0)} missing/empty; legacy diagnostic presence only")
    production = data.get("production")
    if production:
        active = production.get("active_render") or {}
        identifier = active.get("profile") or production["default_profile"]
        profile = production["profiles"].get(identifier)
        if profile:
            add("memory", profile["label"] + " memory / overlap", "WARN",
                profile["memory_policy"]["label"] + "; " + profile["overlap_policy"]["label"] + "; GPU validation pending")
        add("prompt_cache", "Prompt cache epoch", "PASS",
            "Controller " + production["prompt_cache_epoch"] + "; service " + str(production["prompt_service_epoch"] or "not observed"))
    return checks
