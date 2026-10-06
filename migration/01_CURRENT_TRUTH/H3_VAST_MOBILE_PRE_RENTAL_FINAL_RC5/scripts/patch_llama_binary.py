from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if "LINUX_CUDA = PlatformSpec" not in s:
    marker="WINDOWS_CUDA_13 = PlatformSpec("
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
        raise SystemExit("Unexpected llama_binary.py layout")
    s=s.replace(old,new)
p.write_text(s)
