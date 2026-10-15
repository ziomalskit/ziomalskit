#!/usr/bin/env bash
# Explicitly isolated native model preparation. Default: read-only plan.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/scripts/provision_model_acceptance.sh" "$@"
