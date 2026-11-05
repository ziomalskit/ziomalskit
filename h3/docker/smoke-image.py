#!/usr/bin/env python3
"""CPU inspection of the complete pulled image; never start services or inference."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, "/opt/aj/ziomalskit/h3/scripts")
import runtime_dependencies
import sageattention_runtime as sage


revision = sys.argv[1]
image = json.loads(Path("/etc/aj-image.json").read_text())
assert image["revision"] == revision
assert image["gpu_smoke"] == "PENDING"
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
for module in ("torch", "torchvision", "torchaudio", "triton", "fastapi", "av", "cv2"):
    __import__(module)
panel = Path("/opt/aj/seed/H3_VAST_MOBILE")
comfy = Path("/opt/aj/seed/ComfyUI")
runtime_dependencies.verify(panel, comfy, "13.0")
sage.verify(sage.build_identity("13.0"), image_build=True)
assert not sage.receipt_path().exists(), "CPU build must never claim GPU acceptance"
for name in ("llama-cli", "llama-server"):
    binary = next((comfy / "custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp").glob(f"*/linux-x64-cuda/{name}"))
    receipt = json.loads(binary.with_suffix(".build.json").read_text())
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == receipt["sha256"]
    assert receipt["runtime_execution"] == "PENDING"
assert not list(Path("/etc/ssh").glob("ssh_host_*"))
assert not (panel / "runtime.env").exists()
assert not list((comfy / "models").rglob("*.safetensors"))
assert not list((comfy / "models").rglob("*.gguf"))
subprocess.run(["aj-docker", "plan"], check=True)
print("Complete image CPU smoke PASS; GPU kernels, model loads and renders PENDING.")
