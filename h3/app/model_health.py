"""Read-only integrity reports. Full hashing is explicit preflight work."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .artifacts import (parent_directory, read_json, signature, validate_generated_provenance,
                        validate_manifest, verify_file)


def artifact_health(root: Path, item: dict, *, full_hash=False) -> dict:
    result = {"id": item["id"], "role": item["role"], "profiles": item["profiles"], "gpu_validation": "pending"}
    try:
        if item["provider"] == "generated":
            receipt = read_json(root, ".provenance/" + item["id"] + ".json")
            expected = validate_generated_provenance(item, receipt)
        else:
            expected = item
            receipt = None
        with parent_directory(root, item["destination"]) as (directory, name):
            current = signature(os.stat(name, dir_fd=directory, follow_symlinks=False))
        if current["size"] != expected["size_bytes"]:
            return {**result, "status": "wrong_size"}
        if full_hash:
            verify_file(root, item["destination"], expected)
            return {**result, "status": "verified_file"}
        if receipt is None:
            try:
                receipt = read_json(root, ".verification/" + item["id"] + ".json")
            except FileNotFoundError:
                return {**result, "status": "hash_verification_required"}
            if any(receipt.get(field) != item[field] for field in ("repository", "revision", "repository_path", "destination")):
                return {**result, "status": "provenance_mismatch"}
        verified = (receipt.get("size_bytes") == expected["size_bytes"] and receipt.get("sha256") == expected["sha256"]
                    and receipt.get("signature") == current)
        return {**result, "status": "verified_file" if verified else "hash_verification_required"}
    except FileNotFoundError:
        return {**result, "status": "missing"}
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, "status": "wrong_hash_or_provenance"}


def manifest_health(manifest_path: Path, root: Path, *, full_hash=False) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text())
        entries = validate_manifest(manifest, allow_pending=True)
        files = [artifact_health(root, item, full_hash=full_hash) for item in entries.values()]
        pending = [{"id": item["id"], "status": item["status"], "reason": item["reason"]}
                   for item in manifest.get("pending_artifacts", [])]
        ready = not pending and all(item["status"] == "verified_file" for item in files if entries[item["id"]]["required"])
        return {"status": "WARN" if ready else "FAIL", "provisional": bool(pending), "manifest_status": manifest["status"],
                "files": files, "missing": sum(item["status"] == "missing" for item in files), "pending_artifacts": pending,
                "gpu_validation": "pending", "detail": "Verified files still require GPU acceptance" if ready else
                "Required integrity/provenance checks are incomplete"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "FAIL", "provisional": True, "detail": "Production manifest is invalid or unavailable", "files": []}


def lora_health(root: Path, identifier: str, entry: dict, *, bridge=False, full_hash=False) -> dict:
    """Report installation separately from source and GPU compatibility."""
    destination = ("semantic_bridge/" if bridge else "loras/") + entry["filename"]
    result = {"provenance_status": entry["provenance_status"], "gpu_validation": entry["gpu_validation"]}
    if entry["provenance_status"] == "verified_source":
        item = {"id": identifier, "role": "Registered LoRA", "profiles": list(entry["defaults"]),
                "provider": "huggingface", "repository": entry["source"]["repository"],
                "revision": entry["revision"], "repository_path": entry["repository_path"],
                "destination": destination, "size_bytes": entry["size_bytes"], "sha256": entry["sha256"]}
        return {**result, "installation": artifact_health(root, item, full_hash=full_hash)["status"]}
    try:
        with parent_directory(root, destination) as (directory, name):
            signature(os.stat(name, dir_fd=directory, follow_symlinks=False))
        enrolled = read_json(root, ".external_loras.json").get("artifacts", {}).get(identifier)
        if not isinstance(enrolled, dict) or enrolled.get("destination") != destination:
            return {**result, "installation": "external_enrollment_required"}
        if full_hash:
            verify_file(root, destination, enrolled)
            return {**result, "installation": "verified_file"}
        return {**result, "installation": "hash_verification_required"}
    except FileNotFoundError:
        return {**result, "installation": "missing"}
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, "installation": "wrong_hash_or_provenance"}


def verify_bridge_files(root: Path, comfy_root: Path, entry: dict, expected: dict) -> None:
    """Pinned Bunny v0.3 prefers bundled files; no shadow may bypass SHA256."""
    verify_file(root, "semantic_bridge/" + entry["filename"], expected)
    for folder in ("BUNNY_H3_Conditioning_Bridge", "bunny-h3-semantic-bridge"):
        relative = "custom_nodes/" + folder + "/models/" + entry["filename"]
        try:
            with parent_directory(comfy_root, relative) as (directory, name):
                os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            continue
        verify_file(comfy_root, relative, expected)
