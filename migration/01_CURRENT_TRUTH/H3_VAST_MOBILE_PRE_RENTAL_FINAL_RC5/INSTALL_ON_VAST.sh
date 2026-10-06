#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/scripts/provision.sh"

DATA_ROOT="${H3_PERSISTENT_ROOT:-${WORKSPACE:-/workspace}}"
PANEL_ROOT="${PANEL_ROOT:-$DATA_ROOT/H3_VAST_MOBILE}"

echo
echo "Provisioning finished."
echo "Models are NOT assumed to exist yet. Once they are present run:"
echo "  bash $PANEL_ROOT/scripts/preflight.sh"
echo "  bash $PANEL_ROOT/scripts/smoke_test.sh"
