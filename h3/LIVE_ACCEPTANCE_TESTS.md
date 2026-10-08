# Live acceptance tests — run in this order

Do not queue a real H3 batch before 1–3 pass.

## 1. Infrastructure preflight
```bash
bash scripts/preflight.sh
```
Must confirm:
- ComfyUI 0.38.0;
- comfy-cli 1.21.0 (warning if not exact);
- CUDA toolkit >=12.8;
- all required models/default LoRAs/Bunny model;
- Linux llama-cli;
- both Comfy services;
- authenticated panel.

## 2. Conversion smoke test — NO REAL RENDER
```bash
bash scripts/smoke_test.sh
```
This patches a technical workflow and runs:
- `comfy validate` on prompt and render services;
- `comfy run --print-prompt` on both.

`--print-prompt` converts UI -> API and exits without queuing generation.

## 3. Prompt-only acceptance
Create ONE batch with six small images and no audio.
Watch prompt service/logs:
- candidate #1 executes Step 0–4;
- candidates #2–10 should show substantial cache reuse for Step 0–2;
- final prompts must differ because Step 3/4 receive different candidate seeds;
- #1–5 become auto render candidates;
- #6–10 become pending review.

For this test, cancel/stop before spending time on multiple renders if desired.

## 4. One native H3 render
Verify:
- native INT8 model;
- active LoRAs;
- first/second pass;
- output playback through controller proxy.

## 5. Parallel overlap benchmark
While one H3 render runs, generate the next prompt batch.
Compare render wall time against render-only baseline.
If the penalty is material, later switch the prompt worker to prebuffer/idle-only.

## 6. Watchdog
Use a disposable job and intentionally short timeout.
Confirm only the affected service is cancelled/restarted and queue continues.

## 7. Vast lifecycle
Only after persistent storage reports VERIFIED:
- test STOP AFTER CURRENT;
- start the same instance from Vast console and verify `/root/onstart.sh`;
- test STOP AFTER QUEUE;
- leave DESTROY COMPUTE / KEEP DATA until outputs/state are visibly present on the Local Volume.
