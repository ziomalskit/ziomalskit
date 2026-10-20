#!/usr/bin/env bash
set -euo pipefail
set +x
# Drop any inherited export attribute before storing the child-scoped token.
unset AJ_ACCEPTANCE_DOWNLOAD_TOKEN
AJ_ACCEPTANCE_DOWNLOAD_TOKEN="${HF_TOKEN:-}"
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN VAST_API_KEY
export H3_ALLOW_SUBMISSIONS=0 H3_ALLOW_DIAGNOSTIC_SUBMISSIONS=0 H3_ENABLE_TERMINAL=0
export PIP_NO_CACHE_DIR="${PIP_NO_CACHE_DIR:-1}"
export HF_HOME="${HF_HOME:-${WORKSPACE:-/workspace}/.cache/huggingface}"
export VAST_CLI=/usr/bin/false SERVICE_CTL=/usr/bin/false

AJ_ACCEPTANCE_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AJ_ACCEPTANCE_ROOT="${H3_MODEL_ACCEPTANCE_ROOT:?Select an absolute, private model acceptance directory distinct from production}"
# Share a pinned native build only when the physical models store is shared.
AJ_ACCEPTANCE_BUILD_ROOT="$AJ_ACCEPTANCE_ROOT/.h3-build"
if [[ -n "${H3_MODEL_ACCEPTANCE_MODELS_ROOT:-}" ]]; then
  AJ_ACCEPTANCE_BUILD_ROOT="${H3_BUILD_ROOT:-${WORKSPACE:-/workspace}/.h3-build}"
fi
AJ_ACCEPTANCE_BOOTSTRAP="${PYTHON_BIN:-$(command -v python3)}"
AJ_ACCEPTANCE_ACTION="${1:---plan}"
[[ "$#" -le 1 ]] || { echo 'Usage: INSTALL_MODEL_ACCEPTANCE.sh [--plan|--install]' >&2; exit 2; }
case "$AJ_ACCEPTANCE_ACTION" in --plan|--install) ;; *) echo 'Unknown acceptance action' >&2; exit 2;; esac

# Validation has no downloads, model generation, package installation or writes.
"$AJ_ACCEPTANCE_BOOTSTRAP" "$AJ_ACCEPTANCE_PACKAGE/scripts/model_acceptance.py" --root "$AJ_ACCEPTANCE_ROOT" --plan
[[ "$AJ_ACCEPTANCE_ACTION" == --install ]] || exit 0
"$AJ_ACCEPTANCE_BOOTSTRAP" "$AJ_ACCEPTANCE_PACKAGE/scripts/model_acceptance.py" --root "$AJ_ACCEPTANCE_ROOT" --prepare

exec 9<>"$AJ_ACCEPTANCE_ROOT/.environment.lock"
flock -x 9
AJ_ACCEPTANCE_PYTHON="$AJ_ACCEPTANCE_ROOT/.h3-venv/bin/python"
if [[ ! -x "$AJ_ACCEPTANCE_PYTHON" ]]; then
  "$AJ_ACCEPTANCE_BOOTSTRAP" -m venv "$AJ_ACCEPTANCE_ROOT/.h3-venv" 9>&-
fi
# A reused or inherited production interpreter cannot receive acceptance packages.
"$AJ_ACCEPTANCE_PYTHON" - "$AJ_ACCEPTANCE_ROOT/.h3-venv" <<'PY' 9>&-
import sys
from pathlib import Path
if Path(sys.prefix).absolute() != Path(sys.argv[1]).absolute() or sys.version_info < (3, 11):
    raise SystemExit('Acceptance requires its own Python >=3.11 virtualenv')
PY

for AJ_ACCEPTANCE_TOOL in git cmake nvcc; do
  command -v "$AJ_ACCEPTANCE_TOOL" >/dev/null || { echo 'Acceptance requires preinstalled Git, CMake and CUDA devel tools' >&2; exit 1; }
done
"$AJ_ACCEPTANCE_PYTHON" "$AJ_ACCEPTANCE_PACKAGE/scripts/model_acceptance.py" --root "$AJ_ACCEPTANCE_ROOT" --prepare-native-node 9>&-

# Reuse the production pin/build checks in isolated directories. This installs
# native CLI/server binaries only: no ComfyUI server, panel, onstart or Vast CLI.
env WORKSPACE="$AJ_ACCEPTANCE_ROOT" DATA_ROOT="$AJ_ACCEPTANCE_ROOT" \
  COMFY_ROOT="$AJ_ACCEPTANCE_ROOT/native" PANEL_ROOT="$AJ_ACCEPTANCE_PACKAGE" \
  COMFY_PYTHON="$AJ_ACCEPTANCE_PYTHON" PYTHON_BIN="$AJ_ACCEPTANCE_PYTHON" \
  H3_BUILD_ROOT="$AJ_ACCEPTANCE_BUILD_ROOT" \
  "$AJ_ACCEPTANCE_PYTHON" "$AJ_ACCEPTANCE_PACKAGE/scripts/install_timing.py" run llama_build -- bash "$AJ_ACCEPTANCE_PACKAGE/scripts/setup_linux_llama.sh" 9>&-
"$AJ_ACCEPTANCE_PYTHON" -m pip check 9>&-

AJ_ACCEPTANCE_DOWNLOAD_PYTHON="$AJ_ACCEPTANCE_ROOT/.aj-download-venv/bin/python"
if [[ ! -x "$AJ_ACCEPTANCE_DOWNLOAD_PYTHON" ]]; then
  "$AJ_ACCEPTANCE_PYTHON" -m venv "$AJ_ACCEPTANCE_ROOT/.aj-download-venv" 9>&-
fi
"$AJ_ACCEPTANCE_DOWNLOAD_PYTHON" - "$AJ_ACCEPTANCE_ROOT/.aj-download-venv" <<'PY' 9>&-
import sys
from pathlib import Path
if Path(sys.prefix).absolute() != Path(sys.argv[1]).absolute():
    raise SystemExit('Acceptance downloader requires its own virtualenv')
PY
PIP_CONSTRAINT= "$AJ_ACCEPTANCE_DOWNLOAD_PYTHON" -m pip install -r "$AJ_ACCEPTANCE_PACKAGE/download-requirements.txt" 9>&-
HF_TOKEN="$AJ_ACCEPTANCE_DOWNLOAD_TOKEN" "$AJ_ACCEPTANCE_DOWNLOAD_PYTHON" \
  "$AJ_ACCEPTANCE_PACKAGE/scripts/model_acceptance.py" --root "$AJ_ACCEPTANCE_ROOT" --download 9>&-
unset AJ_ACCEPTANCE_DOWNLOAD_TOKEN HF_TOKEN
"$AJ_ACCEPTANCE_PYTHON" "$AJ_ACCEPTANCE_PACKAGE/scripts/model_acceptance.py" --root "$AJ_ACCEPTANCE_ROOT" --verify 9>&-
echo 'Acceptance model files/native runtime verified. GPU load/generation remains unverified; no GPU validation attested. No service, inference, render or Vast lifecycle started.'
