#!/usr/bin/env python3
"""Persistent, resumable Hugging Face downloads with ephemeral credentials.

Only provisioning calls this module. Configuration/Diagnostics never do so.
The pinned SDK's HTTP/Xet range streams resume one retained partial. No Hub
weight cache, login/token-save API or Xet reconstruction file is used. SDK output and
exceptions are not forwarded to logs or reports.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifacts import atomic_json, parent_directory, read_json, signature, validate_artifact, verify_file
from app.file_publication import publish_noreplace
try:
    from .install_timing import Stage
except ImportError:
    from install_timing import Stage


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
    """SDK HTTP/Xet ranges into one retained partial; no duplicate weight cache.

    huggingface-hub 2.2.0's high-level downloader uses unique temporary names
    and deletes failed transfers. Keep our existing serialized staging and use
    its HTTP resume_size / Xet ordered range stream instead. Final integrity
    remains local SHA256; an interrupted stream retains only its written prefix.
    """
    validate_artifact(item)
    validate_staging(staging)
    facts = {key: item[key] for key in ('repository', 'revision', 'repository_path', 'size_bytes', 'sha256')}
    try:
        previous_source = read_json(staging, '.source.json')
    except FileNotFoundError:
        if (staging / '.payload.incomplete').exists():
            raise ValueError('unbound partial download is preserved')
        atomic_json(staging, '.source.json', facts)
    else:
        if previous_source != facts:
            raise ValueError('partial download source differs from frozen artifact')
    path = staging / '.payload.incomplete'
    # Complete retained bytes need neither another network request nor a token.
    if path.exists() and path.stat().st_size == item['size_bytes']:
        return path
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HUB_VERBOSITY'] = 'error'
    # Resume lives in the retained ordered prefix, not a second chunk cache.
    os.environ['HF_XET_CHUNK_CACHE_SIZE_BYTES'] = '0'
    os.environ['HF_XET_TELEMETRY_ENABLED'] = '0'
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with provider_output_suppressed() as sink, redirect_stdout(sink), redirect_stderr(sink):
            from huggingface_hub import hf_hub_url, get_hf_file_metadata
            from huggingface_hub.file_download import http_get
            from huggingface_hub.utils import build_hf_headers
            token = os.environ.get('HF_TOKEN') or False
            url = hf_hub_url(item['repository'], item['repository_path'], revision=item['revision'],
                             endpoint='https://huggingface.co')
            metadata = get_hf_file_metadata(url, token=token)
            if metadata.commit_hash != item['revision'] or metadata.size != item['size_bytes']:
                raise ValueError('upstream metadata differs from frozen artifact')
            location = urlsplit(metadata.location)
            if location.scheme != 'https' or not location.hostname or location.username or location.password or location.port not in (None, 443):
                raise ValueError('unsafe download location')
            # Only HF receives its bearer token; signed CDN URLs carry their own auth.
            headers = build_hf_headers(token=token) if location.netloc == urlsplit(url).netloc else {}
            with parent_directory(staging, path.name) as (directory, name):
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'r+b') as stream:
                    info = os.fstat(stream.fileno())
                    signature(info)
                    if info.st_nlink != 1 or info.st_size > item['size_bytes']:
                        raise ValueError('unsafe partial download')
                    stream.seek(0, os.SEEK_END)
                    try:
                        if getattr(metadata, 'xet_file_data', None) is not None:
                            receive_xet(metadata.xet_file_data, stream, item['size_bytes'], token)
                        else:
                            http_get(metadata.location, stream, headers=headers, resume_size=info.st_size,
                                     expected_size=item['size_bytes'])
                        if stream.tell() != item['size_bytes']:
                            raise ValueError('download size differs from frozen artifact')
                    finally:
                        stream.flush()
                        os.fsync(stream.fileno())
            return path
    except Exception:
        raise ValueError('artifact download failed; check provider access/network and retry provisioning') from None
    finally:
        logging.disable(previous)


def receive_xet(data, stream, expected_size, token):
    """The pinned hf-xet 1.7 ordered byte-range iterator, including >50GB files.

    Calling xet_get/start_download_file would truncate a retained prefix. Its
    stream API resumes at that prefix without materializing another full file.
    """
    from hf_xet import XetFileInfo
    from huggingface_hub.utils import build_hf_headers
    from huggingface_hub.utils._xet import get_xet_session, refresh_xet_connection_info, xet_headers_without_auth
    route = urlsplit(data.refresh_route)
    if route.scheme != 'https' or route.netloc != 'huggingface.co' or route.username or route.password:
        raise ValueError('unsafe Xet token refresh location')
    headers = build_hf_headers(token=token)
    connection = refresh_xet_connection_info(file_data=data, headers=headers)
    with get_xet_session().new_download_stream_group(
            endpoint=connection.endpoint, token=connection.access_token,
            token_expiry_unix_secs=connection.expiration_unix_epoch,
            token_refresh_url=data.refresh_route, token_refresh_headers=headers,
            custom_headers=xet_headers_without_auth(headers)) as group:
        for block in group.download_stream(XetFileInfo(data.file_hash, expected_size),
                                           start=stream.tell(), end=expected_size):
            if len(block) > expected_size - stream.tell():
                raise ValueError('Xet stream exceeds frozen artifact size')
            stream.write(block)


def validate_staging(staging: Path) -> None:
    """Inspect retained SDK data without following links or opening FIFOs.

    Provisioners serialize under store_lock. The private (0700) store is not a
    boundary against another process with the same uid modifying it concurrently.
    """
    def walk(directory):
        for name in os.listdir(directory):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                try:
                    walk(child)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe retained download staging entry")
    # Also walk every ancestor from the filesystem root without following links.
    with parent_directory(Path(staging.absolute().anchor), staging.absolute().as_posix().lstrip('/') + '/.anchor') as (directory, _):
        walk(directory)


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
    """Move one verified inode to its destination, never copy or overwrite."""
    relative = source.absolute().relative_to(staging.absolute()).as_posix()
    with Stage('sha256', artifact=item['id'], size_bytes=item['size_bytes']):
        verified = verify_file(staging, relative, item)
    with parent_directory(staging, relative) as (source_directory, source_name), parent_directory(root, item['destination'], create=True) as (directory, name):
        fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=source_directory)
        try:
            info = os.fstat(fd)
            if info.st_nlink != 1 or signature(info) != verified['signature']:
                raise ValueError('download changed before installation')
            if signature(os.stat(source_name, dir_fd=source_directory, follow_symlinks=False)) != verified['signature']:
                raise ValueError('download path changed before publication')
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            publish_noreplace(source_directory, source_name, name, directory)
            published = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('published inode differs from verified download')
        finally:
            os.close(fd)
    with Stage('sha256_published', artifact=item['id'], size_bytes=item['size_bytes']):
        return verify_file(root, item['destination'], item)


def ensure_download(root: Path, item: dict, *, downloader=download_sdk) -> dict:
    validate_artifact(item)
    if item["provider"] != "huggingface":
        raise ValueError("generated artifacts require the conversion provisioner")
    with store_lock(root):
        verification = Stage('sha256_existing', artifact=item['id'], size_bytes=item['size_bytes']).__enter__()
        try:
            result = verify_file(root, item["destination"], item)
        except FileNotFoundError:
            verification.finish('absent')
            # Retained HTTP partial bytes are not a verified model.
            staging_relative = ".downloads/" + item["id"] + "/.anchor"
            with parent_directory(root, staging_relative, create=True):
                pass
            staging = root / ".downloads" / item["id"]
            validate_staging(staging)
            partial = staging / ".payload.incomplete"
            retained = partial_bytes(root, item)
            if shutil.disk_usage(root).free < max(0, item["size_bytes"] - retained):
                raise ValueError("insufficient disk space for remaining download bytes")
            with Stage("download", artifact=item["id"], size_bytes=item["size_bytes"]):
                source = downloader(item, staging)
            result = install_verified(root, item, staging, Path(source))
            # Rename consumed the payload. Keep only source/resume metadata.
            reused = False
        except BaseException:
            verification.finish('failed')
            raise
        else:
            verification.finish('completed')
            reused = True
            stage_parent = root / ".downloads" / item["id"]
            if stage_parent.exists():
                validate_staging(stage_parent)
                descriptor = os.open(stage_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
            # Finish durability if a prior process died after rename but before
            # syncing the destination directory. No download or overwrite.
            with parent_directory(root, item['destination']) as (directory, _):
                os.fsync(directory)
        receipt = {"schema_version": 1, "artifact_id": item["id"], "repository": item["repository"],
                   "revision": item["revision"], "repository_path": item["repository_path"],
                   "destination": item["destination"], **result, "gpu_validation": "pending"}
        atomic_json(root, ".verification/" + item["id"] + ".json", receipt)
        return {"artifact_id": item["id"], "status": "verified_file", "reused": reused,
                "size_bytes": result["size_bytes"], "sha256": result["sha256"], "gpu_validation": "pending"}


def partial_bytes(root: Path, item: dict) -> int:
    """Only a safely bound SDK partial can reduce the remaining disk estimate."""
    staging = root / '.downloads' / item['id']
    try:
        validate_staging(staging)
        source = read_json(staging, '.source.json')
        if source != {key: item[key] for key in ('repository', 'revision', 'repository_path', 'size_bytes', 'sha256')}:
            raise ValueError('partial source identity differs')
        with parent_directory(staging, '.payload.incomplete') as (directory, name):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            signature(info)
            if info.st_nlink != 1 or info.st_size > item['size_bytes']:
                raise ValueError('unsafe partial file')
            return info.st_size
    except FileNotFoundError:
        return 0
