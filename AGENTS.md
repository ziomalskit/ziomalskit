# Working on AJ

Read `README.md`, then `docs/ROADMAP.md`, `docs/FINAL_CPU_ACCEPTANCE_2026-10-08.md` and `docs/GPU_ACCEPTANCE.md`. Historical material under `migration/`, `archive/` and older audit documents is source material, not current product instructions.

- Use the existing `/workspace/ziomalskit` checkout. Every cloud task is isolated; do not create Git worktrees unless the user explicitly asks.
- Preserve `migration/` and `archive/` byte for byte. Their checksums establish the received baseline.
- PR #4 at `bfcc2bb22ff9691fda924fe17bd060a7e131c2e2` is CPU/runtime accepted. `make setup`, `make test` and `make audit` are expected to pass. That baseline records 358 tests for `make test` and `make audit`, with 16 additional HTTP smoke checks in `make test`. Do not revive historical expectations that `make audit` should fail.
- GPU/live acceptance is still pending. CPU PASS does not prove model loading, real ComfyUI `/object_info`, VRAM behaviour, actual H3 video generation or Vast STOP/DESTROY behaviour.
- Product goal: keep AJ a simple Ref2Video service. Primary UX is references + parameters -> job -> final MP4 link. Avoid adding platform features that are not in `docs/ROADMAP.md`.
- Target is one GPU. Preserve the two ComfyUI services: render on 8188 and prompt on 8189. Only one heavy H3 render may run at a time; the prompt worker may overlap on the same GPU. The current product direction is Parallel only; do not add Parallel/Idle/Pre-buffer selectors unless scope changes.
- The production input contract is exactly six images (Picture 1 + five supporting references) and at most one audio file.
- Persistent Vast Local Volume is the durability boundary for models/data/state that must survive compute destruction. Keep-data destroy must fail closed unless the real retained volume is verified.
- Secrets are deployment/runtime configuration, not a product UI. Never commit credentials, `runtime.env`, API keys, model weights, uploads, outputs or generated queue state. Do not add a Secrets screen or custom encrypted-panel feature unless explicitly requested.
- The current model manifest is not final product truth until the production H3/encoder/VAE/LoRA stack is frozen. Do not guess model URLs, revisions or checksums.
- Start the local panel with `make panel`; verify it with `.venv/bin/python scripts/dev_panel.py check`. Do not publish loopback preview links. Stop only processes started by this workflow.
- A normal panel batch automatically schedules five renders. GPU rental, real inference, Vast stop/destroy and credentials require explicit user scope and suitable credentials. Do not use a normal batch as a nonbillable prompt-only test.
- Use `.local/` or disposable directories for state, fixtures, credentials, logs and generated workflows. Run source-mutating tests through the repository test harness rather than against preserved migration material.
- Do not disable checks, rename failures as passes, weaken idempotency/recovery guarantees, or change checksums merely to hide source modifications.
