#!/usr/bin/env bash
set -euo pipefail
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PYTHON_BIN="${COMFY_PYTHON:-${PYTHON_BIN:-$(command -v python)}}"

ensure_git_node(){
  local class="$1" repo="$2" folder="$3"
  if grep -q "\"$class\"" /tmp/h3_object_info.json 2>/dev/null; then
    echo "$class already available"
    return 0
  fi
  if [[ ! -d "$COMFY_ROOT/custom_nodes/$folder/.git" ]]; then
    echo "Installing fallback node $class from $repo"
    git clone --depth 1 "$repo" "$COMFY_ROOT/custom_nodes/$folder"
  fi
  if [[ -f "$COMFY_ROOT/custom_nodes/$folder/requirements.txt" ]]; then
    "$PYTHON_BIN" -m pip install -r "$COMFY_ROOT/custom_nodes/$folder/requirements.txt"
  fi
}

# Object info may not exist before first start. Fallback installs are idempotent.
ensure_git_node "BunnyH3ConditioningBridge" \
  "https://github.com/aa335615543-ux/BUNNY_H3_Conditioning_Bridge.git" \
  "BUNNY_H3_Conditioning_Bridge"
ensure_git_node "MinimaxH3LatentUpscaler3D" \
  "https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git" \
  "Comfyui_Minimax_h3_latent_Upscaler"
ensure_git_node "MergeImageBatchAndAudioList" \
  "https://github.com/altoiddealer/comfyui_essential-er.git" \
  "comfyui_essential-er"

# LLM Text Processor is Linux-critical for the prompt service. Pin to the exact
# workflow-recorded commit instead of relying on whatever "latest" happens to be.
LLM_DIR="$COMFY_ROOT/custom_nodes/ComfyUI-LLM-text-processor"
LLM_REPO="https://github.com/KingManiya/ComfyUI-LLM-text-processor.git"
LLM_COMMIT="65983de33681816f856db7e16767da906084c688"
if [[ ! -d "$LLM_DIR/.git" ]]; then
  rm -rf "$LLM_DIR"
  git clone "$LLM_REPO" "$LLM_DIR"
fi
git -C "$LLM_DIR" fetch origin "$LLM_COMMIT" --depth 1
git -C "$LLM_DIR" checkout --detach "$LLM_COMMIT"
if [[ -f "$LLM_DIR/requirements.txt" ]]; then
  "$PYTHON_BIN" -m pip install -r "$LLM_DIR/requirements.txt"
fi
