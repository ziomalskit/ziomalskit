# AJ H3 + 10Eros — live performance audit (2026-11-07)

Scope: Existing Vast.ai instance **59453490**, NVIDIA RTX PRO 6000 Blackwell Server Edition, workflow `10eros_full`. No new inference or video render was submitted during this audit. Measurements were obtained from existing ComfyUI history, AJ queue and read-only host telemetry.

## Observed wall-clock timings

| Component | Observations (seconds) | Notes |
| --- | --- | --- |
| Prompt-only ComfyUI executions | 375.9, 394.5, 479.6, 633.8 | Successful examples; wall-clock includes native model startup and output |
| Some previous failed prompt-only executions | 300.9, 301.0, 301.5 | At previous stage-timeout boundary; not useful estimates of successful inference |
| H3 video render | 506.8, 511.8, 731.0 | Three successful executions |
| One end-to-end AJ job | ~1137 | ~399 s to prompt, ~738 s render and completion |
| Panel health HTTP | median 2.5 ms | Local request returns expected HTTP 401 without credentials |
| Render ComfyUI queue API | median 4.6 ms | HTTP 200 |
| Prompt ComfyUI queue API | median 2.4 ms | HTTP 200 |

## Hardware and graph evidence

- GPU utilisation observed at **88–100%** during busy periods; peak observed power around **527 W**, NVIDIA throttle reason `0x0`.
- Host CPU had **256 logical processors** and ~103 GiB available RAM. Measured IO pressure was negligible; neither RAM nor disk throughput looked like a dominant limiter in these snapshots.
- H3 render used **481 frames at 24 FPS**, **24 + 4 sampling steps**, and had SageAttention / block-sparse attention node paths in the graph.
- `Qwen3.5-27B-heretic.Q5_K_M.gguf` writer stages have output ceilings of **8,192** (Step 3) and **16,448** tokens (Step 4), with Step 4 reasoning budget **8,192**. These are ceilings, **not measurements of actual token usage**.
- Native LLM stages create a new `llama-server` process per invocation and terminate it after completion; startup/reloading is additional overhead, although not independently timed here.
- Renderer admission waits for an already active Prompt Worker to settle, while the Prompt Worker can overlap with **sampling** if its policy and measured VRAM headroom permit. Overlap was observed at one point with both ComfyUI queues executing simultaneously. Therefore, the full pipeline is **conditionally concurrent, not always parallel**.
- Existing `H3_PROMPT_PREFETCH=3` is a prefetch buffer, not three concurrently running prompt workers.
- GPU was compute-saturated. A large improvement without changing inference workload is **not demonstrated**.

## Changes completed in this diagnostic session

1. Confirmed real GPU work and no hardware clock throttling; identified inference and sampling as the main wall-clock costs.
2. Installed a **restricted SSH public-key rotator** for the GitHub Actions operator on the already running Vast instance. It preserves prior authorized keys and adds only a forced-command public key with SHA-pinned script validation. **This process is currently running, but post-reboot persistence and a fresh-key workflow run were not yet validated.**
3. No production model, prompt, sampler step count, FPS, resolution, quality or queued job was changed. No model weights were downloaded and no additional GPU generation was submitted.

## Performance actions ranked by risk

1. **Low risk / diagnostic:** Store per-node LLM startup, prompt-token and completion-token timings in future executions, without persisting prompt text; this will quantify model reload vs inference. Requires a coordinated native-node change, not safe mid-batch.
2. **Potential speedup / quality tradeoff:** Offer an explicitly selectable mode with a smaller Step 4 reasoning budget (such as 4,096 instead of 8,192), preserving the 8,192 final-answer budget. Actual savings depend on whether the original reasoning budget is reached; validate final prompt structure before using it for render.
3. **Potential speedup / quality tradeoff:** Offer an explicitly selectable faster sampler schedule, such as 20 instead of 28 total steps; the compute reduction is not equivalent to the measured end-to-end speedup, and output quality must be inspected.
4. **Engineering / risk of regressions:** Reuse llama.cpp model/server residency between appropriate sequential writer stages. Requires proof that thinking mode, model parameters and GPU priority leases remain correct, and a rollback plan.
5. **Scheduling:** Improve time to first MP4 separately from total clips/hour; measure before modifying prefetch and resource overlap policies.

Do not promise order-of-magnitude speedups on unchanged RTX PRO 6000 workload. Do not automatically modify current batch's quality settings.
