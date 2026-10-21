# Required MiniMax SageAttention installation

The normal installer automatically builds/reuses SageAttention and executes a
small GPU kernel smoke **before calling the model downloader**. There is no
attention-mode change or fallback. The shared `COMFY_PYTHON`, ComfyUI v0.38.0,
all node revisions and the canonical workflow stay unchanged.

## Reviewed source and build

Use official [SageAttention v2.2.0](https://github.com/thu-ml/SageAttention/tree/eb615cf6cf4d221338033340ee2de1c37fbdba4a),
immutable commit `eb615cf6cf4d221338033340ee2de1c37fbdba4a`, not moving main or a
third-party wheel. Its `setup.py` explicitly accepts `TORCH_CUDA_ARCH_LIST=12.0`,
emits `compute_120/sm_120` and builds `_fused`, `_qattn_sm80`, `_qattn_sm89`.
It requires CUDA >=12.8 for this target and takes the C++11 ABI from torch.
Its `core.py` exports every API imported by pinned KJNodes. Its compiled sm89
binding includes `qk_int8_sv_f8_accum_f16_fuse_v_scale_attn_inst_buf`.

Pinned KJNodes `3f20054214fec9f9234fd3841ae6f1e4287948f6`,
`nodes/ltxv_nodes.py`, selects that sm89 fp8 kernel on **sm120** (the module name
is not the build architecture). `_sageattn_int8_fp8_nhd` uses CUDA
`per_warp_int8_cuda`, `per_channel_fp8` with scale_max=2.25 and fp32+fp16 PV.
`_resolve_qattn` supports this release's `sm89_compile` wrapper and underlying
compiled extension. These are concrete API/source selection evidence, **not
proof that CUDA 13 / torch 2.11 can build or execute it on the target**.

`sageattention_runtime.py` fetches the exact commit, verifies origin/HEAD and
pristine source, then uses `pip wheel --no-build-isolation --no-deps
--no-cache-dir` and installs only that wheel with `--no-deps`. This builds
against the selected torch rather than an isolated build environment's torch.
No torch/vision/audio/NVIDIA/application upgrade is requested; existing package
versions are compared before/after. The default remains torch 2.11.0+cu130,
CUDA toolkit/runtime 13.0; the existing cu128/cu129 profiles are retained.
Python 3.11/3.12, Linux x86_64, visible sm120, nvcc matching torch CUDA, and a
C++ compiler are required before building. Compiler/build errors stop installation.

Existing setuptools/wheel/packaging are reused. Only missing build helpers are
installed, with fixed versions and `--no-deps`; no installed helper is downgraded
to the old isolation bounds in upstream `pyproject.toml`. Actual setup imports
their ordinary setup/version APIs. Upstream supports `EXT_PARALLEL` separately
from ninja's `MAX_JOBS`; AJ fixes extensions to one and bounds workers to 1–2
using existing CPU/RAM/job limits. Upstream also hardcodes eight nvcc threads,
so there are at most two such compiler processes. Without ninja, upstream uses
its serial distutils fallback. Inherited extra compiler flags and skip-CUDA
selection cannot silently change the approved build plan.

An AJ receipt inside the shared venv records source revision, Python/SOABI,
torch/CUDA/Triton, C++ ABI, compiler/libc identity and hashes of installed Python,
compiled extension and distribution metadata files. Reprovisioning reuses a
matching build and repeats the GPU smoke. Changed/unowned/incomplete installs
fail clearly; explicitly remove only `sageattention` or select a clean shared
venv, then rerun. No unknown base-image Sage package is silently overwritten.
Failed/interrupted builds clean temporary sources; a post-install failure has
no valid receipt and cannot be mistaken for success on retry.

## Real GPU probe before any weights

For the optional first-GGUF preparation in Personal First Run, run first:

```bash
H3_INSTALL_VAST_CLI=0 bash h3/INSTALL_ON_VAST.sh --dependencies-only
```

This performs the normal pinned Comfy/node/native/dependency setup and Sage
probe, then exits before downloads, runtime credential creation or services.
The later full installer reuses the verified Sage build. A normal full install
also always runs this stage automatically before its downloader.

The probe loads **the actual pinned KJ functions** by AST, avoiding unrelated
Comfy/model module initialization. Both Git revision and the complete source
file SHA256 are checked. It invokes `minimax_sageattn_forward` with fused strided
QKV, 56 heads x 128, BF16 and FP16, sequence lengths 128/257, and head chunks 1/7.
The projection/norm stubs isolate the attention dependency; RoPE, model loading
and full inference are outside this probe. It synchronizes CUDA, checks shape
and finite output, and requires relative RMSE <=0.06 and cosine >=0.99 against
explicit FP32 softmax attention. The exact per-warp quantization/fp8/compiled
kernel branch must execute. Production preflight repeats receipt/ABI and GPU
checks; import or `/object_info` success alone is insufficient.

CUDA Graph investigation: canonical v20 has no TorchCompile node and
`PathchSageAttentionKJ` uses `auto, allow_compile=False`. At pinned Comfy commit,
MiniMax `model.py` calls `prefetch_queue_pop` without `core` or `enable_graph`;
their defaults do not capture/replay attention. MiniMax's `malloc_graph_begin`
tracks allocations, not CUDA kernel replay. Other text-model graph paths in
ComfyUI are unrelated. No graph subsystem or speculative replay requirement is
added; revisit this evidence if the Comfy/model/attention path changes.

Offline tests use inert build/GPU doubles, never certify CUDA execution. Actual
source compilation and this GPU probe are **NOT RUN** without a target GPU and
toolkit. They must pass before the first model transfer. Passing them still
does not certify long-sequence VRAM, full H3/10Eros rendering or performance.
No models, rentals or paid inference are used by offline verification.

For an explicit future Sage upgrade, review the source APIs/build flags, update
the immutable revision/version and pip constraint, then repeat offline tests
and target GPU acceptance. Old receipts fail rather than adopting latest.
