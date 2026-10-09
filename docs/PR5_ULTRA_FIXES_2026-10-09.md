# PR #5 focused Ultra fixes — 2026-10-09

Starting reviewed commit: `d3d4d768668a30868467013f68d5b634af7f06b6`.
Base: `bfcc2bb22ff9691fda924fe17bd060a7e131c2e2` (PR #4).
The final fix commit is recorded in PR #5's description and the completion report.
Scope is the nine proven findings. No prompt/model redesign, model download,
Vast operation, GPU inference, merge, or attempt to resolve 9B provenance.

## Targeted self-review

Code aliases below: `execution_journal.py`, `compiler.py` and `temporal.py`
are under `h3/custom_nodes/aj_production/`; `main.py`, `production.py` and
`redaction.py` are under `h3/app/`; `model_downloads.py` is under `h3/scripts/`.

| Finding | Verdict | Code and adversarial evidence |
| --- | --- | --- |
| 1. Watchdog success race | **FIXED** | `execution_journal.py:43` fsyncs the complete terminal record before pinned `PromptQueue.task_done` releases running ownership. `quiesce:134` closes `put/get` admission under the same mutex; readiness requires no running/pending execution. Controller `main.py:1192` and manual restart both require that barrier before executing restart commands. `fetch_durable_result:1225` reconciles journal entries after native history is reset. `test_ultra_fixes.py:255` executes the exact running-snapshot → successful paid MP4 → acknowledged barrier → restart ordering with the verbatim pinned queue; native history is empty afterward and both controller memory/disk retain the original UUID and MP4 link. The journal is installed during custom-node import before the pinned worker thread starts. `test_production_nodes.py:20` proves this startup ordering. The restart barrier additionally fsyncs native terminal history that bypassed a newly installed hook through an already-bound original method; `test_ultra_fixes.py:60` proves that case. Fsync failure, unowned work and missing barrier have separate negative tests. |
| 2. Pending cancellation fence | **FIXED** | `execution_journal.py:117` persists a UUID-bound pending-deletion tombstone before removing pending work under the queue mutex. Running interruption returns a distinct `running_signalled` state and does not create terminal proof. `main.py:1005` validates the protocol/state/UUID; recovery at `2688` consumes a persisted pending receipt. Tests at `test_ultra_fixes.py:88,99,302,312,332` cover worker restart, no native history, lost HTTP reply and genuinely ambiguous running cancellation. No case resubmits uncertain work. |
| 3. Incompatible job hides catalogue | **FIXED** | `main.py:526` validates identity per job and quarantines incompatible nonterminal jobs as `reconciliation_required`, keeping original status and execution IDs. Terminal statuses/results stay readable; no new identity defaults are assigned. Quarantined remote identities fence dispatch/lifecycle actions and appear in Diagnostics. `test_ultra_fixes.py:352` loads one paid completed result with one incompatible old active job and verifies catalogue visibility, original UUID and fences. Existing model/batch drift tests now require visible, individually quarantined records. |
| 4. Unsafe HF staging | **FIXED** | `model_downloads.validate_staging` walks all ancestors and all retained descendants with dirfds/no-follow opens before any downloader and again at the SDK boundary. Links, hardlinks and nonregular entries are rejected; existing size/SHA256/no-overwrite publication remains. `test_ultra_fixes.py:141` places an actual nested symlink to an outside sentinel: the writer is never called and sentinel bytes stay exact. The FIFO/hardlink test is at `test_ultra_fixes.py:156`. The private store's existing cooperative flock remains; a hostile same-uid process concurrently rewriting the store is outside this retained-staging boundary. |
| 5. Inline extra cuts | **FIXED** | `temporal.structural_headers:44` detects structural shot markers anywhere outside clearly quoted/escaped references. `final_timeline:196` accounts for every unquoted timecode cut directive and binds it to the canonical shot start; inline/malformed/misplaced markers cannot become prose merely through placement. Only canonical timestamps may be replaced. `test_production_controller.py:481` follows the inline 90-second extra cut through guard, approval, recovery and actual render graph preparation; each rejects before submission. Tests at `509` and `test_production_nodes.py:231` preserve clearly quoted/escaped references byte for byte. The 20 → 481 → 20.040 identity remains exact. |
| 6. Truncated Step 4 publication | **FIXED** | `compiler.completion_response:24` requires one final assistant choice, `finish_reason=stop`, verbose `stop=true`, `stop_type=eos`, `truncated=false`, and a valid final channel; errors/missing evidence fail closed. The pinned CLI drops finish metadata, so `run_completion:79` uses one private local JSON request to its sibling `llama-server` from the **same** b10472 commit, then terminates/reaps it. `verified_server:45` checks binary SHA, pinned build identity/CUDA/architecture and reported revision. `compiler_file_response` also refuses framing without matching completion evidence. `test_ultra_fixes.py:171,176,193,206` cover six visibly truncated sections, length/context/error/stop-word paths, absent finish metadata, build tampering and an actual local CPU child/JSON exchange with teardown. Existing pinned-command tests retain exact thinking, total budget, context, seed, input cleanup, quoted text and reasoning separation assertions. |
| 7. Frozen memory/overlap | **FIXED** | `production.functional_policy:14` defines functional identity. `main.profile_identity:69` freezes memory release, sampling priority, overlap permission/headrooms and compiler admission route. Recovery validates those frozen fields; phase graph preparation and `phase_admission:2502` use them. Active render policy is also frozen; missing identity defers overlap. `test_production_controller.py:517` changes later defaults, reloads the job and proves old identity/admission survives while new identities receive the edits. GPU validation remains pending; no measured VRAM safety is claimed. |
| 8. Detached terminal descendants | **FIXED — containment** | `main.py` hard-disables Terminal regardless of `H3_ENABLE_TERMINAL`; all production session/WebSocket/UI gates stay closed. ROADMAP explicitly supersedes the earlier opt-in promise. `test_ultra_fixes.py:367` imports with runtime opt-in **1**, verifies disabled configuration, refusal and no PTY construction. Existing experimental PTY/auth/cleanup tests remain, including their original assertions. Detached descendant ownership itself is not solved or claimed; production cannot launch this unsupported feature. |
| 9. Provider credential exposure | **FIXED** | `main.run_vast_cli:2003` passes `CONTAINER_API_KEY` through the provider-supported, child-scoped `VAST_API_KEY` environment; no `--api-key` argv. Provider failure output is suppressed and exceptions sanitized. `redaction.py:8`, `_lifecycle_failure:2255`, `save_vast_control` and its atomic writer redact **before** durable lifecycle publication; Diagnostics/log tails are independently redacted. Ordinary job/prompt identities are not rewritten by the lifecycle sanitizer. `test_ultra_fixes.py:375` runs an actual argv-equivalent local child emitting a sentinel from its environment and proves the secret is absent from command arguments, exception, durable state, Diagnostics and log response. No provider executable is invoked. |

## Completion protocol provenance

Unchanged source pins:

- ComfyUI `6b747c0428c343e1417219641db93a4fb7cb69ae`.
- llama.cpp/converter `60eeeb6082c1126bb8bc72902c83123cd056811b`, release b10472.
- LLM node `65983de33681816f856db7e16767da906084c688`.

Pinned `tools/cli/cli-server.h` explicitly starts this same server engine in a
thread and selects a random local port. CLI output-file framing omits its
machine-readable finish metadata. Pinned `tools/server/server-task.cpp` maps
EOS/stop-word to OpenAI `stop`, other limits to `length`, and emits verbose native
`stop_type`/`truncated`. AJ additionally requires EOS, so a stop word cannot
masquerade as natural completion. `server-common.cpp` forwards `verbose` and
reasoning-budget request parameters. `common/arg.cpp` supports the preserved
reasoning controls and `--no-context-shift` in server mode.

Provisioning now builds/installs/verifies both executables from the same clean
pinned checkout with matching CUDA/Blackwell receipt checks. It can reuse only
when **both** build signatures/hashes match. There is no serving daemon shared
between jobs, pin upgrade, global node monkey patch, or change to v20 instructions.
CPU fixtures verify plumbing and fail-closed semantics, not real model inference.

The queue fixture is a verbatim `PromptQueue` excerpt from pinned `execution.py`.
Full source SHA256 is
`c9ef8ea11b8cb7aa80d05f670ca457211869d27623991b66d0014fb9b33fb246`;
its excerpt receipt is committed beside the fixture and checked by a test.
Pinned `main.py` constructs PromptServer at line 520, imports custom nodes at 526, and starts the prompt worker thread at 543; the output directory is configured first. The retained journal ancestor-link test also proves initialization creates no outside directory.
Provider CLI support was checked in official Vast `vast.py`: `--api-key` defaults
to `os.getenv("VAST_API_KEY", api_key_guard)`. No provider account call was made.

## Validation

Final validation on the source being committed:

| Check | Result | Failures / errors / skips |
| --- | --- | --- |
| `make setup` | **PASS**; pinned dependencies installed, `pip check` found no broken requirements | n/a |
| `make test` | **PASS: 507 tests**, 355.631 seconds, plus **16 HTTP smoke checks** | **0 / 0 / 0** |
| `make audit` | **PASS: 507 tests**, 330.434 seconds; all seven original baseline defects reproduced | **0 / 0 / 0** |
| Focused adversarial/contracts suite | **PASS: 130 tests**, 14.461 seconds | **0 / 0 / 0** |
| Isolated subprocess-cleanup investigation | **PASS: 4 tests**, 1.227 seconds | **0 / 0 / 0** |

The focused command was `.venv/bin/python -m unittest -v`
with `tests.test_ultra_fixes`, `tests.test_temporal_contract`,
`tests.test_production_nodes`, `tests.test_production_controller`,
`tests.test_artifact_storage`, `tests.test_model_provisioning`,
`tests.test_submission` and
`tests.test_deployment.DeploymentTests.test_llama_setup_verifies_pinned_binary_and_reuses_bounded_build`.
All 28 new test names below were checked against the reviewed commit by AST.
The final full audit summary records 507 tests, zero failures/errors/skips,
zero real ComfyUI submissions and zero Vast actions.

One earlier final-audit session ended abruptly without a traceback or summary.
No audit process remained, and cgroup counters showed no OOM kill or PID-limit
event. The four cleanup tests around that interruption passed independently;
the complete rerun then passed all 507 tests, including that point. The cause
of the interrupted execution session remains undetermined; its incomplete log
and the older 504-test summary were not counted as final evidence.

The initial full development run recorded **501 tests, 2 failures and 1 error** from contract fixtures. Earlier development runs exposed
contract fixtures that still expected the old boolean cancel endpoint and a
CMake fixture creating only `llama-cli`; those fixtures were updated to assert
the stronger protocol and both pinned binaries. No check was disabled, assertion
weakened, timeout increased, or failure relabelled as a pass.

The earlier reviewed-head ENOSPC timeout was investigated without changing its
8-second deadline. Three sequential instrumented reruns passed in **6.972,
6.631 and 7.010 seconds** (1 test each; zero failures/errors/skips). The fixture
performs eight real v20 graph conversions, about 0.34–0.43 seconds each, in
addition to scheduler preparation/pacing. This explains sensitivity to competing
CPU load. Full validation runs sequentially. A disposable Linux subreaper
parent reaps exited descendants adopted from its own test command, because
this container's PID 1 does not reap orphans; it does not alter tests or signal
foreign processes. Teardown assertions stay active.

## Preserved integrity and remaining gate

All **72** tracked `migration/` and `archive/` files match the reviewed starting
commit's Git blob identities. No file in either tree was changed.
Canonical `h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json` remains exactly
**3,084,071 bytes**, SHA256
`276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.

Production profiles, the complete manifest and native writer stage defaults
remain unchanged. H3 Full retains 4B Heretic Q8; 10Eros Full retains the DavidAU
9B BF16 target. Step 3 thinking stays OFF; Step 4 stays ON, reasoning budget
4096, total generation 12352, context 29184, final allowance 8192 plus framing.
Two services remain on 8188/8189, one heavy render, bounded prompt prefetch,
durable no-blind-resubmit rules, Stage 5 lifecycle fencing and dynamic LoRAs.

**STILL BLOCKED:** real DavidAU 9B production provenance/build/load/generation.
The manifest remains `blocked_source_provenance`; observed revision
`72f2d803ae63499fc463607d997fe48abd38f093` is not authoritative. No IQ4_XS
substitute, source/output checksum fabrication, real BF16 conversion or model
probe was performed. GPU/VRAM/render/live Vast acceptance remains pending.
PR #5 must stay **Draft**. Nothing was merged; no Vast/GPU action occurred.

## Exact new adversarial tests

**28 new tests**; existing assertions and deadline limits remain active.

`tests/test_ultra_fixes.py` (23 new tests):

- `test_original_bound_completion_before_hook_is_durable_at_restart_barrier`
- `test_retained_journal_ancestor_symlink_creates_nothing_outside`
- `test_pinned_prompt_queue_fixture_preserves_authoritative_excerpt_identity`
- `test_pending_deletion_receipt_survives_worker_restart_without_native_history`
- `test_running_cancellation_cannot_forge_pending_deletion`
- `test_failed_result_fsync_retains_running_ownership_and_refuses_restart`
- `test_quiesce_fences_new_admission_and_refuses_unowned_execution`
- `test_pending_tombstone_write_failure_cannot_delete_work`
- `test_nested_retained_symlink_never_reaches_sdk_or_changes_outside_sentinel`
- `test_fifo_and_hardlink_staging_are_rejected_before_writer`
- `test_six_headers_with_visibly_truncated_final_section_and_no_receipt_are_rejected`
- `test_length_context_stream_runtime_and_truncation_finish_paths_are_rejected`
- `test_natural_eos_keeps_final_and_reasoning_channels_separate`
- `test_completion_runtime_requires_pinned_build_receipt_and_exact_binary_hash`
- `test_real_local_json_runtime_is_reaped_and_truncated_transport_rejected`
- `test_watchdog_running_snapshot_then_success_then_restart_retains_paid_output_identity`
- `test_confirmed_pending_receipt_finishes_cancelled_without_history_or_remote_calls`
- `test_lost_pending_cancel_reply_recovers_from_durable_worker_tombstone`
- `test_running_cancel_receipt_remains_uncertain_and_never_resubmits`
- `test_restart_without_durable_barrier_never_launches_control_command`
- `test_bad_profile_is_quarantined_without_hiding_completed_mp4_or_uuid`
- `test_terminal_runtime_opt_in_is_contained_before_any_child_launch`
- `test_provider_secret_uses_scoped_environment_and_failure_never_leaks_to_state_diagnostics_or_logs`

`tests/test_production_nodes.py` (2 new tests):

- `test_owned_journal_installs_before_prompt_worker_can_cache_queue_methods`
- `test_owned_guard_rejects_inline_extra_cut_and_keeps_clear_quoted_reference`

`tests/test_production_controller.py` (3 new tests):

- `test_inline_extra_cut_is_rejected_through_guard_approval_recovery_and_render_preparation`
- `test_quoted_and_escaped_inline_shot_references_remain_exact_through_approval`
- `test_frozen_memory_and_overlap_recovery_ignores_later_functional_config_edits`
