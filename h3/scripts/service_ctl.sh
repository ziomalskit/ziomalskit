#!/usr/bin/env bash
set -euo pipefail

PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
RUNTIME_ENV="${RUNTIME_ENV:-$PANEL_ROOT/runtime.env}"
if [[ -f "$RUNTIME_ENV" ]]; then
  set -a
  source "$RUNTIME_ENV"
  set +a
fi

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
PID_DIR="${PID_DIR:-$PANEL_ROOT/state/pids}"
mkdir -p "$PID_DIR" "$WORKSPACE"

[[ -x "$COMFY_PYTHON" ]] || { echo "ComfyUI interpreter missing: $COMFY_PYTHON" >&2; exit 1; }

# All invocations share one lock, including 'all'. Do not unlink this file:
# waiters must continue locking the same inode. Workers close this fd below.
exec 9>"$PID_DIR/service_ctl.lock"
flock -x 9
"$COMFY_PYTHON" "$PANEL_ROOT/scripts/process_identity.py" support || {
  echo "Safe service control requires Linux pidfd support" >&2; exit 1;
}

pidfile(){ echo "$PID_DIR/$1.pid"; }
logfile(){ echo "$WORKSPACE/$1.log"; }

process_identity(){
  local service="$1" port
  shift
  case "$service" in render) port="$RENDER_PORT";; prompt) port="$PROMPT_PORT";; panel) port="$PANEL_PORT";; *) return 1;; esac
  "$COMFY_PYTHON" "$PANEL_ROOT/scripts/process_identity.py" "$@" \
    --pid-file "$(pidfile "$service")" --service "$service" \
    --python "$COMFY_PYTHON" --comfy-root "$COMFY_ROOT" --panel-root "$PANEL_ROOT" --port "$port"
}

alive(){
  owned_pid="$(process_identity "$1" check)"
}

wait_http(){
  local url="$1"
  for _ in $(seq 1 180); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

start_one(){
  local svc="$1"
  if alive "$svc"; then
    echo "$svc already running (pid $owned_pid)"
    return 0
  fi
  rm -f "$(pidfile "$svc")"

  case "$svc" in
    render)
      cd "$COMFY_ROOT"
      nohup "$COMFY_PYTHON" main.py \
        --listen 127.0.0.1 --port "$RENDER_PORT" \
        --models-directory "$COMFY_ROOT/models" \
        --input-directory "$COMFY_ROOT/input" \
        --output-directory "$COMFY_ROOT/output" \
        --temp-directory "$COMFY_ROOT/temp-render" \
        --user-directory "$COMFY_ROOT/user-render" \
        --database-url "sqlite:///$COMFY_ROOT/user-render/comfyui.db" \
        --highvram 9>&- >"$(logfile comfyui-render)" 2>&1 &
      process_identity "$svc" record --pid "$!"
      ;;
    prompt)
      cd "$COMFY_ROOT"
      # DynamicVRAM is intentional: render throughput has priority.
      nohup "$COMFY_PYTHON" main.py \
        --listen 127.0.0.1 --port "$PROMPT_PORT" \
        --models-directory "$COMFY_ROOT/models" \
        --input-directory "$COMFY_ROOT/input" \
        --output-directory "$COMFY_ROOT/output" \
        --temp-directory "$COMFY_ROOT/temp-prompt" \
        --user-directory "$COMFY_ROOT/user-prompt" \
        --database-url "sqlite:///$COMFY_ROOT/user-prompt/comfyui.db" \
        --vram-headroom 6 9>&- >"$(logfile comfyui-prompt)" 2>&1 &
      process_identity "$svc" record --pid "$!"
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
      nohup "$PYTHON_BIN" -m uvicorn app.main:app \
        --host 0.0.0.0 --port "$PANEL_PORT" 9>&- >"$(logfile h3-mobile)" 2>&1 &
      process_identity "$svc" record --pid "$!"
      ;;
    *) echo "unknown service: $svc" >&2; exit 2;;
  esac

  sleep 0.5
  if ! alive "$svc"; then
    case "$svc" in render|prompt) failure_log="$(logfile "comfyui-$svc")" ;; panel) failure_log="$(logfile h3-mobile)";; esac
    echo "$svc failed to start; see $failure_log" >&2
    exit 1
  fi
  case "$svc" in
    render) wait_http "http://127.0.0.1:$RENDER_PORT/system_stats" || { echo "render HTTP readiness timeout" >&2; exit 1; } ;;
    prompt) wait_http "http://127.0.0.1:$PROMPT_PORT/system_stats" || { echo "prompt HTTP readiness timeout" >&2; exit 1; } ;;
    panel)
      # An auth challenge proves the panel listens, without placing its password in argv.
      for _ in $(seq 1 30); do
        code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PANEL_PORT/" 2>/dev/null || true)"
        [[ "$code" == 401 ]] && break
        sleep 1
      done
      [[ "$code" == 401 ]] || { echo "panel authenticated HTTP readiness timeout" >&2; exit 1; }
      ;;
  esac
  echo "$svc started (pid $owned_pid)"
}

stop_one(){
  local svc="$1" f
  f="$(pidfile "$svc")"
  if ! alive "$svc"; then
    rm -f "$f"
    echo "$svc already stopped"
    return 0
  fi
  # The helper rechecks the record AFTER opening a pidfd and signals that fd.
  # PID reuse between this check and signalling can never hit the replacement.
  if ! process_identity "$svc" signal --signal TERM && alive "$svc"; then
    echo "Unable to signal owned $svc process; PID record preserved" >&2
    return 1
  fi
  for _ in $(seq 1 40); do
    if ! alive "$svc"; then
      rm -f "$f"
      echo "$svc stopped"
      return 0
    fi
    sleep 0.5
  done
  if ! process_identity "$svc" signal --signal KILL && alive "$svc"; then
    echo "Unable to kill owned $svc process; PID record preserved" >&2
    return 1
  fi
  for _ in $(seq 1 10); do
    if ! alive "$svc"; then
      rm -f "$f"
      echo "$svc killed after timeout"
      return 0
    fi
    sleep 0.1
  done
  echo "$svc did not exit; PID record preserved" >&2
  return 1
}

status_one(){
  local svc="$1"
  if alive "$svc"; then echo "$svc RUNNING pid=$owned_pid"; else echo "$svc STOPPED"; fi
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
