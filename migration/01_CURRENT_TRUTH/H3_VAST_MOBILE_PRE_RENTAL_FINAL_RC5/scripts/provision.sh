#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="${PACKAGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BASE_WORKSPACE="${WORKSPACE:-/workspace}"
DATA_ROOT="${H3_PERSISTENT_ROOT:-$BASE_WORKSPACE}"
WORKSPACE="$DATA_ROOT"
COMFY_ROOT="${COMFY_ROOT:-$DATA_ROOT/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-$DATA_ROOT/H3_VAST_MOBILE}"

if [[ -f /venv/main/bin/activate ]]; then
  source /venv/main/bin/activate
fi
PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"

echo "=== H3 Vast Mobile provision (pre-rental RC5) ==="
echo "Package: $PACKAGE_DIR"
echo "Workspace: $WORKSPACE"
echo "ComfyUI: $COMFY_ROOT"
echo "Python: $PYTHON_BIN"

mkdir -p "$WORKSPACE"

# Build prerequisites for Linux llama.cpp and common custom-node wheels.
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends build-essential cmake ninja-build git curl ca-certificates pkg-config
fi

"$PYTHON_BIN" -m pip install -U pip
"$PYTHON_BIN" -m pip install -U "comfy-cli==1.21.0"

if ! command -v vastai >/dev/null 2>&1; then
  echo "Installing Vast.ai CLI..."
  curl -fsSL https://vast.ai/install.sh | bash
fi

if [[ ! -f "$COMFY_ROOT/main.py" ]]; then
  echo "Installing ComfyUI..."
  comfy --skip-prompt --workspace="$WORKSPACE" install --version 0.38.0 --nvidia --cuda-version 13.0
else
  echo "Existing ComfyUI detected."
  comfy set-default "$COMFY_ROOT" || true
fi

# comfy-cli uses the active virtualenv/conda env first, otherwise a workspace venv.
# Record the interpreter that actually owns ComfyUI dependencies so service_ctl never guesses.
if [[ -x "$COMFY_ROOT/.venv/bin/python" ]]; then
  COMFY_PYTHON="$COMFY_ROOT/.venv/bin/python"
elif [[ -x "$COMFY_ROOT/venv/bin/python" ]]; then
  COMFY_PYTHON="$COMFY_ROOT/venv/bin/python"
else
  COMFY_PYTHON="$PYTHON_BIN"
fi
"$COMFY_PYTHON" - <<'PYCHK'
import torch
print('ComfyUI Python OK:', __import__('sys').executable)
print('Torch:', torch.__version__)
PYCHK
export COMFY_PYTHON

# Copy package only if source differs from target. Use Python shutil, not rsync.
if [[ "$(readlink -f "$PACKAGE_DIR")" != "$(readlink -f "$PANEL_ROOT")" ]]; then
  echo "Copying panel package to $PANEL_ROOT"
  "$PYTHON_BIN" - "$PACKAGE_DIR" "$PANEL_ROOT" <<'PY'
import shutil,sys
from pathlib import Path
src,dst=map(Path,sys.argv[1:3])
state=dst/"state"
backup=None
if state.exists():
    backup=dst.parent/".h3_state_backup"
    if backup.exists(): shutil.rmtree(backup)
    shutil.copytree(state,backup)
if dst.exists(): shutil.rmtree(dst)
shutil.copytree(src,dst)
(dst/"state").mkdir(exist_ok=True)
if backup and backup.exists():
    shutil.copytree(backup,dst/"state",dirs_exist_ok=True)
    shutil.rmtree(backup)
PY
fi

# Generate a panel password once if the caller did not provide one.
H3_PANEL_USER="${H3_PANEL_USER:-h3}"
if [[ -z "${H3_PANEL_PASSWORD:-}" ]]; then
  H3_PANEL_PASSWORD="$("$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
fi
export H3_PANEL_USER H3_PANEL_PASSWORD

echo "Installing controller requirements..."
"$PYTHON_BIN" -m pip install -r "$PANEL_ROOT/requirements.txt"

mkdir -p \
  "$COMFY_ROOT/input" "$COMFY_ROOT/output" \
  "$COMFY_ROOT/temp-render" "$COMFY_ROOT/temp-prompt" \
  "$COMFY_ROOT/user-render" "$COMFY_ROOT/user-prompt" \
  "$COMFY_ROOT/models/LLM" "$COMFY_ROOT/models/diffusion_models" \
  "$COMFY_ROOT/models/text_encoders" "$COMFY_ROOT/models/vae" \
  "$COMFY_ROOT/models/loras" "$COMFY_ROOT/models/latent_upscale_models" \
  "$COMFY_ROOT/models/semantic_bridge"

echo "Installing workflow dependencies from metadata..."
comfy set-default "$COMFY_ROOT" || true
set +e
comfy --workspace="$WORKSPACE" node install-deps \
  --workflow="$PANEL_ROOT/workflows/VAST_H3_MASTER_NATIVE_INT8_96GB.json" \
  --uv-compile
DEPS_RC=$?
set -e
if [[ $DEPS_RC -ne 0 ]]; then
  echo "WARNING: workflow dependency installation reported an error; explicit fallbacks will run."
fi

export COMFY_ROOT PYTHON_BIN COMFY_PYTHON
bash "$PANEL_ROOT/scripts/install_fallback_nodes.sh"
bash "$PANEL_ROOT/scripts/setup_linux_llama.sh"

"$PYTHON_BIN" - "$COMFY_ROOT/input" <<'EOF'
from pathlib import Path
import base64,wave,struct,sys
p=Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=True)
png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
for i in range(1,7):(p/f"__h3_preflight_{i}.png").write_bytes(png)
with wave.open(str(p/"__h3_silence_1s.wav"),"wb") as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(struct.pack("<h",0)*16000)
EOF

cat > "$PANEL_ROOT/runtime.env" <<EOF
WORKSPACE=$WORKSPACE
COMFY_ROOT=$COMFY_ROOT
PANEL_ROOT=$PANEL_ROOT
PYTHON_BIN=$PYTHON_BIN
COMFY_PYTHON=$COMFY_PYTHON
RENDER_PORT=8188
PROMPT_PORT=8189
H3_PANEL_PORT=7860
VAST_CLI=$(command -v vastai || echo vastai)
SERVICE_CTL=$PANEL_ROOT/scripts/service_ctl.sh
H3_PANEL_USER=$H3_PANEL_USER
H3_PANEL_PASSWORD=$H3_PANEL_PASSWORD
H3_PERSISTENCE_MODE=${H3_PERSISTENCE_MODE:-}
H3_PERSISTENT_ROOT=${H3_PERSISTENT_ROOT:-}
COMFY_OUTPUT_DIR=$COMFY_ROOT/output
COMFY_MODELS_DIR=$COMFY_ROOT/models
EOF
chmod 600 "$PANEL_ROOT/runtime.env"

cat > /root/onstart.sh <<EOF
#!/usr/bin/env bash
set -a
[[ -f "$PANEL_ROOT/runtime.env" ]] && source "$PANEL_ROOT/runtime.env"
set +a
bash "$PANEL_ROOT/scripts/service_ctl.sh" start all || true
EOF
chmod +x /root/onstart.sh

bash "$PANEL_ROOT/scripts/service_ctl.sh" restart all

echo
echo "=== Provision complete ==="
echo "Run preflight after models are present:"
echo "  bash $PANEL_ROOT/scripts/preflight.sh"

echo
echo "=== H3 PANEL LOGIN ==="
echo "User: $H3_PANEL_USER"
echo "Password: $H3_PANEL_PASSWORD"
echo "Save this password on your phone. runtime.env is chmod 600."
