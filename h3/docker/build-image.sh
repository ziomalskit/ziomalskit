#!/usr/bin/env bash
set -euo pipefail
set +x
[[ "${1:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'AJ_REVISION must be a full source commit' >&2; exit 2; }
if [[ -f /run/secrets/proxy_ca ]]; then
  export SSL_CERT_FILE=/run/secrets/proxy_ca PIP_CERT=/run/secrets/proxy_ca
  export REQUESTS_CA_BUNDLE=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca
  export CURL_CA_BUNDLE=/run/secrets/proxy_ca
fi
# Python must exist BEFORE provision.sh's timing/venv bootstrap.
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3.12 python3.12-venv python3.12-dev python3-pip \
  build-essential cmake ninja-build git curl ca-certificates pkg-config \
  libcurl4-openssl-dev ffmpeg libgl1 libglib2.0-0 libgomp1 openssh-server tini util-linux
python3.12 -m venv /opt/aj/venv
export WORKSPACE=/opt/aj/seed H3_PERSISTENT_ROOT=/opt/aj/seed
export COMFY_ROOT=/opt/aj/seed/ComfyUI PANEL_ROOT=/opt/aj/seed/H3_VAST_MOBILE
export H3_BUILD_ROOT=/opt/aj/build PACKAGE_DIR=/opt/aj/ziomalskit/h3
export H3_SKIP_SYSTEM_PACKAGES=1 H3_INSTALL_VAST_CLI=0 H3_BUILD_JOBS=1
# --image-build exits before the downloader, runtime.env, onstart or services.
bash "$PACKAGE_DIR/scripts/provision.sh" --image-build
# Preserve source/converters and installed native binaries, discard compiler objects.
rm -rf /opt/aj/build/llama.cpp-*/build /var/lib/apt/lists/* /root/.cache
# Refuse accidental weight materialization by an upstream dependency installer.
if find /opt/aj/seed/ComfyUI/models -type f \( -name '*.gguf' -o -name '*.safetensors' \) -print -quit | read -r _; then
  echo 'Unexpected model weights in image build; refusing image' >&2; exit 1
fi
# SSH host keys must be per instance, not baked into the image.
rm -f /etc/ssh/ssh_host_*
/opt/aj/venv/bin/python - "$1" <<'PY'
import json, pathlib, subprocess, sys
p=pathlib.Path('/etc/aj-image.json')
p.write_text(json.dumps({'revision':sys.argv[1], 'gpu_smoke':'PENDING',
    'packages':json.loads(subprocess.check_output([sys.executable,'-m','pip','list','--format=json'])),
    'apt':subprocess.check_output(['dpkg-query','-W','-f=${Package}=${Version}\n'],text=True).splitlines()},indent=2)+'\n')
PY
