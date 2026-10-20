#!/usr/bin/env bash
# Every CLI, package installation, worker and preflight uses this same Python.
h3_select_python() {
  local requested prefix bootstrap package
  requested="${COMFY_PYTHON:-${PYTHON_BIN:-}}"
  if [[ -z "$requested" ]]; then
    requested="${DATA_ROOT:-${WORKSPACE:-/workspace}}/.h3-venv/bin/python"
    if [[ ! -x "$requested" ]]; then
      bootstrap="$(command -v python3 || command -v python)"
      "$bootstrap" -c 'import sys; sys.version_info >= (3, 11) or sys.exit("Python >=3.11 required")' || return 1
      "$bootstrap" -m venv "$(dirname "$(dirname "$requested")")"
    fi
  fi
  [[ -x "$requested" ]] || { echo "Selected Python is unavailable: $requested" >&2; return 1; }
  prefix="$("$requested" -c 'import sys; sys.version_info >= (3, 11) or sys.exit("Python >=3.11 required"); print(sys.prefix)')" || return 1
  [[ -x "$prefix/bin/python" ]] || { echo "Select a virtualenv/Conda Python with bin/python" >&2; return 1; }
  PYTHON_BIN="$prefix/bin/python"
  COMFY_PYTHON="$PYTHON_BIN"
  # comfy-cli's resolver prefers VIRTUAL_ENV; set it to the selected interpreter.
  export PYTHON_BIN COMFY_PYTHON VIRTUAL_ENV="$prefix"
  unset CONDA_PREFIX
  export PATH="$prefix/bin:$PATH"
  # Applies to CLI subprocesses and node lazy pip installs as well as our pip.
  package="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PIP_CONSTRAINT="$("$COMFY_PYTHON" "$package/scripts/runtime_dependencies.py" constraint-uri "$package" "${H3_CUDA_VERSION:-13.0}")" || return 1
  export PIP_CONSTRAINT
}

h3_comfy() {
  "$COMFY_PYTHON" -m comfy_cli "$@"
}
