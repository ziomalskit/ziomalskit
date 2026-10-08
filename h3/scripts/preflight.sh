#!/usr/bin/env bash
set -euo pipefail
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
if [[ -f "$PANEL_ROOT/runtime.env" ]]; then set -a; source "$PANEL_ROOT/runtime.env"; set +a; fi
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/preflight.py"
