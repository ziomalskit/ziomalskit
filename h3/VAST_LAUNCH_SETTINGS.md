# Vast launch settings

Use SSH or Jupyter launch mode. Use CUDA devel image; CUDA 13 preferred, >=12.8 required for RTX PRO 6000 Blackwell llama.cpp build. Expose only TCP 7860. Keep 8188/8189 internal.

For a Local Volume set H3_PERSISTENCE_MODE=volume and H3_PERSISTENT_ROOT to its actual mount before installation. Provisioning stores ComfyUI/models/input/output/panel/state under that root. `/root/onstart.sh` restores services after STOP -> START.
