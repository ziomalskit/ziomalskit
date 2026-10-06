"""Verify the node's actual Linux executable without any automatic download."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from .runtime_config import LLAMA_TAG, LLAMA_COMMIT
except ImportError:
    from runtime_config import LLAMA_TAG, LLAMA_COMMIT


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_llama(node: Path, cuda_version: str | None = None) -> Path:
    source = node / "llama_binary.py"
    spec = importlib.util.spec_from_file_location("h3_pinned_llama_binary", source)
    if spec is None or spec.loader is None:
        raise ValueError("LLM binary selector is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.LLAMA_CPP_RELEASE_TAG != LLAMA_TAG:
        raise ValueError(f"LLM node expects a different llama.cpp release from {LLAMA_TAG}")
    platform = module._platform_spec()
    if platform.key != "linux-x64-cuda":
        raise ValueError("LLM node is not configured for Linux CUDA")
    existing = module._existing_install(platform)
    if existing is None:
        raise ValueError("LLM node cannot find its Linux executable; automatic downloads are disabled during checks")
    expected = node / "vendor" / "llama.cpp" / LLAMA_TAG / platform.key / "llama-cli"
    if existing.cli.resolve() != expected.resolve() or not expected.is_file():
        raise ValueError("LLM node selected an unexpected executable")
    metadata = json.loads(expected.with_suffix(".build.json").read_text())
    if metadata.get("commit") != LLAMA_COMMIT or metadata.get("tag") != LLAMA_TAG:
        raise ValueError("llama.cpp build provenance does not match the pinned release")
    expected_cuda = cuda_version or os.environ.get("H3_CUDA_VERSION") or "13.0"
    if metadata.get("cuda") != expected_cuda or metadata.get("arch") != "120":
        raise ValueError("llama.cpp CUDA build must match the configured runtime and Blackwell architecture")
    if metadata.get("sha256") != sha256(expected):
        raise ValueError("llama.cpp executable checksum differs from verified build")
    output = subprocess.check_output([str(existing.cli), "--version"], text=True, stderr=subprocess.STDOUT, timeout=30)
    if LLAMA_COMMIT[:7] not in output:
        raise ValueError("Running llama.cpp reports a different source commit")
    return existing.cli


if __name__ == "__main__":
    print("Verified LLM executable:", verify_llama(Path(sys.argv[1])))
