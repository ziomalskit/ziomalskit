#!/usr/bin/env python3
"""Persistent, resumable Hugging Face downloads with ephemeral credentials.

Only provisioning calls this module. Configuration/Diagnostics never do so.
The SDK's hf-xet support handles range/chunk reuse. No login/token-save API is
used; SDK output and exceptions are not forwarded to logs or reports.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
import hashlib
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifacts import atomic_json, parent_directory, signature, validate_artifact, verify_file


@contextmanager
def store_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with parent_directory(root, ".provision.lock") as (directory, name):
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            signature(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def download_sdk(item: dict, staging: Path) -> Path:
    # The credentials live only in this process environment and SDK call.
    # Hugging Face accepts HF_XET_HIGH_PERFORMANCE=1 natively. Preserve it.
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_VERBOSITY"] = "error"
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with provider_output_suppressed() as sink, redirect_stdout(sink), redirect_stderr(sink):
            from huggingface_hub import hf_hub_download
            result = hf_hub_download(repo_id=item["repository"], revision=item["revision"],
                                     filename=item["repository_path"], local_dir=str(staging),
                                     token=os.environ.get("HF_TOKEN") or False)
        return Path(result)
    except Exception:
        raise ValueError("artifact download failed; check provider access/network and retry provisioning") from None
    finally:
        logging.disable(previous)


@contextmanager
def provider_output_suppressed():
    """Suppress Python and native hf-xet output in the provisioning process."""
    stdout_fd, stderr_fd = os.dup(1), os.dup(2)
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            try:
                yield sink
            finally:
                os.dup2(stdout_fd, 1)
                os.dup2(stderr_fd, 2)
    finally:
        os.close(stdout_fd)
        os.close(stderr_fd)


def install_verified(root: Path, item: dict, staging: Path, source: Path) -> dict:
    # Never follow SDK-returned paths outside the isolated staging directory.
    relative = source.absolute().relative_to(staging.absolute()).as_posix()
    verified = verify_file(staging, relative, item)
    with parent_directory(staging, relative) as (source_directory, source_name), parent_directory(root, item["destination"], create=True) as (directory, name):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            # Wrong existing weights never get overwritten as an implicit repair.
            raise ValueError("artifact destination appeared during download; verify it before retrying")
        temporary = "." + name + "." + uuid.uuid4().hex + ".partial"
        source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=source_directory)
        target_fd = None
        try:
            if signature(os.fstat(source_fd)) != verified["signature"]:
                raise ValueError("download changed before installation")
            target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            digest, size = hashlib.sha256(), 0
            while block := os.read(source_fd, 8 * 1024 * 1024):
                digest.update(block)
                size += len(block)
                view = memoryview(block)
                while view:
                    view = view[os.write(target_fd, view):]
            if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("download changed during installation")
            os.fsync(target_fd)
            os.close(target_fd)
            target_fd = None
            # linkat provides no-overwrite publication, even against races.
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    return verify_file(root, item["destination"], item)


def ensure_download(root: Path, item: dict, *, downloader=download_sdk) -> dict:
    validate_artifact(item)
    if item["provider"] != "huggingface":
        raise ValueError("generated artifacts require the conversion provisioner")
    with store_lock(root):
        try:
            result = verify_file(root, item["destination"], item)
        except FileNotFoundError:
            # Xet staging can reuse interrupted chunks, but is never a model.
            staging_relative = ".downloads/" + item["id"] + "/.anchor"
            with parent_directory(root, staging_relative, create=True):
                pass
            staging = root / ".downloads" / item["id"]
            if shutil.disk_usage(root).free < item["size_bytes"] * 2:
                raise ValueError("insufficient persistent disk space for download staging and artifact")
            source = downloader(item, staging)
            result = install_verified(root, item, staging, Path(source))
            # Keep SDK resume metadata, not a second copy of verified weights.
            source_relative = Path(source).absolute().relative_to(staging.absolute()).as_posix()
            with parent_directory(staging, source_relative) as (directory, name):
                os.unlink(name, dir_fd=directory)
            reused = False
        else:
            reused = True
        receipt = {"schema_version": 1, "artifact_id": item["id"], "repository": item["repository"],
                   "revision": item["revision"], "repository_path": item["repository_path"],
                   "destination": item["destination"], **result, "gpu_validation": "pending"}
        atomic_json(root, ".verification/" + item["id"] + ".json", receipt)
        return {"artifact_id": item["id"], "status": "verified_file", "reused": reused,
                "size_bytes": result["size_bytes"], "sha256": result["sha256"], "gpu_validation": "pending"}
