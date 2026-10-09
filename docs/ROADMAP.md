# AJ / H3 Ref2Video — product roadmap

Status: PR #4 is merged at `bfcc2bb22ff9691fda924fe17bd060a7e131c2e2`.
Its offline feature set and full CPU regression/audit are **PASS**:
[9 October evidence and safety review](CPU_PRODUCT_ACCEPTANCE_2026-10-09.md).
Production selection is **H3 Full + 10Eros Full with exact v20 Heretic**.
Complete 9B writer source provenance remains blocked, so the migration is not
ready for GPU acceptance. See [model migration evidence](MODEL_MIGRATION_2026-10-09.md).
GPU/live Vast acceptance remains **pending**.

## Product goal

**references + parameters -> job -> final MP4 link**

Keep AJ a small Ref2Video product. Infrastructure tools are secondary, under a
collapsed Advanced section; they do not add steps to normal generation.

## Agreed order

1. **Finish the feature set offline/CPU.**
2. **Run the full CPU regression and safety audit on the final feature set.**
3. **Choose and freeze final production models separately.**
4. **Perform one consolidated Vast/GPU acceptance session at the end.**

Do not rent compute or perform live Vast/GPU actions as part of offline development.
CPU tests do not prove inference, model loading, VRAM, throughput or live lifecycle
behavior. No live/GPU acceptance PASS has been recorded.

## Scope decisions

- One GPU with two ComfyUI services: Render `8188`, Prompt `8189`.
- One Render Worker, one Prompt Worker; only one heavy H3 render at a time.
- Prompt Worker may overlap the active render on the same GPU.
- **Parallel only**, with bounded prompt prefetch, default `H3_PROMPT_PREFETCH=3`.
- Exactly six images and at most one audio reference.
- Completed jobs expose canonical video outputs and a direct **OPEN MP4** link.
- Retained Vast Local Volume is the durability boundary across compute destruction.
- Only `h3_full` / **H3 Full** and `10eros_full` / **10Eros Full** are normal
  production profiles. H3 Full is the runtime default. Each batch/job freezes its
  profile, model route, LoRA selection and bridge settings before publication.
- The canonical prompt is the exact v20 attachment; no v21/v22 prompt revisions.
- The production manifest pins 17 artifacts; complete 9B source metadata is
  still missing. Provisioning/preflight fail closed rather than substitute a quant.
- Terminal is an optional admin tool, disabled by default: `H3_ENABLE_TERMINAL=0`.

Out of scope:

- Secrets/API-key UI, custom HTTPS/encrypted-panel system;
- file explorer or model-manager UI;
- elaborate render gallery/player/library;
- multi-GPU, Parallel/Idle/Pre-buffer scheduling selectors;
- Eliza/legacy experiments;
- guessed final model URLs, revisions or checksums;
- real GPU inference or Vast rental/STOP/DESTROY during this CPU task.

Historical material under `migration/` and `archive/` stays byte-for-byte intact.

## Phase 1 — finish CPU-testable features

### Final MP4 contract

- Successful render jobs expose canonical `video_outputs`.
- Persist `render_started_at`, `finished_at` and `render_duration_seconds` when
  timestamps make calculation possible; retain them across recovery.
- Prioritize status, render duration and **OPEN MP4** in Queue / Results.
- Keep batch/candidate/job/seed as secondary metadata.
- Preserve authenticated HTTP Range/proxy playback.
- Completion still requires settled successful ComfyUI history with a final video,
  not preview text or an uncertain watcher result.
- Add backend/API and actual frontend JavaScript tests; no gallery/player/library.

### Bounded Parallel prefetch

- Clamp integer targets to 1–5; invalid/missing configuration falls back to 3.
- Count waiting automatic/approved renders and the active prompt reservation.
- Exclude the active render and unapproved Review Pool decisions from the buffer.
  Existing approved work may exceed the target; preparation then pauses.
- Preserve all automatic #1–5 priorities before unstarted review #6–10, across
  batches. Finite queues must still produce all five review decisions.
- Approved reviews keep top render priority.
- STOP AFTER CURRENT starts no new preparation. Queue draining skips unsubmitted
  review preparation; pending/rejected reviews never block safe shutdown.
- Expose the active target in `/api/config` and Diagnostics.
- Test targets 1, 2, 3, auto/review ordering, draining, render/prompt overlap and
  recovery. Keep per-service fences; do not introduce a global GPU mutex.

### Compact Diagnostics

Read-only `/api/diagnostics`, with PASS / WARN / FAIL:

- AJ/controller task and persistence state;
- Render and Prompt ComfyUI health with independent short deadlines;
- GPU and VRAM telemetry when available;
- persistent-storage proof and disk free space;
- queue summary and ready/preparing/prefetch target;
- local Vast CLI/control availability, clearly separate from remote authorization;
- production profiles, memory/overlap policy, cache/service epoch and LoRA defaults;
- model/provenance health: missing, wrong size/hash, verification required or
  verified file, with GPU validation still pending. Opening Diagnostics does not
  hash hundreds of GB, download weights or build artifacts.

Manual refresh is sufficient. Diagnostics must never submit, restart, mutate queue
state or invoke Vast. Preserve Render/Prompt restart controls in Advanced.

### Bounded Live Logs

- Render ComfyUI, Prompt ComfyUI, AJ panel/controller.
- Tail the existing `service_ctl.sh` files under `WORKSPACE`.
- Known service identifiers only; no client-selected paths.
- Bound line count and bytes read; UTF-8 with replacement.
- Missing log = "Not created yet"; unsafe/unreadable logs = unavailable.
- Source switching and manual refresh; stale responses must not replace current
  source/closed-panel state.
- Test missing, small, large/truncated, invalid service and unsafe-file cases.
- No indexing, searching, downloading or log-management subsystem.

### Optional terminal — implement last within this feature phase

- Disabled by default; disabled UI remains inactive and backend refuses sessions.
- Interactive PTY in `WORKSPACE`, live output/input, Enter, Ctrl+C, connect/disconnect.
- No persistent command history.
- Authenticated HTTP handshake creates a random, short-lived one-use token.
  WebSocket authentication is independent of HTTP middleware.
- Keep the token out of normal URLs/access logs; invalid/expired/reused tokens
  fail closed before process launch.
- One active session; always clean and reap shell/descendants on disconnect,
  error, cancellation and controller shutdown.
- Enable only on a deliberately trusted/private connection. Do not add a Secrets
  screen, API-key editor or custom HTTPS feature.
- Test disabled/auth/token behavior, real CPU PTYs and cleanup, plus frontend races.
  Terminal remains optional for normal Ref2Video acceptance.

## Phase 2 — full CPU regression and safety audit

Run:

```bash
make setup
make test
make audit
```

All expected checks must pass; zero failures/errors/skips or hung teardown.
Add focused tests for each feature, preserve existing checks and review:

- durable queue snapshots and idempotent batch/approval retries;
- no duplicate paid render submission, including lost `POST /prompt` acknowledgements;
- prompt/render admission races and bounded-buffer reconstruction;
- controller/worker restart and recovery, cancellation and watchdog;
- persistence-failure and lifecycle fencing;
- owned process/group cleanup;
- arbitrary-file/log disclosure;
- terminal process leaks and WebSocket authentication bypass;
- stale frontend responses, double clicks and close/reconnect races.

Keep all Stage 5 safeguards: STOP AFTER CURRENT, STOP AFTER QUEUE, FINISH QUEUE &
DESTROY COMPUTE / KEEP DATA, idle stop, cost guard, STOP NOW, DESTROY NOW, destructive
confirmations, service restarts, Vast/GPU/storage telemetry and fail-closed retained
volume verification. Simulate these boundaries locally; do not execute live actions.

CPU evidence belongs to the exact tested commit and must report live-only limits
separately. Open one feature PR against `main`; do not merge automatically.

## Phase 3 — full model migration (source-provenance blocker remains)

Implemented: shared exact v20 prompt composition, two full render templates,
profile-specific writers, dynamic 16-slot LoRAs, per-profile defaults, explicit
conditioning-before-transformer dependencies, phase-aware overlap, cache epochs,
durable profile routing, persistent checksum reuse and ephemeral HF/Xet downloads.

H3 uses the 4B Heretic Q8_0 writer. 10Eros requires deterministic BF16 GGUF
conversion from the canonical DavidAU 9B source using the unchanged pinned
llama.cpp. The converter contains Qwen3.5 support, but full source shard/config/
tokenizer metadata and exact source compatibility have not been verified.
Resolve that blocker before freezing the recipe or running provisioning; never
substitute the public IQ4_XS/Q4 build. External LoRAs require explicit local
checksum enrollment when trustworthy upstream provenance is unavailable.

H3 cold Step 0–2 waits while Full H3 samples. Cached/durable analysis permits
the small writer with measured headroom. 10Eros permits analysis overlap only
with measured headroom. Conditioning/decoding/recovery close the overlap window;
one heavy render owns the renderer. Limits remain GPU benchmark hypotheses.

## Phase 4 — one consolidated Vast/GPU acceptance session (pending)

Use [GPU_ACCEPTANCE.md](GPU_ACCEPTANCE.md) for the controlled gates, after the
feature set, CPU regression and separate final model decision are complete.

Order inside the single rented session:

1. Attach and verify the real retained Local Volume; install the final checkout.
2. Check driver/CUDA/torch/Python, real models/loaders and both services.
3. Run infrastructure preflight and conversion smoke without inference; inspect
   real `/object_info`.
4. Run a separate prompt-only test, with no Render POST.
5. Run controlled H3 Full and 10Eros Full renders, then H3 → 10Eros → H3.
   Verify black frames, encoder release, peak VRAM, playable MP4/Range, outputs
   and timing before starting a normal five-render automatic batch.
6. Benchmark render-only versus Parallel overlap. Measure wall time, peak VRAM,
   OOM/restarts and useful prompt throughput. Refine the bounded prefetch target
   using evidence; scheduling remains Parallel only.
7. Exercise the full ten-candidate flow, automatic/review priority, queue draining,
   worker/controller restart, cancellation, watchdog and uncertain submission.
8. Check diagnostics/logs on the real layout and optional terminal over the chosen
   private connection if deliberately enabled.
9. Verify STOP AFTER CURRENT, STOP AFTER QUEUE, guards and live lifecycle behavior.
   Keep-data destroy requires the exact provider attachment proof and protected
   paths. Verify retention and compatible reattachment/bootstrap after compute
   destruction.

Record GPU, driver, CUDA, torch, checkout SHA, model choices, resolution, video
duration, seed, wall time, peak VRAM, output evidence and lifecycle/retention evidence.
A normal batch automatically schedules five renders; never use it as the first
nonbillable prompt-only test.

Final live acceptance requires no duplicate paid submissions, lost durable jobs,
orphaned processes, OOM/restart loops or loss of data declared retained.
Until documented live gates pass, status remains **GPU/live acceptance pending**.

## UI direction

Primary UI:

```text
REFERENCES
PROMPT
SETTINGS
GENERATE
QUEUE / RESULTS
completed   OPEN MP4
```

Advanced is collapsed and holds Diagnostics, Live Logs, optional Terminal and
service controls. Existing Vast lifecycle controls may stay in their current
location. Avoid a whole-application redesign.
