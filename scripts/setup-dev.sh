#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON_BIN="${AJ_PYTHON:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 12), "AJ development setup requires Python 3.12"'
mkdir -p .local
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Existing venv has the wrong Python version"'
.venv/bin/python -m pip --disable-pip-version-check --cache-dir "$REPO_ROOT/.local/pip-cache" install --require-hashes -r requirements/panel.lock
.venv/bin/python -m pip --disable-pip-version-check --cache-dir "$REPO_ROOT/.local/pip-cache" check
echo "CPU development dependencies installed. GPU/ComfyUI/model installation is separate."
