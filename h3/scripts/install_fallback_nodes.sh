#!/usr/bin/env bash
set -euo pipefail
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python

ensure_git_node(){
  local class="$1" repo="$2" folder="$3" revision="$4" target
  target="$COMFY_ROOT/custom_nodes/$folder"
  if [[ -e "$target" ]]; then
    [[ -d "$target/.git" && ! -L "$target" ]] || { echo "Refusing unrelated node directory: $target" >&2; exit 1; }
    [[ "$(git -C "$target" remote get-url origin)" == "$repo" ]] || { echo "Unexpected node origin: $target" >&2; exit 1; }
    [[ -z "$(git -C "$target" status --porcelain --untracked-files=normal)" ]] || {
      echo "Local changes preserved; reconcile them before installing pinned node: $target" >&2; exit 1;
    }
  else
    echo "Installing pinned fallback node $class"
    git clone --no-checkout "$repo" "$target"
  fi
  git -C "$target" fetch origin "$revision" --depth 1
  [[ "$(git -C "$target" rev-parse 'FETCH_HEAD^{commit}')" == "$revision" ]] || { echo "Fetched node revision mismatch: $target" >&2; exit 1; }
  echo "Selecting $class revision $revision; existing branches are preserved"
  git -C "$target" checkout --detach "$revision"
  [[ "$(git -C "$target" rev-parse HEAD)" == "$revision" ]] || { echo "Installed node revision mismatch: $target" >&2; exit 1; }
  if [[ -f "$target/requirements.txt" ]]; then
    "$PYTHON_BIN" -m pip install -r "$target/requirements.txt"
  fi
}

# A cached /object_info cannot establish provenance. Always enforce the pins.
specs="$("$COMFY_PYTHON" - "$PANEL_ROOT/config/custom_nodes_manifest.json" <<'PY'
import json,re,sys
from pathlib import Path
for item in json.loads(Path(sys.argv[1]).read_text())['explicit_fallback_git']:
    if not re.fullmatch(r'[0-9a-f]{40}',item['revision']) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*',item['folder']):
        raise SystemExit('Invalid pinned fallback specification')
    fields=[item[key] for key in ('node_class','repo','folder','revision')]
    if any(not value or any(c in value for c in '\t\r\n') for value in fields):
        raise SystemExit('Invalid fallback specification fields')
    print('\t'.join(fields))
PY
)"
while IFS=$'\t' read -r class repo folder revision; do
  ensure_git_node "$class" "$repo" "$folder" "$revision"
done <<< "$specs"

# LLM Text Processor is Linux-critical for the prompt service. Pin to the exact
# workflow-recorded commit instead of relying on whatever "latest" happens to be.
LLM_DIR="$COMFY_ROOT/custom_nodes/ComfyUI-LLM-text-processor"
LLM_REPO="https://github.com/KingManiya/ComfyUI-LLM-text-processor.git"
LLM_COMMIT="$("$COMFY_PYTHON" "$PANEL_ROOT/scripts/runtime_config.py" constant LLM_NODE_COMMIT)"
if [[ ! -d "$LLM_DIR/.git" ]]; then
  [[ ! -e "$LLM_DIR" ]] || { echo "Refusing to replace unrelated LLM node directory" >&2; exit 1; }
  git clone "$LLM_REPO" "$LLM_DIR"
fi
git -C "$LLM_DIR" fetch origin "$LLM_COMMIT" --depth 1
git -C "$LLM_DIR" checkout --detach "$LLM_COMMIT"
if [[ -f "$LLM_DIR/requirements.txt" ]]; then
  "$PYTHON_BIN" -m pip install -r "$LLM_DIR/requirements.txt"
fi
