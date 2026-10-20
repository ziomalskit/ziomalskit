"""Small, read-only checks for the deployment's reviewed dependency pins."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

try:
    from .runtime_config import LLM_NODE_COMMIT
except ImportError:
    from runtime_config import LLM_NODE_COMMIT


def constraint_file(panel: Path, cuda: str) -> Path:
    if cuda not in {"12.8", "12.9", "13.0"}:
        raise ValueError("No reviewed dependency constraints for configured CUDA")
    path = panel / f"constraints-cu{cuda}.txt"
    if not path.is_file():
        raise ValueError("Runtime dependency constraints are missing")
    return path


def pinned_versions(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("-c "):
            result.update(pinned_versions(path.parent / line[3:].strip()))
        elif line:
            name, version = line.split("==")
            result[name] = version
    return result


def verify(panel: Path, comfy: Path, cuda: str) -> None:
    for name, expected in pinned_versions(constraint_file(panel, cuda)).items():
        if importlib.metadata.version(name) != expected:
            raise ValueError(f"{name} differs from the reviewed runtime pin")
    manifest = json.loads((panel / "config/custom_nodes_manifest.json").read_text())
    specs = manifest["workflow_git"] + manifest["explicit_fallback_git"] + [{
        "folder": "ComfyUI-LLM-text-processor", "revision": LLM_NODE_COMMIT,
        "repo": "https://github.com/KingManiya/ComfyUI-LLM-text-processor.git"}]
    for spec in specs:
        node = comfy / "custom_nodes" / spec["folder"]
        for args, expected in ((["rev-parse", "HEAD"], spec["revision"]),
                               (["config", "--get", "remote.origin.url"], spec["repo"])):
            actual = subprocess.check_output(["git", "-C", str(node), *args], text=True, timeout=15).strip()
            if actual != expected:
                raise ValueError(f"Custom node {spec['folder']} differs from the reviewed source pin")


if __name__ == "__main__":
    if sys.argv[1] == "constraint-uri":
        # pip splits PIP_CONSTRAINT on whitespace; a file URI preserves spaces.
        print(constraint_file(Path(sys.argv[2]), sys.argv[3]).resolve().as_uri())
    elif sys.argv[1] == "verify":
        verify(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("Expected constraint-uri or verify")
