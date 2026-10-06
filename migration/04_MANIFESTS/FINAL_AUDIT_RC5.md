# PRE-RENTAL FINAL RC5 — audit closure

RC5 is based on RC4 and changes only deployment/test hardening.

Closed before rental:
- recursive workflow/subgraph link integrity;
- correct authoritative H3 render Seed (rgthree) sources;
- one analysis_seed per batch and unique Step 3/4 prompt seeds;
- frontend/backend fixed-width input contract (6 images, max 1 audio for first deploy);
- no-audio neutralization with silence file + reference disabled;
- prompt/render split on ports 8189/8188;
- recovery checks history + queue before requeueing, reducing duplicate paid-render risk;
- atomic queue/control state writes;
- Basic Auth fail-closed;
- persistent-data destroy gate;
- ComfyUI pinned to 0.38.0 and comfy-cli to 1.21.0;
- ComfyUI Manager enabled because install-deps --uv-compile requires Manager >=4.1;
- actual ComfyUI interpreter persisted as COMFY_PYTHON instead of assuming .venv;
- Linux llama.cpp b8840 CUDA build for the pinned LLM node;
- smoke validation uses documented `comfy --json workflow validate --workflow ...`;
- smoke UI->API conversion remains non-submitting (`--print-prompt`).

Live-only gates remain:
1. exact custom-node installation on the chosen Vast image;
2. live UI->API conversion against that /object_info;
3. required models physically present;
4. Step 0-2 execution cache observed on candidate #2+;
5. simultaneous prompt/render VRAM + throughput on RTX PRO 6000 96GB;
6. Vast instance credential + Local Volume mount behavior on the chosen offer.

Do not start a paid H3 render until `preflight.sh` and `smoke_test.sh` both pass.
