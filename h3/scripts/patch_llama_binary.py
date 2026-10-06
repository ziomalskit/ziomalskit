"""Add the Linux platform to the exact pinned node without changing its release."""
import ast
from pathlib import Path
import sys

try:
    from .runtime_config import LLAMA_TAG
except ImportError:
    from runtime_config import LLAMA_TAG


def patch_source(source: str) -> str:
    releases = [node.value.value for node in ast.parse(source).body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and any(isinstance(target, ast.Name) and target.id == "LLAMA_CPP_RELEASE_TAG"
                        for target in node.targets)]
    if releases != [LLAMA_TAG]:
        raise ValueError(f"Pinned LLM node must expect {LLAMA_TAG}; refusing a different release")
    s = source
    if "LINUX_CUDA = PlatformSpec" not in s:
        marker="WINDOWS_CUDA_13 = PlatformSpec("
        if marker not in s:
            raise ValueError("Unexpected llama_binary.py platform layout")
        i=s.index(marker)
        block="""LINUX_CUDA = PlatformSpec(
    key="linux-x64-cuda",
    cli_executable="llama-cli",
    asset_patterns=(),
    required_files=("llama-cli",),
)

"""
        s=s[:i]+block+s[i:]
    old="""    if system == "windows" and machine in {"amd64", "x86_64"}:
        return WINDOWS_CUDA_13
    raise RuntimeError("""
    new="""    if system == "windows" and machine in {"amd64", "x86_64"}:
        return WINDOWS_CUDA_13
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return LINUX_CUDA
    raise RuntimeError("""
    if "return LINUX_CUDA" not in s:
        if old not in s:
            raise ValueError("Unexpected llama_binary.py platform selection")
        s=s.replace(old,new)
    ast.parse(s)
    return s


if __name__ == "__main__":
    path = Path(sys.argv[1])
    original = path.read_text()
    patched = patch_source(original)
    if patched != original:
        path.write_text(patched)
