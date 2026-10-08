#!/usr/bin/env bash
set -euo pipefail
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PANEL_ROOT="${PANEL_ROOT:-/workspace/H3_VAST_MOBILE}"
source "$PANEL_ROOT/scripts/python_env.sh"
h3_select_python

# The lock belongs only to this shell. Git/pip helpers close it on exec.
mkdir -p "$COMFY_ROOT/custom_nodes"
exec 8>"$COMFY_ROOT/custom_nodes/.h3-install.lock"
flock -x 8
stage=""
trap 'if [[ -n "$stage" ]]; then rm -rf -- "$stage" 8>&- || { echo "Node staging cleanup failed; primary exit status retained" >&2 || true; }; fi' EXIT
ensure_git_node(){
  local class="$1" repo="$2" folder="$3" revision="$4" target legacy=0 working
  target="$COMFY_ROOT/custom_nodes/$folder"
  working="$target"
  if [[ -e "$target" ]]; then
    [[ -d "$target/.git" && ! -L "$target" ]] || { echo "Refusing unrelated node directory: $target" >&2; exit 1; }
    [[ "$(git -C "$target" config --get remote.origin.url 8>&-)" == "$repo" ]] || { echo "Unexpected node origin: $target" >&2; exit 1; }
    # Old --no-checkout clones have an empty index AND an empty working tree.
    # A normal checkout with deleted files still has index entries and is never
    # mistaken for installer state. Even ignored/untracked user files block it.
    if [[ -z "$(git -C "$target" ls-files --stage 8>&-)" ]] &&
       [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 ! -name .git -print -quit 8>&-)" ]]; then
      legacy=1
    elif [[ -n "$(git -C "$target" status --porcelain --untracked-files=all 8>&-)" ]]; then
      echo "Local changes preserved; reconcile them before installing pinned node: $target" >&2; exit 1
    fi
  fi
  if [[ ! -e "$target" || "$legacy" == 1 ]]; then
    stage="$(mktemp -d "$COMFY_ROOT/custom_nodes/.h3-node-XXXXXXXX" 8>&-)"
    working="$stage"
    git clone --no-checkout "$repo" "$working" 8>&-
  fi
  git -C "$working" fetch origin "$revision" --depth 1 8>&-
  [[ "$(git -C "$working" rev-parse 'FETCH_HEAD^{commit}' 8>&-)" == "$revision" ]] || { echo "Fetched node revision mismatch: $target" >&2; exit 1; }
  git -C "$working" checkout --detach "$revision" 8>&-
  [[ "$(git -C "$working" rev-parse HEAD 8>&-)" == "$revision" ]] || { echo "Installed node revision mismatch: $target" >&2; exit 1; }
  if [[ "$working" != "$target" ]]; then
    "$COMFY_PYTHON" - "$working" "$target" "$legacy" <<'PYCOMMIT' 8>&-
import os,sys
from pathlib import Path
sys.path.insert(0,os.environ['PANEL_ROOT']+'/scripts')
from deployment import exchange,sync_directory
stage,target=map(Path,sys.argv[1:3])
if sys.argv[3]=='1': exchange(stage,target)
else: os.rename(stage,target)
sync_directory(target.parent)
PYCOMMIT
    # Retain a recovered legacy .git directory for inspection; it may contain
    # old refs even though no working checkout or user files existed.
    if [[ "$legacy" != 1 ]]; then rm -rf -- "$stage" 8>&-; fi
    stage=""
  fi
  if [[ -f "$target/requirements.txt" ]]; then
    "$PYTHON_BIN" -m pip install -r "$target/requirements.txt" 8>&-
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
ensure_git_node "LLMTextProcessor" "$LLM_REPO" "ComfyUI-LLM-text-processor" "$LLM_COMMIT"
