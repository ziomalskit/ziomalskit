"""Exact artifact integrity and safe persistent destinations.

All identities originate in trusted server configuration. Browser requests
cannot supply repositories, revisions, destinations or checksums.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .production import IDENTIFIER, relative_path

SHA256 = re.compile(r"[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def validate_artifact(item: dict) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not IDENTIFIER.fullmatch(item["id"]):
        raise ValueError("invalid artifact ID")
    relative_path(item["destination"])
    if type(item.get("required")) is not bool or not isinstance(item.get("role"), str) or not item["role"]:
        raise ValueError("artifact role/requirement missing")
    if not isinstance(item.get("profiles"), list) or not item["profiles"] or not set(item["profiles"]) <= {"shared", "h3_full", "10eros_full"}:
        raise ValueError("artifact ownership missing")
    if item.get("gpu_validation") not in {"pending", "validated"}:
        raise ValueError("artifact GPU validation state missing")
    if item.get("provider") == "generated":
        validate_recipe(item["generation"])
        if Path(item["destination"]).name != item["generation"]["output_filename"] or not item["destination"].startswith("LLM/"):
            raise ValueError("generated output destination differs from recipe")
        return
    if item.get("provider") != "huggingface" or not REPOSITORY.fullmatch(item.get("repository", "")) or not REVISION.fullmatch(item.get("revision", "")):
        raise ValueError("artifact immutable source missing")
    relative_path(item["repository_path"])
    if type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0 or not SHA256.fullmatch(item.get("sha256", "")):
        raise ValueError("artifact exact size/SHA256 missing")


def validate_recipe(recipe: dict) -> None:
    if not isinstance(recipe, dict) or recipe.get("format") != "bf16" or recipe.get("runtime") != "llama.cpp":
        raise ValueError("generated writer must use BF16 llama.cpp conversion")
    for field in ("source_revision", "llama_cpp_revision", "converter_revision"):
        if not REVISION.fullmatch(recipe.get(field, "")):
            raise ValueError("conversion immutable revision missing")
    if recipe["converter_revision"] != recipe["llama_cpp_revision"]:
        raise ValueError("converter/toolchain mismatch")
    if not REPOSITORY.fullmatch(recipe.get("source_repository", "")) or not SHA256.fullmatch(recipe.get("converter_sha256", "")):
        raise ValueError("conversion provenance missing")
    relative_path(recipe["output_filename"])
    if "/" in recipe["output_filename"] or not recipe["output_filename"].endswith("-BF16.gguf"):
        raise ValueError("invalid generated output filename")
    files = recipe.get("source_files")
    if not isinstance(files, list) or not files:
        raise ValueError("conversion source hashes missing")
    paths = set()
    for item in files:
        relative_path(item["path"])
        if item["path"] in paths or type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0 or not SHA256.fullmatch(item.get("sha256", "")):
            raise ValueError("conversion source size/hash invalid or duplicate")
        paths.add(item["path"])
    if not any(path.endswith(".safetensors") for path in paths):
        raise ValueError("conversion source shards missing")
    if not {"config.json", "tokenizer.json", "tokenizer_config.json"} <= paths:
        raise ValueError("conversion config/tokenizer hashes missing")
    if sum(path.endswith(".safetensors") for path in paths) > 1 and "model.safetensors.index.json" not in paths:
        raise ValueError("sharded conversion index hash missing")


def recipe_identity(recipe: dict) -> str:
    validate_recipe(recipe)
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_generated_provenance(item: dict, receipt: dict) -> dict:
    validate_artifact(item)
    recipe = item["generation"]
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("recipe_sha256") != recipe_identity(recipe):
        raise ValueError("generated provenance missing or stale")
    if receipt.get("cpu_load_verified") is not True or receipt.get("gpu_validation") != "pending":
        raise ValueError("generated provenance requires the completed CPU load probe and pending GPU status")
    expected = {"source_repository": recipe["source_repository"], "source_revision": recipe["source_revision"],
                "source_files": recipe["source_files"], "llama_cpp_revision": recipe["llama_cpp_revision"],
                "converter_revision": recipe["converter_revision"], "converter_sha256": recipe["converter_sha256"],
                "output_filename": recipe["output_filename"], "format": "bf16"}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("generated provenance differs from pinned source/toolchain")
    command = receipt.get("conversion_command")
    # The command is evidence, never executable manifest content. Check the
    # fixed converter/options rather than accepting an arbitrary shell command.
    if not isinstance(command, list) or len(command) != 8 or any(not isinstance(value, str) for value in command):
        raise ValueError("conversion command provenance missing")
    if Path(command[1]).name != "convert_hf_to_gguf.py" or command[3] != "--outfile" or command[5:] != ["--outtype", "bf16", "--no-lazy"]:
        raise ValueError("conversion command does not produce BF16")
    if Path(command[4]).name != recipe["output_filename"]:
        raise ValueError("conversion output provenance mismatch")
    if type(receipt.get("size_bytes")) is not int or receipt["size_bytes"] <= 24 or not SHA256.fullmatch(receipt.get("sha256", "")):
        raise ValueError("generated exact output integrity missing")
    return {"size_bytes": receipt["size_bytes"], "sha256": receipt["sha256"]}


def validate_manifest(manifest: dict, *, allow_pending=False) -> dict[str, dict]:
    allowed = {"production_pinned_gpu_pending"} | ({"blocked_source_provenance"} if allow_pending else set())
    if manifest.get("schema_version") != 2 or manifest.get("status") not in allowed:
        raise ValueError("production manifest not frozen")
    if not allow_pending and manifest.get("pending_artifacts"):
        raise ValueError("production provenance is incomplete")
    entries, destinations = {}, set()
    for item in manifest.get("artifacts", []):
        validate_artifact(item)
        if item["id"] in entries or item["destination"] in destinations:
            raise ValueError("duplicate artifact identity/destination")
        entries[item["id"]] = item
        destinations.add(item["destination"])
    if not entries:
        raise ValueError("production artifacts missing")
    return entries


@contextmanager
def parent_directory(root: Path, relative: str, *, create=False):
    """Walk from the trusted root using dirfds, refusing every symlink."""
    parts = relative_path(relative).split("/")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def signature(info: os.stat_result) -> dict:
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("artifact is not a regular file")
    return {key: getattr(info, "st_" + key) for key in ("dev", "ino", "size", "mtime_ns", "ctime_ns")}


def verify_file(root: Path, relative: str, expected: dict) -> dict:
    if type(expected.get("size_bytes")) is not int or expected["size_bytes"] <= 0 or not SHA256.fullmatch(expected.get("sha256", "")):
        raise ValueError("trusted exact size/SHA256 required")
    with parent_directory(root, relative) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        try:
            before = signature(os.fstat(fd))
            if before["size"] != expected["size_bytes"]:
                raise ValueError("artifact wrong size")
            digest = hashlib.sha256()
            while block := os.read(fd, 8 * 1024 * 1024):
                digest.update(block)
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError("artifact wrong SHA256")
            if signature(os.fstat(fd)) != before or signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != before:
                raise ValueError("artifact changed during verification")
            return {"size_bytes": before["size"], "sha256": digest.hexdigest(), "signature": before}
        finally:
            os.close(fd)


def atomic_json(root: Path, relative: str, value: dict) -> None:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    with parent_directory(root, relative, create=True) as (directory, name):
        temporary = "." + name + "." + uuid.uuid4().hex + ".partial"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                target = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                target = None
            if target is not None and not stat.S_ISREG(target.st_mode):
                raise ValueError("unsafe artifact metadata target")
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def read_json(root: Path, relative: str) -> dict:
    with parent_directory(root, relative) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        try:
            info = signature(os.fstat(fd))
            if info["size"] > 2 * 1024 * 1024:
                raise ValueError("oversized artifact metadata")
            with os.fdopen(fd, "r") as handle:
                fd = None
                value = json.load(handle)
                if not isinstance(value, dict):
                    raise ValueError("invalid artifact metadata")
                return value
        finally:
            if fd is not None:
                os.close(fd)
