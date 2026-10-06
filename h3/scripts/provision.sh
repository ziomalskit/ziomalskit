#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="${PACKAGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DATA_ROOT="${H3_PERSISTENT_ROOT:-${WORKSPACE:-/workspace}}"
WORKSPACE="$DATA_ROOT"
COMFY_ROOT="${COMFY_ROOT:-$DATA_ROOT/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-$DATA_ROOT/H3_VAST_MOBILE}"
H3_CUDA_VERSION="${H3_CUDA_VERSION:-13.0}"
case "$H3_CUDA_VERSION" in 12.8|12.9|13.0) ;; *) echo "Supported CUDA selection: 12.8, 12.9, 13.0" >&2; exit 1;; esac
mkdir -p "$DATA_ROOT"
source "$PACKAGE_DIR/scripts/python_env.sh"
h3_select_python
export PACKAGE_DIR DATA_ROOT WORKSPACE COMFY_ROOT PANEL_ROOT H3_CUDA_VERSION

echo "Preparing H3 with Python $COMFY_PYTHON and ComfyUI $COMFY_ROOT"
if [[ "${H3_SKIP_SYSTEM_PACKAGES:-0}" != 1 ]] && command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends build-essential cmake ninja-build git curl ca-certificates pkg-config
fi
"$COMFY_PYTHON" -m pip install "comfy-cli==1.21.0"

if [[ "${H3_INSTALL_VAST_CLI:-1}" == 1 ]] && ! command -v vastai >/dev/null 2>&1; then
  # Official HTTPS installer; preserve TLS validation. Never supply credentials here.
  curl -fsSL https://vast.ai/install.sh | bash
fi

COMFY_COMMIT="$("$COMFY_PYTHON" "$PACKAGE_DIR/scripts/runtime_config.py" constant COMFY_COMMIT)"
if [[ -f "$COMFY_ROOT/main.py" ]]; then
  [[ "$(git -C "$COMFY_ROOT" rev-parse HEAD)" == "$COMFY_COMMIT" ]] || {
    echo "Existing ComfyUI differs from pinned v0.38.0; select a clean COMFY_ROOT" >&2; exit 1;
  }
  h3_comfy --skip-prompt --workspace="$COMFY_ROOT" install --restore --version 0.38.0 --nvidia --cuda-version "$H3_CUDA_VERSION"
else
  h3_comfy --skip-prompt --workspace="$COMFY_ROOT" install --version 0.38.0 --nvidia --cuda-version "$H3_CUDA_VERSION"
fi
[[ "$(git -C "$COMFY_ROOT" rev-parse HEAD)" == "$COMFY_COMMIT" ]] || {
  echo "Installed ComfyUI commit does not match verified v0.38.0" >&2; exit 1;
}
"$COMFY_PYTHON" -c 'import torch; print("ComfyUI dependencies installed; CUDA readiness remains a separate check")'

if [[ "$(readlink -f "$PACKAGE_DIR")" != "$(readlink -f "$PANEL_ROOT")" ]]; then
  "$COMFY_PYTHON" - "$PACKAGE_DIR" "$PANEL_ROOT" <<'PY'
import shutil, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:3])
if src.resolve() in dst.resolve().parents or dst.resolve() in src.resolve().parents:
    raise SystemExit('Package and deployment paths must not contain each other')
if dst.exists() and any(dst.iterdir()) and not (dst/'app/main.py').is_file():
    raise SystemExit('Refusing to overwrite an unrelated deployment directory')
shutil.copytree(src, dst, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns('state', 'runtime.env', '__pycache__', '.venv', '.git'))
(dst/'state').mkdir(exist_ok=True)
PY
fi

"$COMFY_PYTHON" -m pip install -r "$PANEL_ROOT/requirements.txt"
mkdir -p "$COMFY_ROOT/input" "$COMFY_ROOT/output" \
  "$COMFY_ROOT/temp-render" "$COMFY_ROOT/temp-prompt" \
  "$COMFY_ROOT/user-render" "$COMFY_ROOT/user-prompt" \
  "$COMFY_ROOT/models/LLM" "$COMFY_ROOT/models/diffusion_models" \
  "$COMFY_ROOT/models/text_encoders" "$COMFY_ROOT/models/vae" \
  "$COMFY_ROOT/models/loras" "$COMFY_ROOT/models/latent_upscale_models" \
  "$COMFY_ROOT/models/semantic_bridge"
h3_comfy --skip-prompt --workspace="$COMFY_ROOT" set-default "$COMFY_ROOT"
# Do not downgrade a failed dependency installation to a successful provision.
h3_comfy --skip-prompt --workspace="$COMFY_ROOT" node install-deps \
  --workflow="$PANEL_ROOT/workflows/VAST_H3_MASTER_NATIVE_INT8_96GB.json" --uv-compile
bash "$PANEL_ROOT/scripts/install_fallback_nodes.sh"
bash "$PANEL_ROOT/scripts/setup_linux_llama.sh"
"$COMFY_PYTHON" -m pip check

"$COMFY_PYTHON" - "$COMFY_ROOT/input" <<'PY'
from pathlib import Path
import base64, wave, sys
p=Path(sys.argv[1])
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
for i in range(1,7): (p/f'__h3_preflight_{i}.png').write_bytes(png)
with wave.open(str(p/'__h3_silence_1s.wav'),'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000))
PY

export RENDER_PORT="${RENDER_PORT:-8188}" PROMPT_PORT="${PROMPT_PORT:-8189}" H3_PANEL_PORT="${H3_PANEL_PORT:-7860}"
export RENDER_COMFY_URL="http://127.0.0.1:$RENDER_PORT" PROMPT_COMFY_URL="http://127.0.0.1:$PROMPT_PORT"
export H3_PANEL_URL="http://127.0.0.1:$H3_PANEL_PORT"
export VAST_CLI="${VAST_CLI:-$(command -v vastai || echo vastai)}" SERVICE_CTL="$PANEL_ROOT/scripts/service_ctl.sh"
export COMFY_INPUT_DIR="$COMFY_ROOT/input" COMFY_OUTPUT_DIR="$COMFY_ROOT/output" COMFY_MODELS_DIR="$COMFY_ROOT/models"
COMFY_PYTHON=$COMFY_PYTHON "$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" write-env "$PANEL_ROOT/runtime.env"

H3_ONSTART_PATH="${H3_ONSTART_PATH:-/root/onstart.sh}"
# Quote path literals too: persistent mount paths may contain spaces.
"$COMFY_PYTHON" - "$PANEL_ROOT" "$H3_ONSTART_PATH" <<'PY'
from pathlib import Path
import shlex, shutil, sys, tempfile
panel, target=map(Path,sys.argv[1:3])
env=shlex.quote(str(panel/'runtime.env')); ctl=shlex.quote(str(panel/'scripts/service_ctl.sh'))
marker='# Managed by AJ H3 deployment'
if target.exists() and marker not in target.read_text():
    descriptor, backup=tempfile.mkstemp(prefix=target.name+'.aj-backup-',dir=target.parent)
    import os
    os.close(descriptor)
    shutil.copyfile(target,backup)
    Path(backup).chmod(0o600)
sys.path.insert(0,str(panel/'scripts'))
from runtime_config import atomic_private_text
atomic_private_text(target,f'#!/usr/bin/env bash\n{marker}\nset -euo pipefail\nset -a\nsource {env}\nset +a\nexec bash {ctl} start all\n')
target.chmod(0o700)
PY
bash "$PANEL_ROOT/scripts/service_ctl.sh" restart all
echo "Provisioning finished. Credentials are retained privately in $PANEL_ROOT/runtime.env (mode 600); no credentials are printed."
echo "Models and GPU acceptance are still required; run preflight and smoke before inference."
