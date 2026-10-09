# AJ / H3 Ref2Video — product roadmap

Status: CPU/runtime accepted on `main`; live GPU/Vast acceptance pending.

## Product goal

AJ should remain a simple Ref2Video service:

**references + parameters -> job -> final MP4 link**

The runtime may be robust internally, but the normal user flow should stay small and obvious.

## Scope decisions

Current product direction:
- one GPU;
- two ComfyUI services on that GPU: Render `8188`, Prompt `8189`;
- only one heavy H3 render at a time;
- Prompt Worker may run concurrently with the active render;
- prompt scheduling mode is **Parallel only** for now;
- prompt prefetch target: approximately 2–3 candidates ahead, subject to the live overlap benchmark;
- exactly six images and at most one audio input;
- completed jobs expose direct final MP4 links;
- persistent Vast Local Volume keeps durable data across compute destruction;
- model stack is not frozen yet; do not treat current model manifest as final product truth.

Explicitly not in the current core scope:
- Secrets/API-key management UI;
- custom encrypted-panel feature;
- file explorer;
- model-manager UI;
- elaborate render gallery/player;
- multi-GPU scheduling;
- Parallel/Idle/Pre-buffer mode selector;
- Eliza / legacy experimental projects.

Existing Stage 5 lifecycle and cost controls should not be removed merely to simplify the UI. They may later be moved under an Advanced section.

## Phase 0 — scope and repository truth

Goal: make the repository itself describe the current accepted baseline and product direction.

Deliverables:
- current `AGENTS.md`;
- this roadmap;
- README links to current acceptance and roadmap;
- no runtime behaviour changes.

Acceptance:
- `make setup`;
- `make test`;
- `make audit`;
- all expected to PASS.

## Phase 1 — first live Vast/GPU acceptance

Do not add unrelated features before this phase passes.

Order:
1. rent the target GPU instance;
2. attach and verify the intended Local Volume;
3. install current `main`;
4. run infrastructure preflight;
5. run conversion smoke without a real render;
6. inspect real ComfyUI `/object_info` on both services;
7. run prompt-only acceptance;
8. run exactly one controlled native H3 render;
9. verify a real playable MP4 through the controller/proxy.

Record:
- GPU;
- driver;
- CUDA;
- torch;
- checkout SHA;
- resolution;
- duration;
- seed;
- wall time;
- peak VRAM;
- any restart/OOM behaviour.

Pass means the current core actually works on the intended environment, not merely in CPU tests.

## Phase 2 — persistence and Vast lifecycle

Goal: finish work, destroy compute, keep the expensive persistent payload, and return later without rebuilding the environment from scratch.

Persistent data should include, where appropriate:
- models and LoRAs;
- durable ComfyUI/custom-node data required by the chosen deployment layout;
- inputs and outputs;
- AJ queue/state;
- workflow/configuration data required for fast bootstrap.

Acceptance sequence:
1. render successfully;
2. `STOP AFTER QUEUE`;
3. restart the same instance and verify state/data;
4. arm `FINISH QUEUE & DESTROY COMPUTE`;
5. destroy compute only after Local Volume verification passes;
6. confirm the Local Volume still contains protected data;
7. create compatible compute again on the host that can attach that Local Volume;
8. attach volume and bootstrap;
9. verify previous outputs/state and the ability to generate again.

The keep-data action must fail closed if retained storage cannot be proven.

## Phase 3 — simple results/API UX

Goal: make the normal path feel like a simple video API.

Normal job lifecycle:
`queued -> prompting -> rendering -> completed`

Completed job display should prioritize:
- status;
- useful job/seed identifier;
- render duration when available;
- **OPEN MP4** direct link.

Do not build a separate media-library product.

## Phase 4 — Parallel prompt prefetch

Goal: use Prompt Worker time productively while H3 is rendering.

Rules:
- exactly one H3 render at a time;
- Prompt Worker may overlap with it on the same GPU;
- no user-selectable scheduling modes yet;
- prefetch approximately 2–3 future candidates, refined by live measurements;
- preserve queue priorities and idempotency guarantees.

## Phase 5 — one-time overlap benchmark

This is an engineering acceptance test, not a product feature.

Compare:
- baseline H3 render with Prompt Worker idle;
- H3 render while Prompt Worker prepares future prompts.

Measure:
- H3 wall time;
- peak VRAM;
- OOM/restarts;
- useful prompt throughput gained during the render.

If overlap has a material performance or stability penalty, revisit scheduling. Otherwise keep Parallel as the only mode.

## Phase 6 — freeze the production model stack

Do this only after model choices are actually decided.

Freeze exact:
- H3 checkpoint/variant;
- prompt-worker LLM/VLM stack;
- encoders;
- VAE;
- required LoRAs and strengths;
- optional comparison variants that are intentionally supported.

Then harden provisioning with:
- exact source;
- exact revision;
- exact filename;
- checksum;
- fail-closed behaviour for missing/corrupt artifacts.

Do not guess URLs or checksums before this phase.

## Phase 7 — full queue and recovery acceptance

Run the intended product flow:
- ten prompt candidates;
- #1–5 automatic render path;
- #6–10 Review Pool;
- approved review jobs retain their priority semantics;
- pending/rejected review work must not block safe shutdown.

Exercise:
- controller restart;
- Prompt Worker restart;
- Render Worker restart;
- watchdog timeout;
- persistence failure/recovery boundaries;
- uncertain submission handling;
- no duplicate paid render submissions.

This is the production-behaviour acceptance phase.

## Phase 8 — usability tools

Only after the core generation path and lifecycle are accepted.

### Live Logs
Simple live tail for:
- Render ComfyUI;
- Prompt ComfyUI.

No log-management platform is required.

### Diagnostics
Small health page, for example:
- AJ;
- Render ComfyUI;
- Prompt ComfyUI;
- GPU;
- persistent storage;
- required models;
- queue;
- Vast API/control availability.

Show concise failures and actionable detail.

## Phase 9 — optional terminal

Terminal is last because it meaningfully increases the security impact of panel compromise.

If added:
- treat it as an advanced/developer escape hatch;
- require a deliberately chosen secure access model before exposing it remotely;
- do not make terminal availability a prerequisite for ordinary Ref2Video use.

## UI direction

The main screen should stay focused on:

```text
REFERENCES
PROMPT
SETTINGS

[ GENERATE ]

QUEUE

completed   OPEN MP4
rendering
prompting
queued
```

Infrastructure features such as lifecycle controls, diagnostics, logs and terminal belong under secondary/Advanced UI.

## Existing features to preserve

Current Stage 5 already contains useful lifecycle/cost controls such as:
- STOP AFTER CURRENT;
- STOP AFTER QUEUE;
- FINISH QUEUE & DESTROY COMPUTE;
- idle stop;
- cost guard;
- immediate STOP/DESTROY safeguards;
- basic Vast telemetry.

These are not prerequisites for the simple primary UX, but existing safe functionality should be preserved unless there is a specific reason to change it.

## Development workflow

Prefer small reviewable changes.

Suggested sequence:
1. roadmap/repository-truth documentation;
2. live GPU/Vast acceptance fixes only;
3. persistence/lifecycle fixes;
4. result-link UX cleanup;
5. Parallel prefetch;
6. benchmark-driven fixes;
7. final model manifest/provisioning;
8. full queue/recovery acceptance;
9. logs + diagnostics;
10. optional terminal.

Each coding task should:
- state its Definition of Done;
- avoid unrelated refactors;
- run the relevant automated tests;
- preserve migration/archive baseline material;
- report live-only assumptions separately from CPU-test evidence.
