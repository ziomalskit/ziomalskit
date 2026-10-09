#!/usr/bin/env bash
set -euo pipefail
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python
LLM_NODE="$COMFY_ROOT/custom_nodes/ComfyUI-LLM-text-processor"
[[ -d "$LLM_NODE" ]] || { echo "LLM node missing: $LLM_NODE" >&2; exit 1; }
command -v nvcc >/dev/null || { echo "nvcc missing: use a CUDA devel image >=12.8" >&2; exit 1; }
CUDA_VERSION="$(nvcc --version | sed -n 's/.*release \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1.\2/p' | head -1)"
H3_CUDA_VERSION="${H3_CUDA_VERSION:-13.0}"
"$COMFY_PYTHON" - "$CUDA_VERSION" "$H3_CUDA_VERSION" <<'PY'
import sys
actual=tuple(map(int,sys.argv[1].split('.')))
expected=tuple(map(int,sys.argv[2].split('.')))
if actual < (12,8) or actual != expected:
    raise SystemExit(f'CUDA toolkit {actual} must match configured {expected}, >=12.8')
PY
LLAMA_TAG="$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" constant LLAMA_TAG)"
LLAMA_COMMIT="$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" constant LLAMA_COMMIT)"
LLM_COMMIT="$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" constant LLM_NODE_COMMIT)"
[[ "$(git -C "$LLM_NODE" rev-parse HEAD)" == "$LLM_COMMIT" ]] || { echo "LLM node commit differs from the pinned workflow" >&2; exit 1; }
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/patch_llama_binary.py" "$LLM_NODE/llama_binary.py"

BUILD_ROOT="${H3_BUILD_ROOT:-${WORKSPACE:-/workspace}/.h3-build}"
SRC="$BUILD_ROOT/llama.cpp-$LLAMA_TAG"
mkdir -p "$BUILD_ROOT"
if [[ ! -d "$SRC/.git" ]]; then
  [[ ! -e "$SRC" ]] || { echo "Build source directory is not an expected Git checkout: $SRC" >&2; exit 1; }
  git clone --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp.git "$SRC"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$LLAMA_COMMIT" ]] || { echo "llama.cpp tag commit failed verification" >&2; exit 1; }
git -C "$SRC" diff --quiet HEAD --
JOBS="$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" build-jobs)"
BIN="$SRC/build/bin/llama-cli"
SIGNATURE="$SRC/build/.h3-build.json"
SERVER_BIN="$SRC/build/bin/llama-server"
SERVER_SIGNATURE="$SRC/build/.h3-server-build.json"
if [[ -x "$BIN" && -x "$SERVER_BIN" ]] && "$COMFY_PYTHON" - "$SIGNATURE" "$BIN" "$SERVER_SIGNATURE" "$SERVER_BIN" "$LLAMA_COMMIT" "$CUDA_VERSION" <<'PY'
import hashlib,json,sys
from pathlib import Path
try:
    valid=True
    for receipt,binary in ((sys.argv[1],sys.argv[2]),(sys.argv[3],sys.argv[4])):
        meta=json.loads(Path(receipt).read_text())
        valid=valid and meta['commit']==sys.argv[5] and meta['cuda']==sys.argv[6] and meta['arch']=='120'
        valid=valid and hashlib.sha256(Path(binary).read_bytes()).hexdigest()==meta['sha256']
except (OSError,KeyError,ValueError): valid=False
raise SystemExit(0 if valid else 1)
PY
then
  echo "Reusing verified llama.cpp $LLAMA_TAG build"
else
  echo "Building llama.cpp $LLAMA_TAG with $JOBS parallel jobs"
  cmake -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=120
  cmake --build "$SRC/build" --config Release --parallel "$JOBS" --target llama-cli llama-server
fi
VENDOR="$LLM_NODE/vendor/llama.cpp/$LLAMA_TAG/linux-x64-cuda"
mkdir -p "$VENDOR"
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/install_llama.py" "$BIN" "$SIGNATURE" "$VENDOR/llama-cli" "$LLAMA_TAG" "$LLAMA_COMMIT" "$CUDA_VERSION"
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/install_llama.py" "$SERVER_BIN" "$SERVER_SIGNATURE" "$VENDOR/llama-server" "$LLAMA_TAG" "$LLAMA_COMMIT" "$CUDA_VERSION"
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/llama_runtime.py" "$LLM_NODE"
