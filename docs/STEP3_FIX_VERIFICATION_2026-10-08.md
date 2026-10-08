# STEP 3 fixes: verification record

Base and required commit parent: `934b0526901e6287b62dea8b502e8a33154d49e5`.
Branch: `fix/aj-runtime-blockers`.

The twelve confirmed findings are fixed in the active `h3/` application. All
verification uses disposable state, fake models/credentials, local CPU workers
and local/mock HTTP. No real ComfyUI inference, GPU render or Vast action ran.
`migration/` and `archive/`, the original structural catalog, and verified
upstream converter fixtures remain unchanged.

## Contracts changed

- LoRA strengths must be finite and within **[-2.0, 2.0], inclusive**, whether
  enabled or disabled. This is the user-approved project range; ComfyUI v0.38's
  underlying loader declares the wider [-100, 100] range. Identifiers are bare
  supported filenames; path prefixes are rejected deterministically.
- HTTP lifecycle mutations require `control_token` from `/api/vast/status` or
  the preceding acknowledged mutation. Missing tokens return 428; stale tokens
  return 409. Every accepted user decision durably increments the generation.
  A controller-process epoch invalidates old-process tokens. A conflict requires
  a new explicit decision; the frontend never automatically retries an ARM.
- Service restarts reserve a per-service lock and publish maintenance state.
  Concurrent duplicates receive 409. New admissions pause; preparation spanning
  a restart is deferred even if the restart has already finished. Failed or
  cancelled restarts retain an unavailable barrier until an explicit successful
  restart. Already dispatched/uncertain execution retains the existing recovery
  rules; it is never automatically submitted again.
- UI workflow identities, types, saved widget mirrors, selected dynamic branches
  and catalog order must be unambiguous. Converted unlinked widgets must retain
  their saved values. The two existing Spectrum named `enabled` mirrors now
  match the positional `true` values previously consumed by the converter.
- Capture title ambiguity fails the prompt. Final fallback requires no title
  candidate and the expected PreviewAny identity. Missing/ambiguous captures
  cannot feed automatic rendering.
- Proxy limits: connect/write/pool 5 seconds, read/header 60 seconds. A single
  owner closes acquisition tasks, streams and clients on every exit. Cleanup
  failures preserve the primary exception.

## Findings, regressions and original attack outcomes

Tests below refer to classes in `tests/test_step3_api.py` (API),
`tests/test_step3_workflows.py` (Workflow), `tests/test_step3_frontend.py`
(Frontend), and `tests/test_step3_proxy.py` (Proxy).

| # | Implemented invariant | Regression | Original scenario after fix |
| --- | --- | --- | --- |
| 1 | Pydantic finite/range validation before batch publication; strict atomic JSON and fail-closed reload; validation errors cannot echo nonfinite JSON | API `test_nonfinite_json_strings_and_numbers_rejected_before_persistence`, `test_nonfinite_persisted_state_is_preserved_and_blocks_reload`, `test_finite_strength_boundaries_persist_reload_and_patch_exactly` | NaN/Infinity/-Infinity/1e400: 422, zero jobs/writes, jobs polling 200; supported finite control persists, reloads and patches correctly |
| 2 | Durable CAS generation and process epoch; no await between comparison and publication | API `test_barrier_late_lifecycle_actions_cannot_cross_acknowledged_cancel`, `test_lifecycle_missing_token_and_previous_boot_token_reject` | CANCEL 200; late ARM 409; plan none; zero guard dispatch. Barrier covers all three deferred plans and both immediate actions before admission |
| 3 | Frontend per-service in-flight button guard and backend per-service restart lock | API `test_concurrent_restart_is_one_owned_restart_and_inprogress_409`; Frontend `test_restart` | Concurrent real owned fake restart commands: 200/409, one restart; double click emits one POST |
| 4 | Service maintenance admission barrier plus preparation epoch | API `test_restart_pauses_queue_and_resume_dispatches_exactly_once`, `test_restart_during_preparation_defers_even_after_success_or_failure` | Original worker/restart race retains render_queued_review, no submission while unavailable, eligible after success; regression completes exactly one dispatch |
| 5 | Definitive 400/404/409/422 clears only the exact rejected approval; transport/408/5xx keeps immutable retries | Frontend `test_approval`, `test_approval_uncertain`, `test_approval_replacement`; unchanged frontend retry suite | Empty/whitespace gets 400, next click reopens editor, corrected prompt succeeds; uncertain retry preserves body across reload |
| 6 | Dirty/focus/edit generations preserve drafts; own acknowledged partial saves advance the draft token; conflict requires explicit SAVE again | Frontend `test_guards`, `test_focused`, `test_edit_during_save`, `test_partial_save`, `test_guard_conflict` | Poll preserves 2/5; SAVE sends 2/5 with chained acknowledged tokens |
| 7 | Independent jobs, prompt-detail and Vast request generations; action acknowledgement invalidates old polls | Frontend `test_selection`, `test_jobs`, `test_vast`, `test_action_snapshot`, `test_jobs_failure` | Late A cannot replace B; completed jobs and newer Vast state remain displayed; a failed poll preserves the queue |
| 8 | Per-graph/subgraph ID uniqueness, globally unambiguous patch targets, expected types/fields, widget mirror/order validation and converted-value verification | Workflow `test_duplicate_identity_in_scope_and_patch_namespace_fails_before_conversion`, `test_conflicting_missing_and_reordered_widgets_fail_zero_submission`, `test_converter_catalog_change_or_corruption_cannot_silently_use_default`, `test_real_converter_preserves_source_sigma_seed_and_both_phases` | Duplicate 1854 fails at patch; conflicting/missing storage and reversed catalog order: zero POST. Valid controls pass the actual pinned CLI Graph validator with seed 123 and video/audio shifts 6/3 |
| 9 | Every logical title has at most one source; ambiguity fails before fallback; fallback identity/type verified | Workflow `test_duplicate_each_logical_capture_fails_without_fallback_or_render`, `test_capture_unique_titles_and_verified_fallback_preserve_exact_text`, `test_real_converted_capture_ambiguity_never_dispatches_automatic_render` | Duplicate STEP 4: prompt_failed, final null, one fake prompt POST, zero render POST; the actual CLI schema validator accepts the fixture, so failure is capture provenance rather than schema |
| 10 | Canonical filename validated once; installed model and exact supported slots checked before publication; both saved mirrors patched and checked | API `test_unknown_prefixed_duplicate_and_out_of_range_loras_reject_before_write`, `test_finite_strength_boundaries_persist_reload_and_patch_exactly`; Workflow `test_lora_slot_duplicates_and_wrong_type_reject_before_patch` | Known LoRA consumes requested 0.123 exactly; installed unsupported LoRA 400; ../ prefix 422; no silent default or ignored accepted setting |
| 11 | Bounded timeout, acquisition disconnect monitoring and cancellation-shielded response ownership | Proxy suite: 9 tests, including three real header disconnects, actual header/body timeouts, repeated cancellation and primary-error preservation | Three interrupted downstream header requests leave upstream sockets closed; ReadTimeout returns 504 and client closed. Additional real body-disconnect check: three requests leave zero upstream sockets/tasks |
| 12 | isfinite after price conversion, finite nested telemetry and finite session estimate | API `test_vast_nonfinite_prices_never_break_status_json` | NaN/Infinity/-Infinity/1e309: status 200 and hourly_usd null; valid/zero/missing/invalid price cases retain diagnostic payload |

Original STEP 3 probes were replayed with their attack mutations retained.
Where necessary, harnesses were adapted to the new required CAS token and to
assert absence of the original bug. The old converter fixture's inferred union
of dynamic branches is not a faithful worker catalog: replay and adapter tests
use a separate explicit dynamic-combo catalog; original fixture checksums stay
unchanged. The unchanged old frontend probe now fails its bug assertion because
it receives **PROMPT B**, rather than the incorrect PROMPT A it previously
expected; deterministic regressions verify all reversed-response scenarios.
The old nonfinite probe's finite control uses an unsupported filename and now
correctly receives 400; a supported finite control is tested separately.

## Validation

- `make setup`: PASS, locked dependencies verified.
- `make test`: PASS, **275 unittest tests**, zero failures/errors/skips; all four
  original/active offline scripts; 16 HTTP smoke checks; package integrity,
  JavaScript/shell/JSON checks and unchanged archive/migration checksums.
- `make audit`: PASS, **275 tests**, zero failures/errors/skips; all seven original baseline defects still reproduced.
- Dedicated STEP 1 reliability: PASS, **39 tests**.
- Dedicated STEP 2 process/filesystem/provisioning: PASS, **69 tests**.
- Dedicated controller/persistence/recovery: PASS, **95 tests**.
- Dedicated STEP 3 API/workflow/frontend/proxy/adapter: PASS, **72 tests**.
- New STEP 3 regressions alone: PASS, **41 tests**.
- `git diff --check`: PASS.

Parallel invocations of the existing process suites collided on their fixed
8188/8189/7860 fixtures. Those runs are not recorded as PASS. Final process
suites and full audit run sequentially, with assertions and deadlines unchanged.
Two active offline scripts now import the application as a package so the new
relative validation imports resolve; all their assertions remain intact.
Existing terminal-history fixtures include the expected PreviewAny identity.

Submission uncertainty, durable idempotency, persistence safety generation,
pidfd/service ownership, subprocess/group cleanup and transactional provisioning
are covered by the full existing regression suite. Actual GPU execution and real
Vast lifecycle behavior remain outside this CPU verification.

## Changed files

```text
docs/STEP3_FIX_VERIFICATION_2026-10-08.md
h3/app/main.py
h3/app/proxy.py
h3/app/workflow_validation.py
h3/scripts/integration_matrix_test.py
h3/scripts/offline_selftest.py
h3/static/index.html
h3/workflows/VAST_H3_MASTER_NATIVE_INT8_96GB.json
h3/workflows/VAST_H3_PROMPT_ONLY_STAGE2.json
tests/fixtures/step3_frontend.js
tests/step3_helpers.py
tests/test_adversarial_persistence.py
tests/test_controller.py
tests/test_final_blocker_fixes.py
tests/test_reliability_submission.py
tests/test_step3_api.py
tests/test_step3_frontend.py
tests/test_step3_proxy.py
tests/test_step3_workflows.py
tests/test_submission.py
tests/test_workflows.py
```
