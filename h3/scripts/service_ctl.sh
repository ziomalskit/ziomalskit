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

pidfile(){ echo "$PID_DIR/$1.pid"; }
logfile(){ echo "$WORKSPACE/$1.log"; }

alive(){
  local f p
  f="$(pidfile "$1")"
  [[ -f "$f" ]] || return 1
  p="$(cat "$f" 2>/dev/null || true)"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null || return 1
  # Linux containers may retain a dead child briefly as a zombie.
  [[ ! -r "/proc/$p/stat" || ! "$(cat "/proc/$p/stat")" =~ \)\ Z[[:space:]] ]]
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
    echo "$svc already running (pid $(cat "$(pidfile "$svc")"))"
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
        --highvram >"$(logfile comfyui-render)" 2>&1 &
      echo $! >"$(pidfile "$svc")"
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
        --vram-headroom 6 >"$(logfile comfyui-prompt)" 2>&1 &
      echo $! >"$(pidfile "$svc")"
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
        --host 0.0.0.0 --port "$PANEL_PORT" >"$(logfile h3-mobile)" 2>&1 &
      echo $! >"$(pidfile "$svc")"
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
  echo "$svc started (pid $(cat "$(pidfile "$svc")"))"
}

stop_one(){
  local svc="$1" f p
  f="$(pidfile "$svc")"
  if ! alive "$svc"; then
    rm -f "$f"
    echo "$svc already stopped"
    return 0
  fi
  p="$(cat "$f")"
  kill "$p" 2>/dev/null || true
  for _ in $(seq 1 40); do
    if ! alive "$svc"; then
      rm -f "$f"
      echo "$svc stopped"
      return 0
    fi
    sleep 0.5
  done
  kill -9 "$p" 2>/dev/null || true
  rm -f "$f"
  echo "$svc killed after timeout"
}

status_one(){
  local svc="$1"
  if alive "$svc"; then echo "$svc RUNNING pid=$(cat "$(pidfile "$svc")")"; else echo "$svc STOPPED"; fi
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
