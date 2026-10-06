#!/usr/bin/env bash
set -euo pipefail
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
if [[ -f "$PANEL_ROOT/runtime.env" ]]; then set -a; source "$PANEL_ROOT/runtime.env"; set +a; fi
"${PYTHON_BIN:-python}" "$PANEL_ROOT/scripts/smoke_test.py"
