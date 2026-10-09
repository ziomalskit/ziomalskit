"""Build pinned BF16 GGUFs; unknown source provenance stops before download."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifacts import (atomic_json, parent_directory, read_json, recipe_identity,
                           signature, validate_artifact, validate_generated_provenance, verify_file)
try:
    from .model_downloads import ensure_download, install_verified, store_lock
except ImportError:
    from model_downloads import ensure_download, install_verified, store_lock


def conversion_environment() -> dict:
    # No inherited provider credentials or panel/Vast credentials reach the
    # converter or its CPU load probe. Conversion is entirely local/offline.
    safe = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH") if key in os.environ}
    return {**safe, "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "HF_HUB_OFFLINE": "1"}


def run_private(command: list[str], *, environment: dict | None = None):
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=conversion_environment() if environment is None else environment)
    except Exception:
        raise ValueError("pinned BF16 conversion/load check failed; stop and review toolchain compatibility") from None


def verify_toolchain(toolchain: Path, recipe: dict) -> None:
    revision = subprocess.check_output(["git", "-C", str(toolchain), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    if revision != recipe["llama_cpp_revision"]:
        raise ValueError("converter requires the pinned llama.cpp revision")
    subprocess.run(["git", "-C", str(toolchain), "diff", "--quiet", "HEAD", "--"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    script = toolchain / "convert_hf_to_gguf.py"
    verify_file(toolchain, script.name, {"size_bytes": script.stat().st_size, "sha256": recipe["converter_sha256"]})
    qwen = (toolchain / "conversion/qwen.py").read_text()
    if '"Qwen3_5ForConditionalGeneration"' not in qwen or '"Qwen3_5ForCausalLM"' not in qwen:
        raise ValueError("pinned converter lacks Qwen3.5 architecture support; controlled upgrade required")


def source_items(item: dict) -> list[dict]:
    recipe = item["generation"]
    prefix = ".sources/" + item["id"] + "/" + recipe["source_revision"]
    return [{"id": item["id"] + "-source-" + str(index), "role": "Pinned BF16 conversion input",
             "provider": "huggingface", "repository": recipe["source_repository"], "revision": recipe["source_revision"],
             "repository_path": source["path"], "destination": prefix + "/" + source["path"],
             "size_bytes": source["size_bytes"], "sha256": source["sha256"], "profiles": item["profiles"],
             "required": True, "gpu_validation": "pending"} for index, source in enumerate(recipe["source_files"])]


def ensure_generated(root: Path, item: dict, toolchain: Path, *, python=sys.executable,
                     downloader=ensure_download, runner=run_private, toolchain_check=verify_toolchain) -> dict:
    validate_artifact(item)
    if item["provider"] != "generated":
        raise ValueError("expected generated artifact")
    receipt_path = ".provenance/" + item["id"] + ".json"
    staging_relative = ".build/" + item["id"] + "/" + item["generation"]["output_filename"]
    journal_path = ".build/" + item["id"] + "/provenance.json"
    # Exact output/provenance verification precedes all source downloads. A
    # future bootstrap needs neither credentials nor conversion dependencies.
    with store_lock(root):
        try:
            receipt = read_json(root, receipt_path)
        except FileNotFoundError:
            try:
                journal = read_json(root, journal_path)
            except FileNotFoundError:
                journal = None
            if journal is not None:
                expected = validate_generated_provenance(item, journal)
                if journal.get("cpu_load_verified") is not True:
                    raise ValueError("generated journal has no completed CPU load check")
                try:
                    installed = verify_file(root, item["destination"], expected)
                except FileNotFoundError:
                    output = root / staging_relative
                    installed = install_verified(root, {**item, **expected}, output.parent, output)
                journal.update(signature=installed["signature"])
                atomic_json(root, receipt_path, journal)
                remove_build_output(root, staging_relative)
                return {"artifact_id": item["id"], "status": "verified_file", "reused": True, **expected, "gpu_validation": "pending"}
            with parent_directory(root, item["destination"], create=True) as (directory, name):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("generated output exists without trusted provenance")
        else:
            expected = validate_generated_provenance(item, receipt)
            verify_file(root, item["destination"], expected)
            remove_build_output(root, staging_relative)
            return {"artifact_id": item["id"], "status": "verified_file", "reused": True, **expected, "gpu_validation": "pending"}
    recipe = item["generation"]
    toolchain_check(toolchain, recipe)
    inputs = source_items(item)
    for source in inputs:
        downloader(root, source)
    with store_lock(root):
        # A competing bootstrap may have completed while source files arrived.
        try:
            receipt = read_json(root, receipt_path)
        except FileNotFoundError:
            pass
        else:
            expected = validate_generated_provenance(item, receipt)
            verify_file(root, item["destination"], expected)
            return {"artifact_id": item["id"], "status": "verified_file", "reused": True, **expected, "gpu_validation": "pending"}
        for source in inputs:
            verify_file(root, source["destination"], source)
        source_root = root / ".sources" / item["id"] / recipe["source_revision"]
        config = read_json(source_root, "config.json")
        if config.get("architectures") not in (["Qwen3_5ForConditionalGeneration"], ["Qwen3_5ForCausalLM"]):
            raise ValueError("exact source architecture differs from supported Qwen3.5; stop conversion")
        dtype = config.get("dtype", config.get("torch_dtype", config.get("text_config", {}).get("dtype")))
        if dtype != "bfloat16":
            raise ValueError("canonical conversion source is not BF16")
        if config.get("quantization_config") or config.get("text_config", {}).get("quantization_config"):
            raise ValueError("quantized conversion source cannot replace canonical BF16")
        shards = {source["path"] for source in recipe["source_files"] if source["path"].endswith(".safetensors")}
        if len(shards) > 1:
            index = read_json(source_root, "model.safetensors.index.json")
            if set(index.get("weight_map", {}).values()) != shards:
                raise ValueError("source shard index differs from pinned hashes")
        estimate = sum(source["size_bytes"] for source in recipe["source_files"] if source["path"].endswith(".safetensors")) * 11 // 10
        if shutil.disk_usage(root).free < estimate * 2:
            raise ValueError("insufficient persistent disk space for BF16 output staging and installation")
        with parent_directory(root, staging_relative, create=True) as (directory, filename):
            try:
                os.stat(filename, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("partial/stale generated output exists; remove the failed build explicitly")
        output = root / staging_relative
        command = [python, str(toolchain / "convert_hf_to_gguf.py"), str(source_root),
                   "--outfile", str(output), "--outtype", "bf16", "--no-lazy"]
        runner(command)
        with parent_directory(root, staging_relative) as (directory, filename):
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as handle:
                before = signature(os.fstat(handle.fileno()))
                if handle.read(4) != b"GGUF" or int.from_bytes(handle.read(4), "little") not in (2, 3):
                    raise ValueError("converter did not produce GGUF")
                handle.seek(0)
                size, digest = 0, hashlib.sha256()
                while block := handle.read(8 * 1024 * 1024):
                    size += len(block)
                    digest.update(block)
                if signature(os.fstat(handle.fileno())) != before:
                    raise ValueError("generated output changed during hashing")
        # Check the exact pinned runtime on CPU, with no warmup or generated
        # tokens. This never performs a real H3/10Eros render or GPU inference.
        runner([str(toolchain / "build/bin/llama-cli"), "--model", str(output), "--gpu-layers", "0",
                "--ctx-size", "128", "--n-predict", "0", "--no-warmup", "--no-conversation"])
        receipt = {"schema_version": 1, "recipe_sha256": recipe_identity(recipe),
                   **{key: recipe[key] for key in ("source_repository", "source_revision", "source_files", "llama_cpp_revision",
                       "converter_revision", "converter_sha256", "output_filename", "format")},
                   "conversion_command": command, "size_bytes": size, "sha256": digest.hexdigest(),
                   "cpu_load_verified": True, "gpu_validation": "pending"}
        validate_generated_provenance(item, receipt)
        # Publish completed conversion evidence first. Either side of the
        # following file publication can be resumed without another conversion.
        atomic_json(root, journal_path, receipt)
        installed = install_verified(root, {**item, "size_bytes": size, "sha256": digest.hexdigest()}, output.parent, output)
        receipt.update(signature=installed["signature"], cpu_load_verified=True)
        atomic_json(root, receipt_path, receipt)
        remove_build_output(root, staging_relative)
        return {"artifact_id": item["id"], "status": "verified_file", "reused": False,
                "size_bytes": size, "sha256": digest.hexdigest(), "gpu_validation": "pending"}


def remove_build_output(root: Path, relative: str) -> None:
    try:
        with parent_directory(root, relative) as (directory, name):
            try:
                signature(os.stat(name, dir_fd=directory, follow_symlinks=False))
                os.unlink(name, dir_fd=directory)
                os.fsync(directory)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
