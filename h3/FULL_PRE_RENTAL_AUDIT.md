# Full pre-rental audit — FINAL RC2

## 1. H3 render workflow — PASS offline
- UI graph link integrity checked.
- Native `minimax_h3_ref2va_pruned_int8_convrot.safetensors` is the default.
- First pass remains 20 steps / `res_multistep` / `simple` / denoise 1.
- Second pass remains 4 steps / `er_sde` / `linear_quadratic` / denoise ~0.30.
- Connected rgthree seed nodes are patched, not only sampler UI mirrors.
- No explicit unload nodes remain in the 96 GB master.
- Active/default LoRA stack preserved.

## 2. Prompt pipeline Step 0–4 — PASS offline after RC2 fix
A critical integration bug was found and fixed:
- Step 3/4 `LLMTextProcessor.seed` sockets are connected.
- Therefore changing only their local widget value was not authoritative.
- RC2 patches the real upstream LLM dashboard seed controller (node 4022).

Final seed policy:
- Step 0 forensic/tiles/verifier: stable fixed inputs.
- Step 1 = `analysis_seed`.
- Step 2 = `analysis_seed + 1`.
- Step 3 = candidate `prompt_seed`.
- Step 4 = candidate `prompt_seed + 1`.
- Internal variation token = the same `analysis_seed` for all 10 candidates in one batch.

Result: Step 0–2 can actually be reused/cached per batch, while Step 3–4 genuinely vary per candidate.

## 3. Prompt-only workflow — PASS offline
- SaveVideo render output is absent.
- Prompt output PreviewAny nodes remain.
- Raw prompt capture still uses `/history`.
- Final prompt has top-level PreviewAny fallback if subgraph title metadata is lost.

## 4. Inputs — PASS with conservative first-release contract
To eliminate stale desktop filenames and ambiguous optional branches:
- exactly 6 images are required for RC2;
- 0 or 1 audio reference is supported;
- no-audio mode loads generated technical silence only to satisfy LoadAudio serialization,
  AND explicitly sets Audio 0 `is_reference=false`, so silence is not used as an H3 audio reference;
- LLM audio visibility is also disabled in no-audio mode.

Multiple audio references are deliberately disabled for first deploy because the source workflow has Audio 1/2 branches bypassed by default.

## 5. Batch scheduler/review — PASS offline
- 10 candidates/batch.
- #1–5 auto.
- #6–10 review.
- Auto prompt generation across waiting batches outranks review generation.
- Approved review renders outrank auto renders.
- Pending review never blocks shutdown-after-queue.
- Arming shutdown freezes creation of new batches and review approvals.

## 6. Watchdog/recovery — PASS offline
- Separate prompt/render workers.
- Soft timeout attempts `comfy jobs cancel`.
- `/interrupt` is retained only as a compatibility fallback.
- Hard timeout restarts only the affected Comfy service.
- Controller restart reconciles in-flight prompt/render jobs through `/history`.
- Cancel-before-dispatch race is handled.

## 7. Two ComfyUI services — PASS structurally
- Render: localhost:8188, `--highvram`.
- Prompt: localhost:8189, DynamicVRAM with 6 GB headroom.
- Shared models/input/output.
- Separate temp/user/SQLite state.
- Only panel port 7860 is public.

Live performance/VRAM contention is still hardware-dependent and must be benchmarked once.

## 8. Linux prompt runtime — HARDENED
The LLM Text Processor officially auto-downloads llama.cpp only on Windows x64 CUDA 13.
RC2:
- pins the LLM node to workflow-recorded commit `65983de...`;
- builds llama.cpp `b8840` with CUDA for Linux;
- installs it in the node's expected vendor path;
- patches only the platform selector to `linux-x64-cuda`;
- requires CUDA toolkit >=12.8.

## 9. ComfyUI/comfy-cli compatibility — HARDENED
- ComfyUI pinned to 0.38.0, matching the known-good local setup from Oct 5.
- comfy-cli pinned to 1.21.0.
- install explicitly selects NVIDIA/CUDA 13.
- smoke test uses current `comfy validate --workflow ... --json`.
- `comfy run --print-prompt` performs UI->API conversion without queuing a paid render.

## 10. Models/custom nodes — FAIL-CLOSED live preflight
Static manifest checks:
- all production native H3 models;
- active default LoRAs;
- Bunny bridge model;
- Linux llama-cli.

Then BOTH live Comfy services must pass:
- `/object_info`;
- `comfy validate`;
- UI->API conversion with `--print-prompt`.

This catches missing custom classes, invalid combo/model filenames and converter incompatibility before real generation.

## 11. Vast lifecycle/security — PASS offline
- Basic Auth is global and fail-closed.
- Vast API key never enters browser JS.
- Stop/destroy happen server-side.
- immediate STOP/DESTROY require typed confirmation.
- `STOP AFTER CURRENT` waits for active prompt/render work and dispatches no new work.
- `STOP AFTER QUEUE` skips unstarted review prompts but completes auto/approved jobs.
- destroy-with-keep-data is disabled unless state/models/input/output are verified on the declared separate persistent mount.

## 12. Persistence/restart — PASS structurally
If `H3_PERSISTENT_ROOT` is set before install:
- ComfyUI, models, input, output, controller and state are installed below it.
- `/root/onstart.sh` restarts services after STOP -> START of the SAME instance.

After DESTROY + attaching the volume to a NEW instance, provisioning must be run again because `/root/onstart.sh` belongs to the destroyed container, while persistent data remains on the volume.

## 13. Release hygiene — PASS
- no test queue;
- no generated job JSONs;
- no secrets;
- state contains only `.gitkeep`.

## Remaining live-only risks
These cannot be proven offline:
1. chosen Vast host/image + Blackwell driver/toolkit compatibility;
2. actual custom-node import success on Linux;
3. llama.cpp CUDA build/runtime on that host;
4. exact `/history` text-output shape from installed node versions;
5. actual Step 0–2 cache hits;
6. overlap performance and VRAM pressure between two processes;
7. actual Local Volume mount source/layout;
8. instance-scoped Vast credentials from the chosen launch mode.

The included preflight and smoke/conversion tests are specifically designed to catch items 1–4 and 7 before the first H3 render. Items 5–6 require one prompt-only batch / benchmark.
