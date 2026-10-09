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
    from .deployment import exchange, sync_directory
except ImportError:
    from deployment import exchange, sync_directory


def install(source: Path, custom_nodes: Path) -> None:
    name = "aj_production"
    custom_nodes.mkdir(parents=True, exist_ok=True)
    if custom_nodes.is_symlink():
        raise ValueError("custom node parent is symlinked")
    target = custom_nodes / name
    if source.is_symlink() or (source / "__init__.py").is_symlink():
        raise ValueError("owned node source is symlinked")
    body = (source / "__init__.py").read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise ValueError("AJ node destination is unrelated or symlinked")
        owner = target / ".aj-owner.json"
        module = target / "__init__.py"
        if owner.is_symlink() or module.is_symlink():
            raise ValueError("AJ node ownership files are symlinked")
        previous = json.loads(owner.read_text())
        if previous.get("owner") != "AJ" or hashlib.sha256(module.read_bytes()).hexdigest() != previous.get("sha256"):
            raise ValueError("AJ node local edits preserved; reconcile before provisioning")
        if previous["sha256"] == digest:
            return
        if set(path.name for path in target.iterdir()) - {"__init__.py", ".aj-owner.json", "__pycache__"}:
            raise ValueError("AJ node contains unrelated files; preserved")
    stage = Path(tempfile.mkdtemp(prefix=".aj-node-", dir=custom_nodes))
    try:
        for filename, content in (("__init__.py", body), (".aj-owner.json", json.dumps({"owner": "AJ", "sha256": digest}).encode())):
            descriptor = os.open(stage / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
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
