# Stage 4 Audit V2

Issues found and fixed:

1. **Active Bunny adapter was missing from model manifest.**
   The master workflow has active node `BunnyH3ConditioningBridge` using
   `BUNNY_H3_ActionLogic_Bridge_V1.safetensors`.
   Preflight now requires it and accepts the custom-node model folder or
   `models/semantic_bridge`.

2. **Bunny custom node cannot be trusted to workflow `cnr_id` discovery.**
   Its workflow node lacks a normal `cnr_id`. Explicit Git fallback installation
   is now included. Latent Upscaler and Essential-ER also get explicit fallbacks.

3. **Supervisor dependency was too image-specific.**
   Replaced by `scripts/service_ctl.sh`, using PID files + nohup. Watchdog restart
   commands call this script directly.

4. **Both ComfyUI processes used `--highvram`.**
   Render keeps `--highvram`; prompt uses DynamicVRAM with 6 GB headroom.
   This preserves render priority and lowers cross-process OOM risk.

5. **Preflight required disabled LoRAs.**
   Now only the three enabled default LoRAs are mandatory. Disabled LoRAs and
   10Eros are warnings/optional until selected.

6. **Final prompt capture had only title matching.**
   Added fallback to the known top-level PreviewAny node ID 5732.

7. **Cache observability.**
   Controller now records `execution_cached` node IDs, so the live acceptance
   test can verify Step 0–2 reuse across candidates instead of assuming it.

Remaining live-only checks:
- UI→API conversion against installed node versions;
- exact `/history` payload produced by current PreviewAny/custom nodes;
- actual cache hits for Step 0–2;
- prompt/render overlap performance and VRAM on RTX PRO 6000.
