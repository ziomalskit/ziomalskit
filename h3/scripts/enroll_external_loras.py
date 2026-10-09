#!/usr/bin/env python3
"""Explicit operator enrollment of user-provided registered LoRA bytes.

This does not assert source provenance or GPU compatibility. Subsequent starts
must match these exact bytes; a same-name replacement fails closed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifacts import atomic_json, parent_directory, read_json, signature
from app.production import read_loras, read_bridge
try:
    from .model_downloads import store_lock
except ImportError:
    from model_downloads import store_lock


def enroll(root: Path, registry_path: Path, identifiers: list[str]):
    with store_lock(root):
        return _enroll_locked(root, registry_path, identifiers)


def _enroll_locked(root: Path, registry_path: Path, identifiers: list[str]):
    registry = read_loras(registry_path)
    bridge = read_bridge(registry_path)
    destinations = {key: "loras/" + value["filename"] for key, value in registry.items()}
    destinations[bridge["id"]] = "semantic_bridge/" + bridge["filename"]
    if len(set(identifiers)) != len(identifiers) or any(identifier not in destinations for identifier in identifiers):
        raise ValueError("only registered LoRA IDs can be enrolled")
    try:
        record = read_json(root, ".external_loras.json")
    except FileNotFoundError:
        record = {"schema_version": 1, "artifacts": {}}
    if record.get("schema_version") != 1 or not isinstance(record.get("artifacts"), dict):
        raise ValueError("invalid external LoRA enrollment metadata")
    for identifier in identifiers:
        destination = destinations[identifier]
        if identifier in record["artifacts"]:
            raise ValueError("LoRA was already enrolled; changed weights require explicit reconciliation")
        with parent_directory(root, destination) as (directory, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                before = signature(os.fstat(descriptor))
                if not before["size"]:
                    raise ValueError("empty external LoRA")
                digest = hashlib.sha256()
                while block := os.read(descriptor, 8 * 1024 * 1024):
                    digest.update(block)
                if signature(os.fstat(descriptor)) != before or signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != before:
                    raise ValueError("external LoRA changed during enrollment")
            finally:
                os.close(descriptor)
        record["artifacts"][identifier] = {"destination": destination, "size_bytes": before["size"],
            "sha256": digest.hexdigest(), "provenance_status": "external/user-provided", "gpu_validation": "pending"}
    atomic_json(root, ".external_loras.json", record)
    return {"enrolled_ids": identifiers, "gpu_validation": "pending", "provenance_status": "external/user-provided"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("ids", nargs="+")
    arguments = parser.parse_args()
    try:
        print(json.dumps(enroll(arguments.models_root, arguments.registry, arguments.ids)))
    except Exception:
        raise SystemExit("External LoRA enrollment failed; verify registered IDs/files and reconcile existing enrollment") from None
