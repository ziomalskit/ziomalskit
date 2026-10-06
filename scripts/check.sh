#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[[ -x .venv/bin/python ]] || { echo 'Run make setup first.' >&2; exit 1; }
.venv/bin/python scripts/check_package.py
.venv/bin/python scripts/run_offline_tests.py
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python scripts/panel_http_smoke.py
echo 'CPU development checks passed. This does not certify GPU rendering or Vast lifecycle controls.'
