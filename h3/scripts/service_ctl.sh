#!/usr/bin/env bash
set -euo pipefail
set +x
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
RUNTIME_ENV="${RUNTIME_ENV:-$PANEL_ROOT/runtime.env}"
if [[ -f "$RUNTIME_ENV" ]]; then
  set -a
  source "$RUNTIME_ENV"
  set +a
fi
# One-time download credentials never enter long-lived panel/ComfyUI workers.
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN HF_HUB_TOKEN

COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
WORKSPACE="${WORKSPACE:-/workspace}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"
COMFY_PYTHON="${COMFY_PYTHON:-$PYTHON_BIN}"
[[ "$PYTHON_BIN" == "$COMFY_PYTHON" ]] || { echo "Panel and ComfyUI must use the same selected interpreter" >&2; exit 1; }
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python
RENDER_PORT="${RENDER_PORT:-8188}"
PROMPT_PORT="${PROMPT_PORT:-8189}"
PANEL_PORT="${H3_PANEL_PORT:-7860}"
PID_DIR="${PID_DIR:-$WORKSPACE/.h3-service-pids}"
mkdir -p "$PID_DIR" "$WORKSPACE"

[[ -x "$COMFY_PYTHON" ]] || { echo "ComfyUI interpreter missing: $COMFY_PYTHON" >&2; exit 1; }

"$COMFY_PYTHON" "$PANEL_ROOT/scripts/process_identity.py" migrate \
  --pid-dir "$PID_DIR" --legacy-dir "${H3_LEGACY_PID_DIR:-$SCRIPT_ROOT/state/pids}" || {
  echo "Unable to import existing service ownership; no service action dispatched" >&2; exit 1;
}

# All invocations share one lock, including 'all'. Do not unlink this file:
# waiters must continue locking the same inode. Workers close this fd below.
exec 9>"$PID_DIR/service_ctl.lock"
flock -x 9
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/process_identity.py" support 9>&- || {
  echo "Safe service control requires Linux pidfd support" >&2; exit 1;
}

pidfile(){ echo "$PID_DIR/$1.pid"; }
logfile(){ echo "$WORKSPACE/$1.log"; }

process_identity(){
  local service="$1" port
  shift
  case "$service" in render) port="$RENDER_PORT";; prompt) port="$PROMPT_PORT";; panel) port="$PANEL_PORT";; *) return 1;; esac
  "$COMFY_PYTHON" "$PANEL_ROOT/scripts/process_identity.py" \
    --pid-file "$(pidfile "$service")" --service "$service" \
    --python "$COMFY_PYTHON" --comfy-root "$COMFY_ROOT" --panel-root "$PANEL_ROOT" --port "$port" \
    --controller-pid "$$" "$@" 9>&-
}

alive(){
  if owned_pid="$(exec 9>&-; process_identity "$1" check)"; then
    identity_state=0
    return 0
  else
    identity_state=$?
    return "$identity_state"
  fi
}

wait_http(){
  local svc="$1" url="$2" expected="$3" code deadline
  deadline=$((SECONDS + ${H3_READINESS_TIMEOUT:-180}))
  while (( SECONDS < deadline )); do
    if process_identity "$svc" listener; then
      code="$(exec 9>&-; curl --connect-timeout 1 --max-time 2 -sS -o /dev/null -w '%{http_code}' "$url" 9>&- 2>/dev/null || true)"
      if [[ "$code" == "$expected" ]] && process_identity "$svc" listener; then return 0; fi
    fi
    if ! alive "$svc"; then return 1; fi
    sleep 0.1 9>&-
  done
  return 1
}

start_one(){
  local svc="$1"
  if alive "$svc"; then
    process_identity "$svc" desired || { echo "$svc configuration changed; restart required" >&2; return 1; }
    echo "$svc already running (pid $owned_pid)"
    return 0
  elif [[ "$identity_state" != 1 ]]; then
    echo "Unable to establish $svc ownership; PID record preserved" >&2
    return 1
  fi
  if ! process_identity "$svc" port-free; then
    echo "Unable to establish an unused $svc port; PID record preserved" >&2
    return 1
  fi
  rm -f "$(pidfile "$svc")" 9>&-

  case "$svc" in
    render)
      cd "$COMFY_ROOT"
      process_identity "$svc" launch --log-file "$(logfile comfyui-render)" -- "$COMFY_PYTHON" main.py \
        --listen 127.0.0.1 --port "$RENDER_PORT" \
        --models-directory "$COMFY_ROOT/models" \
        --input-directory "$COMFY_ROOT/input" \
        --output-directory "$COMFY_ROOT/output" \
        --temp-directory "$COMFY_ROOT/temp-render" \
        --user-directory "$COMFY_ROOT/user-render" \
        --database-url "sqlite:///$COMFY_ROOT/user-render/comfyui.db" \
        --highvram
      ;;
    prompt)
      cd "$COMFY_ROOT"
      # DynamicVRAM is intentional: render throughput has priority.
      process_identity "$svc" launch --log-file "$(logfile comfyui-prompt)" -- "$COMFY_PYTHON" main.py \
        --listen 127.0.0.1 --port "$PROMPT_PORT" \
        --models-directory "$COMFY_ROOT/models" \
        --input-directory "$COMFY_ROOT/input" \
        --output-directory "$COMFY_ROOT/output" \
        --temp-directory "$COMFY_ROOT/temp-prompt" \
        --user-directory "$COMFY_ROOT/user-prompt" \
        --database-url "sqlite:///$COMFY_ROOT/user-prompt/comfyui.db" \
        --vram-headroom 6
      ;;
    panel)
      cd "$PANEL_ROOT"
      export RENDER_COMFY_URL="http://127.0.0.1:$RENDER_PORT"
      export PROMPT_COMFY_URL="http://127.0.0.1:$PROMPT_PORT"
      export COMFY_INPUT_DIR="$COMFY_ROOT/input"
      export COMFY_OUTPUT_DIR="${COMFY_OUTPUT_DIR:-$COMFY_ROOT/output}"
      export COMFY_MODELS_DIR="${COMFY_MODELS_DIR:-$COMFY_ROOT/models}"
      export COMFY_ROOT="$COMFY_ROOT"
      export H3_PANEL_USER="${H3_PANEL_USER:-h3}"
      export H3_PANEL_PASSWORD="${H3_PANEL_PASSWORD:-}"
      printf -v RENDER_RESTART_CMD 'bash %q restart render' "$PANEL_ROOT/scripts/service_ctl.sh"
      printf -v PROMPT_RESTART_CMD 'bash %q restart prompt' "$PANEL_ROOT/scripts/service_ctl.sh"
      export RENDER_RESTART_CMD PROMPT_RESTART_CMD
      export SERVICE_CTL="$PANEL_ROOT/scripts/service_ctl.sh"
      export VAST_CLI="${VAST_CLI:-vastai}"
      export H3_PERSISTENT_ROOT="${H3_PERSISTENT_ROOT:-}"
      export H3_PERSISTENCE_MODE="${H3_PERSISTENCE_MODE:-}"
      # Panel must be externally reachable through Vast port mapping; Basic Auth protects it.
      process_identity "$svc" launch --log-file "$(logfile h3-mobile)" -- "$PYTHON_BIN" -m uvicorn app.main:app \
        --host 0.0.0.0 --port "$PANEL_PORT"
      ;;
    *) echo "unknown service: $svc" >&2; exit 2;;
  esac

  sleep 0.5 9>&-
  if alive "$svc"; then
    :
  elif [[ "$identity_state" == 1 ]]; then
    case "$svc" in render|prompt) failure_log="$(logfile "comfyui-$svc")" ;; panel) failure_log="$(logfile h3-mobile)";; esac
    echo "$svc failed to start; see $failure_log" >&2
    exit 1
  else
    echo "Unable to establish launched $svc ownership; PID record preserved" >&2
    return 1
  fi
  case "$svc" in
    render) url="http://127.0.0.1:$RENDER_PORT/system_stats"; expected=200 ;;
    prompt) url="http://127.0.0.1:$PROMPT_PORT/system_stats"; expected=200 ;;
    panel) url="http://127.0.0.1:$PANEL_PORT/"; expected=401 ;;
  esac
  wait_http "$svc" "$url" "$expected" || { echo "$svc owned HTTP readiness timeout" >&2; return 1; }

  if ! alive "$svc"; then
    echo "Unable to confirm $svc ownership after HTTP readiness; PID record preserved" >&2
    return 1
  fi
  echo "$svc started (pid $owned_pid)"
}

stop_one(){
  local svc="$1" f
  f="$(pidfile "$svc")"
  if alive "$svc"; then
    :
  elif [[ "$identity_state" == 1 ]]; then
    rm -f "$f" 9>&-
    echo "$svc already stopped"
    return 0
  else
    echo "Unable to establish $svc ownership; PID record preserved" >&2
    return 1
  fi
  if process_identity "$svc" stop; then
    echo "$svc stopped"
    return 0
  fi
  echo "Unable to stop owned $svc group; PID record preserved" >&2
  return 1
}

status_one(){
  local svc="$1"
  if alive "$svc"; then
    echo "$svc RUNNING pid=$owned_pid"
  elif [[ "$identity_state" == 1 ]]; then
    echo "$svc STOPPED"
  else
    echo "$svc UNKNOWN; PID record preserved" >&2
    return 1
  fi
}

cmd="${1:-status}"
svc="${2:-all}"
services=(render prompt panel)
[[ "$svc" == "all" ]] || services=("$svc")

case "$cmd" in
  start) for s in "${services[@]}"; do start_one "$s"; done ;;
  stop) for s in "${services[@]}"; do stop_one "$s"; done ;;
  restart) for s in "${services[@]}"; do stop_one "$s"; start_one "$s"; done ;;
  status) for s in "${services[@]}"; do status_one "$s"; done ;;
  *) echo "usage: $0 {start|stop|restart|status} [render|prompt|panel|all]" >&2; exit 2;;
esac
