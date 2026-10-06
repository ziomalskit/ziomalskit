#!/usr/bin/env bash
set -euo pipefail
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
PYTHON_BIN="${COMFY_PYTHON:-${PYTHON_BIN:-$(command -v python)}}"
LLM_NODE="$COMFY_ROOT/custom_nodes/ComfyUI-LLM-text-processor"
[[ -d "$LLM_NODE" ]] || { echo "LLM node missing: $LLM_NODE" >&2; exit 1; }
command -v nvcc >/dev/null || { echo "nvcc missing: use CUDA devel image >=12.8" >&2; exit 1; }
CUDA_VERSION="$(nvcc --version | sed -n 's/.*release \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1.\2/p' | head -1)"
"$PYTHON_BIN" - "$CUDA_VERSION" <<'EOF'
import sys
v=tuple(int(x) for x in sys.argv[1].split('.'))
if v < (12,8): raise SystemExit(f'CUDA >=12.8 required, got {v}')
EOF
BUILD_ROOT="${H3_BUILD_ROOT:-/tmp/h3-build}"
SRC="$BUILD_ROOT/llama.cpp"
rm -rf "$SRC"; mkdir -p "$BUILD_ROOT"
git clone --depth 1 --branch b8840 https://github.com/ggml-org/llama.cpp.git "$SRC"
cmake -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build "$SRC/build" --config Release -j"$(nproc)" --target llama-cli
VENDOR="$LLM_NODE/vendor/llama.cpp/b8840/linux-x64-cuda"
mkdir -p "$VENDOR"
cp "$SRC/build/bin/llama-cli" "$VENDOR/llama-cli"
chmod +x "$VENDOR/llama-cli"
"$PYTHON_BIN" "$PANEL_ROOT/scripts/patch_llama_binary.py" "$LLM_NODE/llama_binary.py"
"$VENDOR/llama-cli" --version | head -3
