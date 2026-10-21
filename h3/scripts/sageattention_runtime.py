"""AJ-owned SageAttention source build and model-free MiniMax kernel acceptance."""
from __future__ import annotations

import ast
import fcntl
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from types import SimpleNamespace

try:
    from .runtime_config import build_parallelism
except ImportError:
    from runtime_config import build_parallelism

SOURCE = "https://github.com/thu-ml/SageAttention.git"
REVISION = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"  # official v2.2.0
VERSION = "2.2.0"
KJ_REVISION = "3f20054214fec9f9234fd3841ae6f1e4287948f6"
KJ_SOURCE_SHA256 = "11286583c56e653dbd57f5e8eda6902c84b759f16b359b3bf0c895b43e6b5364"
BUILD_TOOLS = {"setuptools": "74.1.2", "wheel": "0.43.0", "packaging": "23.2"}


def output(args):
    return subprocess.check_output(args, text=True).strip()


def run(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def packages():
    return {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version
            for d in metadata.distributions()}


def unchanged(before, after):
    changed = [name for name, version in before.items()
               if name != "sageattention" and after.get(name) != version]
    if changed:
        raise ValueError(f"SageAttention setup changed existing dependencies: {changed}")


def runtime_identity(cuda):
    import torch
    import triton
    from torch.utils.cpp_extension import CUDA_HOME
    if sys.version_info[:2] not in {(3, 11), (3, 12)} or platform.machine() != "x86_64":
        raise ValueError("SageAttention requires reviewed Linux x86_64 Python 3.11/3.12")
    if platform.system() != "Linux" or cuda not in {"12.8", "12.9", "13.0"}:
        raise ValueError("Unsupported SageAttention platform/CUDA selection")
    expected = f"2.11.0+cu{cuda.replace('.', '')}"
    if torch.__version__ != expected or torch.version.cuda != cuda:
        raise ValueError(f"SageAttention requires existing torch {expected}, CUDA {cuda}; will not replace it")
    if not torch.cuda.is_available() or torch.cuda.get_device_capability(0) != (12, 0):
        raise ValueError("SageAttention setup requires a visible Blackwell sm_120 GPU")
    nvcc = Path(CUDA_HOME or "/missing-cuda") / "bin/nvcc"
    if not nvcc.is_file() or not shutil.which("c++"):
        raise ValueError("SageAttention needs CUDA devel nvcc and a C++ compiler; no packages were replaced")
    compiler = output([str(nvcc), "--version"])
    if not re.search(r"release\s+" + re.escape(cuda) + r"\b", compiler):
        raise ValueError("SageAttention nvcc toolkit must match selected torch CUDA runtime")
    return dict(source=SOURCE, revision=REVISION, version=VERSION, arch="12.0",
                python=platform.python_version(), soabi=sysconfig.get_config_var("SOABI"),
                prefix=str(Path(sys.prefix).resolve()), torch=torch.__version__, cuda=cuda,
                triton=triton.__version__, cxx11_abi=bool(torch._C._GLIBCXX_USE_CXX11_ABI),
                nvcc=compiler, cxx=output(["c++", "--version"]), libc=list(platform.libc_ver()))


def installed_files():
    dist = metadata.distribution("sageattention")
    if dist.version != VERSION:
        raise ValueError("Installed SageAttention version differs from the approved source")
    files = {}
    for relative in dist.files or []:
        if str(relative).endswith((".py", ".so", "/METADATA", "/WHEEL", "/RECORD")):
            path = Path(dist.locate_file(relative)).resolve()
            if not path.is_relative_to(Path(sys.prefix).resolve()):
                raise ValueError("SageAttention must be installed inside selected COMFY_PYTHON")
            files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not any("_qattn_sm89" in p and p.endswith(".so") for p in files) or not any(
            "_fused" in p and p.endswith(".so") for p in files):
        raise ValueError("SageAttention compiled sm89/fused extensions are missing")
    return files


def receipt_path():
    return Path(sys.prefix) / ".aj-sageattention.json"


def verify(identity):
    receipt = receipt_path()
    if not receipt.is_file():
        raise ValueError("Unowned/incomplete SageAttention installation: use a clean shared venv or explicitly uninstall sageattention and rerun provisioning")
    saved = json.loads(receipt.read_text())
    if saved.get("runtime") != identity or saved.get("files") != installed_files():
        raise ValueError("SageAttention source/build/runtime differs from AJ receipt; select a clean shared venv or explicitly uninstall sageattention and rerun")


def checked_source(source):
    if output(["git", "-C", str(source), "rev-parse", "HEAD"]) != REVISION:
        raise ValueError("SageAttention source revision mismatch")
    if output(["git", "-C", str(source), "config", "--get", "remote.origin.url"]) != SOURCE:
        raise ValueError("SageAttention source origin mismatch")
    if output(["git", "-C", str(source), "status", "--porcelain"]):
        raise ValueError("SageAttention build source is not pristine")


def smoke_command(comfy):
    run([sys.executable, str(Path(__file__).resolve()), "smoke", str(comfy)])


def install(comfy, build_root, cuda):
    before = packages()
    if "sageattention" in before:
        verify(runtime_identity(cuda))
        smoke_command(comfy)
        print("Reusing AJ-verified SageAttention", REVISION)
        return
    # A failed/interrupted attempt must never leave an apparently valid receipt.
    receipt_path().unlink(missing_ok=True)
    for name, version in BUILD_TOOLS.items():
        if name not in before:
            run([sys.executable, "-m", "pip", "install", "--no-deps", f"{name}=={version}"])
    unchanged(before, packages())
    # torch.utils.cpp_extension imports setuptools; bootstrap missing helpers
    # before loading it on Python 3.12, whose fresh venv omits setuptools.
    identity = runtime_identity(cuda)
    build_root.mkdir(parents=True, exist_ok=True)
    # Upstream setup.py runs extensions in parallel as well as ninja workers;
    # each nvcc also has --threads=8. Bound the multipliers, not just MAX_JOBS.
    memory = next((int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                   if line.startswith("MemAvailable:")), 3 * 1024**3)
    env = dict(os.environ, TORCH_CUDA_ARCH_LIST="12.0", EXT_PARALLEL="1",
               MAX_JOBS=str(min(2, build_parallelism(os.environ.get("H3_BUILD_JOBS"), os.cpu_count() or 1, memory))),
               CXX=shutil.which("c++"),
               SAGEATTN_SKIP_CUDA_BUILD="0")
    for key in ("CXX_APPEND_FLAGS", "NVCC_APPEND_FLAGS", "NVCC_PREPEND_FLAGS", "CFLAGS", "CXXFLAGS", "NVCC_FLAGS"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="sageattention-", dir=build_root) as temporary:
        source = Path(temporary) / "source"
        run(["git", "init", "-q", str(source)])
        run(["git", "-C", str(source), "remote", "add", "origin", SOURCE])
        run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", REVISION])
        run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
        checked_source(source)
        wheels = Path(temporary) / "wheels"
        run([sys.executable, "-m", "pip", "wheel", "--no-build-isolation", "--no-deps",
             "--no-cache-dir", "--wheel-dir", str(wheels), str(source)], env=env)
        built = list(wheels.glob("sageattention-*.whl"))
        if len(built) != 1:
            raise ValueError("Pinned SageAttention build did not produce exactly one wheel")
        run([sys.executable, "-m", "pip", "install", "--no-deps", str(built[0])])
    unchanged(before, packages())
    files = installed_files()
    smoke_command(comfy)  # Fresh process: never reuse a cached pre-install module.
    saved = json.dumps(dict(runtime=identity, files=files), indent=2) + "\n"
    target = receipt_path()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(saved)
    temporary.replace(target)
    print("Installed and GPU-verified SageAttention", REVISION)


def kj_functions(comfy, namespace):
    node = comfy / "custom_nodes/ComfyUI-KJNodes"
    if output(["git", "-C", str(node), "rev-parse", "HEAD"]) != KJ_REVISION:
        raise ValueError("MiniMax Sage smoke requires the approved KJNodes revision")
    path = node / "nodes/ltxv_nodes.py"
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != KJ_SOURCE_SHA256:
        raise ValueError("MiniMax Sage source differs from the reviewed pinned KJNodes file")
    # Load the actual upstream functions without importing unrelated Comfy/model
    # loaders. sm120 uses no KJ Triton kernels; the CUDA quant/fp8 path is intact.
    names = {"_resolve_qattn", "_sageattn_int8_fp8_nhd", "minimax_sageattn_forward"}
    tree = ast.parse(source, filename=str(path))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names
                or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_QATTN_PROBE" for t in n.targets)]
    if len(selected) != 4:
        raise ValueError("Required MiniMax Sage functions were not found")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def smoke(comfy):
    import torch
    from sageattention import sageattn
    from sageattention import core
    from sageattention import _fused, _qattn_sm89
    if not torch.cuda.is_available() or core.get_cuda_arch_versions()[0] != "sm120":
        raise ValueError("MiniMax Sage kernel smoke requires Blackwell sm_120")
    names = ("per_thread_int8_triton", "per_warp_int8_cuda", "per_block_int8_triton",
             "per_channel_fp8", "get_cuda_arch_versions", "attn_false")
    namespace = dict(torch=torch, importlib=importlib, _cuda_archs=core.get_cuda_arch_versions())
    namespace.update({name: getattr(core, name) for name in names})
    ns = kj_functions(comfy, namespace)
    qattn = ns["_resolve_qattn"]("sm89")
    symbol = "qk_int8_sv_f8_accum_f16_fuse_v_scale_attn_inst_buf"
    if qattn is None or not hasattr(_qattn_sm89, symbol) or not hasattr(qattn, symbol):
        raise ValueError("MiniMax Blackwell Sage compiled fp8 attention symbol missing")
    ns.update(_qattn_sm89=qattn, sageplus_sm89_available=True)
    # Fused QKV views match MiniMax's noncontiguous stride, 56 heads x 128.
    # Cover BF16/FP16, padded tails, and the existing head-group slicing path.
    torch.manual_seed(812)
    results = []
    with torch.inference_mode():
        for dtype, length, chunks in ((torch.bfloat16, 128, 1), (torch.bfloat16, 257, 7),
                                       (torch.float16, 257, 1)):
            packed = torch.randn(length, 3 * 56 * 128, device="cuda", dtype=dtype)
            q, k, v = [x.view(1, length, 56, 128).transpose(1, 2).float()
                       for x in packed.split(56 * 128, dim=-1)]
            reference = ((q @ k.transpose(-2, -1) / (128 ** 0.5)).softmax(-1) @ v)
            reference = reference.transpose(1, 2).reshape(length, 56 * 128)
            attention = SimpleNamespace(heads=56, head_dim=128, qkv_proj=lambda x: x,
                                        q_norm=lambda x: x, k_norm=lambda x: x, out_proj=lambda x: x)
            actual = ns["minimax_sageattn_forward"](attention, packed,
                        transformer_options={"minimax_head_chunks": chunks})
            torch.cuda.synchronize()
            if actual.shape != reference.shape or not torch.isfinite(actual).all().item():
                raise ValueError("MiniMax Sage kernel produced invalid/nonfinite output")
            error = ((actual.float() - reference).square().mean() / reference.square().mean()).sqrt().item()
            cosine = torch.nn.functional.cosine_similarity(actual.float().flatten(), reference.flatten(), dim=0).item()
            if error > 0.06 or cosine < 0.99:
                raise ValueError(f"MiniMax Sage kernel numerical mismatch: relative_RMSE={error}, cosine={cosine}")
            results.append(dict(dtype=str(dtype), sequence=length, chunks=chunks,
                                relative_rmse=error, cosine=cosine))
    print(json.dumps(dict(minimax_sage="PASS", cases=results, cuda_graph="not used by canonical path")))


def main():
    action, comfy = sys.argv[1], Path(sys.argv[2])
    if action == "smoke":
        smoke(comfy)
    elif action == "verify":
        verify(runtime_identity(sys.argv[3]))
        smoke(comfy)
    elif action == "install":
        with (Path(sys.prefix) / ".aj-sageattention.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            install(comfy, Path(sys.argv[3]), sys.argv[4])
    else:
        raise ValueError("Expected install COMFY BUILD_ROOT CUDA, verify COMFY CUDA, or smoke COMFY")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"SageAttention setup/verification failed before model readiness: {error}")
