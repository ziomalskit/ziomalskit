#!/usr/bin/env python3
"""Non-inference readiness checks. Missing provenance is a blocker, not a pass."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

try:
    from .runtime_config import COMFY_COMMIT
    from .llama_runtime import verify_llama
except ImportError:
    from runtime_config import COMFY_COMMIT
    from llama_runtime import verify_llama


GPU_PROBE = """import json, torch
assert torch.cuda.is_available(), 'torch CUDA unavailable'
x=torch.ones(1, device='cuda'); assert (x+1).item()==2; torch.cuda.synchronize()
p=torch.cuda.get_device_properties(0)
print(json.dumps({'cuda':torch.version.cuda, 'name':p.name, 'memory':p.total_memory,
                  'capability':list(torch.cuda.get_device_capability(0))}))
"""


def service_urls(environment: dict[str, str]) -> dict[str, str]:
    return {
        "render": environment.get("RENDER_COMFY_URL") or f"http://127.0.0.1:{environment.get('RENDER_PORT') or '8188'}",
        "prompt": environment.get("PROMPT_COMFY_URL") or f"http://127.0.0.1:{environment.get('PROMPT_PORT') or '8189'}",
        "panel": environment.get("H3_PANEL_URL") or f"http://127.0.0.1:{environment.get('H3_PANEL_PORT') or '7860'}",
    }


def get_json(url: str, environment: dict[str, str], auth: bool = False) -> dict:
    headers = {}
    if auth:
        password = environment.get("H3_PANEL_PASSWORD", "")
        if not password:
            raise ValueError("panel password is missing")
        user = environment.get("H3_PANEL_USER") or "h3"
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_model(path: Path, expected: dict) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"missing/empty model: {path.name}")
    checksum = expected.get("sha256", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError(f"no trusted SHA256 configured for model: {path.name}")
    size = expected.get("size_bytes")
    if size is not None and path.stat().st_size != size:
        raise ValueError(f"model size mismatch: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != checksum.lower():
        raise ValueError(f"model checksum mismatch: {path.name}")
    # Detect obvious wrong-format files even if a mistakenly recorded hash matches.
    with path.open("rb") as handle:
        if path.suffix == ".gguf":
            magic = handle.read(4)
            version = int.from_bytes(handle.read(4), "little")
            if magic != b"GGUF" or version not in {2, 3} or path.stat().st_size < 24:
                raise ValueError(f"invalid GGUF model: {path.name}")
        elif path.suffix == ".safetensors":
            size = int.from_bytes(handle.read(8), "little")
            if not 2 <= size <= min(16 * 1024 * 1024, path.stat().st_size - 8):
                raise ValueError(f"invalid safetensors header: {path.name}")
            header = json.loads(handle.read(size))
            tensors = [value for key, value in header.items() if key != "__metadata__"]
            available = path.stat().st_size - 8 - size
            if not tensors or any(not isinstance(value, dict) or
                not isinstance(value.get("data_offsets"), list) or len(value['data_offsets']) != 2 or
                not 0 <= value['data_offsets'][0] < value['data_offsets'][1] <= available for value in tensors):
                raise ValueError(f"invalid safetensors payload: {path.name}")


def validate_gpu(info: dict, configured_cuda: str) -> None:
    if info.get("cuda") != configured_cuda:
        raise ValueError(f"torch CUDA runtime must match configured {configured_cuda}")
    if info.get("capability") != [12, 0] or "RTX PRO 6000" not in info.get("name", ""):
        raise ValueError("target GPU must be RTX PRO 6000 Blackwell (compute capability 12.0)")
    if info.get("memory", 0) < 90 * 1024**3:
        raise ValueError("target GPU has less than the required 96 GB class VRAM")


def model_requirements(comfy: Path, manifest: dict) -> list[tuple[Path, dict]]:
    required = [(comfy / "models" / item["dir"] / item["file"], item)
                for item in manifest["required_primary"]]
    required.extend((comfy / "models" / "loras" / item["file"], item)
                    for item in manifest["loras_default_required"])
    for item in manifest.get("special_required_models", []):
        paths = [comfy / location for location in item["accepted_locations"]]
        required.append((next((path for path in paths if path.is_file()), paths[0]), item))
    return required


def run_checks(environment: dict[str, str] | None = None) -> list[str]:
    env = dict(os.environ if environment is None else environment)
    panel = Path(env.get("PANEL_ROOT") or "/workspace/H3_VAST_MOBILE")
    comfy = Path(env.get("COMFY_ROOT") or "/workspace/ComfyUI")
    python = env.get("COMFY_PYTHON") or env.get("PYTHON_BIN") or sys.executable
    configured_cuda = env.get("H3_CUDA_VERSION") or "13.0"
    errors = []

    def check(name, action):
        try:
            action()
            print(name, "OK")
        except Exception as error:
            # Never include HTTP request headers or runtime.env content in diagnostics.
            errors.append(f"{name}: {error}")

    def versions():
        subprocess.check_call([python, "-c", "import sys; sys.version_info >= (3,11) or sys.exit('Python >=3.11 required')"])
        commit = subprocess.check_output(["git", "-C", str(comfy), "rev-parse", "HEAD"], text=True, stderr=subprocess.STDOUT).strip()
        if commit != COMFY_COMMIT:
            raise ValueError("ComfyUI must match the verified v0.38.0 commit")
        cli = subprocess.check_output([python, "-c", "import importlib.metadata; print(importlib.metadata.version('comfy-cli'))"], text=True).strip()
        if cli != "1.21.0":
            raise ValueError("comfy-cli must be 1.21.0 in the selected Python")
    check("pinned versions", versions)

    def cuda():
        output = subprocess.check_output(["nvcc", "--version"], text=True)
        match = re.search(r"release\s+(\d+\.\d+)", output)
        if not match or match.group(1) != configured_cuda or tuple(map(int, configured_cuda.split('.'))) < (12, 8):
            raise ValueError(f"nvcc must match configured CUDA {configured_cuda}, >=12.8")
        info = json.loads(subprocess.check_output([python, "-c", GPU_PROBE], text=True, stderr=subprocess.PIPE, timeout=60))
        validate_gpu(info, configured_cuda)
    check("CUDA kernel and target GPU", cuda)

    def models():
        manifest = json.loads((panel / "config/models_manifest.json").read_text())
        configured = env.get("H3_MODEL_CHECKSUMS_FILE", "")
        checksums = json.loads(Path(configured).read_text()) if configured else {}
        for path, item in model_requirements(comfy, manifest):
            relative = str(path.relative_to(comfy))
            validate_model(path, {**item, **checksums.get(relative, {})})
    check("model integrity", models)
    check("LLM actual executable", lambda: verify_llama(comfy / "custom_nodes/ComfyUI-LLM-text-processor", configured_cuda))

    critical = ["LLMTextProcessor", "BunnyH3ConditioningBridge", "MinimaxH3LatentUpscaler3D",
                "MergeImageBatchAndAudioList", "Power Lora Loader (rgthree)", "Seed (rgthree)"]
    for name, base in service_urls(env).items():
        def service(name=name, base=base):
            info = get_json(base.rstrip('/') + ("/api/config" if name == "panel" else "/object_info"), env, name == "panel")
            if name != "panel":
                missing = [cls for cls in critical if cls not in info]
                if missing:
                    raise ValueError("missing backend classes: " + ", ".join(missing))
            elif not isinstance(info, dict) or "batch" not in info:
                raise ValueError("panel config response is invalid")
            else:
                state = info.get("state")
                if not isinstance(state, dict) or state.get("ready") is not True or any(
                    value for key, value in state.items() if key.endswith("error")
                ):
                    raise ValueError("controller state is unavailable; resolve queue/lifecycle errors before deployment")
                workers = state.get("workers")
                if workers is not None and (not isinstance(workers, dict) or any(
                    workers.get(name) != "running" for name in ("prompt", "render", "recovery", "lifecycle_guard")
                )):
                    raise ValueError("required controller worker is unavailable")
        check(name + " service", service)
    return errors


def main() -> int:
    errors = run_checks()
    for error in errors:
        print("ERROR:", error)
    print("RESULT:", "NOT READY" if errors else "READY FOR CONVERSION TEST — no inference performed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
