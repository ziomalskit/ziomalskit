#!/usr/bin/env python3
"""Explicit provisioning entry point; never submits a render or Vast action."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifacts import validate_manifest
try:
    from .model_downloads import ensure_download
    from .build_generated_models import ensure_generated
except ImportError:
    from model_downloads import ensure_download
    from build_generated_models import ensure_generated


def disk_plan(manifest: dict, root: Path) -> dict:
    items = validate_manifest(manifest, allow_pending=True)
    known = [item for item in items.values() if item["provider"] == "huggingface"]
    missing = [item for item in known if not (root / item["destination"]).exists()]
    required = sum(item["size_bytes"] for item in missing)
    staging = max((item["size_bytes"] for item in missing), default=0)
    generated = [item for item in items.values() if item["provider"] == "generated" and not (root / item["destination"]).exists()]
    source_bytes = sum(source["size_bytes"] for item in generated for source in item["generation"]["source_files"])
    source_staging = max((source["size_bytes"] for item in generated for source in item["generation"]["source_files"]), default=0)
    output_estimate = sum(source["size_bytes"] for item in generated for source in item["generation"]["source_files"] if source["path"].endswith(".safetensors")) * 11 // 10
    target = root
    while not target.exists():
        target = target.parent
    return {"known_stack_bytes": sum(item["size_bytes"] for item in known), "missing_download_bytes": required,
            "download_staging_bytes": max(staging, source_staging),
            "generated_source_bytes": source_bytes, "generated_output_estimate_bytes": output_estimate,
            "minimum_free_bytes": required + source_bytes + output_estimate * 2 + max(staging, source_staging),
            "free_bytes": shutil.disk_usage(target).free,
            "generated_source_and_output_space": "unresolved" if manifest.get("pending_artifacts") else "conservative BF16 estimate from pinned shards",
            "pending_artifacts": [item["id"] for item in manifest.get("pending_artifacts", [])]}


def provision(manifest: dict, root: Path, toolchain: Path):
    items = validate_manifest(manifest)
    plan = disk_plan(manifest, root)
    if plan["free_bytes"] < plan["minimum_free_bytes"]:
        raise ValueError("persistent volume cannot hold the required models and download staging")
    results = []
    for item in items.values():
        results.append(ensure_generated(root, item, toolchain, python=os.environ.get("COMFY_PYTHON") or sys.executable)
                       if item["provider"] == "generated" else ensure_download(root, item))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        print(json.dumps(disk_plan(manifest, args.models_root), sort_keys=True))
        # Always fail before downloads when the complete production identity
        # cannot be proven. The plan still reports the known disk requirement.
        validate_manifest(manifest)
        if not args.plan:
            if args.toolchain is None:
                raise ValueError("pinned llama.cpp toolchain path is required")
            print(json.dumps(provision(manifest, args.models_root, args.toolchain), sort_keys=True))
    except Exception:
        # SDK/tool exceptions may contain URLs or credential headers.
        print("Model provisioning blocked: incomplete provenance, insufficient disk, or artifact verification/build failure. No render submitted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
