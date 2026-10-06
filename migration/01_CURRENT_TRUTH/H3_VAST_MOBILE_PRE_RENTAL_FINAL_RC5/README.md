# H3 Vast Mobile — PRE-RENTAL FINAL RC5

This is the release candidate to use for the first Vast deployment.

Target:
- RTX PRO 6000 Blackwell 96 GB
- CUDA devel image, CUDA 13 preferred (>=12.8 required)
- expose only panel port 7860
- ComfyUI 0.38.0 (pinned to the known-good local workflow environment)
- comfy-cli 1.21.0

Conservative first-release input contract:
- exactly 6 images: Picture 1 + 5 supporting references
- 0 or 1 audio reference
- native MiniMax H3 INT8 is the production default
- 10Eros Hybrid remains optional A/B

If preserving data, set `H3_PERSISTENCE_MODE=volume` and
`H3_PERSISTENT_ROOT=<actual mounted Local Volume path>` BEFORE installation.

Run:
```bash
bash INSTALL_ON_VAST.sh
```

After all required models are present:
```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Do not start a real H3 render until both pass.

See `FULL_PRE_RENTAL_AUDIT.md` for the complete audit and remaining live-only risks.
