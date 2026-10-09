#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/scripts/provision.sh"

DATA_ROOT="${H3_PERSISTENT_ROOT:-${WORKSPACE:-/workspace}}"
PANEL_ROOT="${PANEL_ROOT:-$DATA_ROOT/H3_VAST_MOBILE}"

echo
echo "Provisioning finished."
echo "Pinned model files verified. GPU loading and rendering remain pending; submissions are disabled. Run:"
printf '  PANEL_ROOT=%q bash %q\n' "$PANEL_ROOT" "$PANEL_ROOT/scripts/preflight.sh"
printf '  PANEL_ROOT=%q bash %q\n' "$PANEL_ROOT" "$PANEL_ROOT/scripts/smoke_test.sh"
