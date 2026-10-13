#!/usr/bin/env python3
"""Idempotently install AJ's owned node package; preserve unrelated files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    from ..app.native_identity import read_native_sources, verify_deployed_native, validate_manifest_paths
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.native_identity import read_native_sources, verify_deployed_native, validate_manifest_paths

try:
    from .deployment import exchange, sync_directory
except ImportError:
    from deployment import exchange, sync_directory


def install(source: Path, custom_nodes: Path) -> None:
    name = "aj_production"
    custom_nodes.mkdir(parents=True, exist_ok=True)
    if custom_nodes.is_symlink():
        raise ValueError("custom node parent is symlinked")
    target = custom_nodes / name
    bodies = read_native_sources(source)
    digests = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise ValueError("AJ node destination is unrelated or symlinked")
        owner = target / ".aj-owner.json"
        if owner.is_symlink():
            raise ValueError("AJ node ownership files are symlinked")
        previous = json.loads(owner.read_text())
        previous_files = previous.get("files_sha256", {"__init__.py": previous.get("sha256")})
        if previous.get("owner") != "AJ":
            raise ValueError("AJ node local edits preserved; reconcile before provisioning")
        try:
            validate_manifest_paths(previous_files)
            verify_deployed_native(target, previous_files)
        except (ValueError, OSError) as error:
            raise ValueError("AJ node local edits or ambiguous ownership preserved; reconcile before provisioning") from error
        if previous_files == digests:
            return
    stage = Path(tempfile.mkdtemp(prefix=".aj-node-", dir=custom_nodes))
    try:
        receipt = {"owner": "AJ", "sha256": digests["__init__.py"], "files_sha256": digests}
        for filename, content in [*bodies.items(), (".aj-owner.json", json.dumps(receipt).encode())]:
            (stage / filename).parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(stage / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        # Persist every subpackage's entries before publishing the package root.
        for directory in sorted((path for path in stage.rglob("*") if path.is_dir()),
                                key=lambda path: len(path.parts), reverse=True):
            sync_directory(directory)
        sync_directory(stage)
        if target.exists():
            exchange(stage, target)
        else:
            os.rename(stage, target)
        sync_directory(custom_nodes)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    try:
        install(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception:
        raise SystemExit("AJ custom node installation failed; existing files preserved") from None
