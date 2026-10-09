# AJ/H3 CPU product acceptance — 2026-10-09

Scope: finish the agreed offline/CPU Ref2Video features before renting GPU compute.
Branch: `feat/h3-cpu-product-completion`, based on current `main`
`15b66f0f1475958a3e6c60ef36ec0cf0a61e51f7` (PR #3).
This report supplements the historical [8 October baseline](FINAL_CPU_ACCEPTANCE_2026-10-08.md).

## Validation

`make setup` **PASS** (pinned dependencies and `pip check`).
`make test` **PASS**: **358 tests + 16 HTTP smoke checks**.
`make audit` **PASS**: **358 tests**, with all seven original baseline defects
still reproduced by the historical probes. Both full suites have zero
failures/errors/skips and completed teardown. The feature set adds **68 focused
tests** to the 290-test baseline. No GPU/live acceptance is claimed.

Package integrity **PASS**: 66 original files unchanged, 23 JSON documents parsed,
17 shell scripts checked and frontend JavaScript syntax checked. Behavioral
frontend fixtures are included in the full regression counts above.

The session container's PID 1 is `tail`, which does not reap orphaned processes.
Repeated process suites accumulated zombies and produced intermittent existing
service-control deadline failures. The affected unchanged service tests also
passed five consecutive focused runs before the final full audit. Final full
commands run in a fresh local PID namespace with a disposable init under ignored
`.local/`. That init reaps only its
namespace's children, preserves the original Make exit status and fails if live
children remain after teardown. No existing assertions, timeouts, production
service controls or repository test commands were weakened or replaced. This is
development-environment isolation, not an AJ feature or a deployed init service.

The implementation/test tree identities below associate the evidence with the
exact tested sources; the containing commit is the PR head:

- `h3/` Git tree: `67bdcc115cb391d0346d51cbed2a720ddcd75301`.
- `tests/` Git tree: `c6c195de3b6e0b1e9d66f288f85ea3ae0530e9ab`.

Local panel verification also uses `make panel`, `scripts/dev_panel.py check` and
authenticated HTTP requests to the new config/diagnostics/log endpoints. The
preview uses isolated `.local/` state, submissions disabled and no GPU workers.
Only the preview started by this workflow is stopped afterward.

## Implemented product behavior

- Completed render results expose canonical `video_outputs`, original render
  start, observed finish and calculated duration when possible. Queue / Results
  prioritizes **OPEN MP4** and keeps batch/candidate/job/seed as secondary metadata.
  Legacy result reads do not rewrite state. The Range/streaming proxy remains.
- Parallel stays the sole scheduling mode. `H3_PROMPT_PREFETCH=3` defaults to a
  bounded future-render buffer; integers clamp to 1–5. One active prompt reserves
  a slot, active rendering does not occupy the future buffer, and approved reviews
  retain their existing render priority. Unapproved reviews remain decisions,
  allowing all five review candidates to finish for a finite queue.
- Collapsed Advanced holds read-only Diagnostics, bounded Live Logs and existing
  Render/Prompt restart controls. Diagnostics probes independently with short
  deadlines and labels model presence as provisional. It invokes no Vast control.
- Logs accept three fixed identifiers, at most 1,000 lines and 256 KiB read, with
  replacement UTF-8 decoding. Missing files are normal; symlinks/FIFOs fail closed.
- Terminal is disabled by default (`H3_ENABLE_TERMINAL=0`), with one optional
  interactive PTY in `WORKSPACE`. An authenticated HTTP handshake issues a random
  30-second one-use ticket; the WebSocket independently consumes it via a
  subprotocol header. Invalid/expired/reused tickets fail before process launch.
  Enable it only over a trusted/private connection. AJ saves no command history.

## Safety self-review

| Area | Review and CPU evidence |
| --- | --- |
| Duplicate paid submission | The sole Render `POST /prompt` boundary, durable UUID/arm and uncertain-acknowledgement recovery remain intact. The prefetch recheck occurs before arming and is a known no-POST deferral. Existing crash/fsync/ack-loss regressions remain; result recovery adds an explicit no-resubmission assertion. |
| Scheduler races | Tests cover targets 1/2/3, global automatic-before-unstarted-review priority, eventual reviews, approved-review buffer occupancy, approval during preparation, draining and reconstructed buffers. Actual phase coroutines overlap with one mocked POST per service and refuse a second heavy render. Immediate/cached completion yields between jobs so one worker cannot starve the other or controller callbacks; an actual worker-loop test covers this. No global GPU mutex was added. |
| Recovery and durability | Canonical legacy normalization copies results without writes. Timing preserves the original recovered start or uses settled history when available. Existing controller/worker restart, cancellation, watchdog, malformed-state and persistence-failure fences remain. |
| Lifecycle | Existing STOP/DESTROY plans, guards, destructive confirmations, telemetry and retained-volume proof tests remain. Diagnostic-only probe deadlines do not change lifecycle verification defaults. Pending/rejected reviews still do not block draining; unsubmitted review preparation is skipped. No real lifecycle action was executed. |
| Log/file disclosure | Client paths are ignored; only fixed service filenames are opened. `O_NOFOLLOW`, nonblocking open and regular-file checks reject symlinks/FIFOs. Tests assert bounded reads, truncation, replacement decoding, invalid services/line counts and HTTP authentication. |
| Terminal ownership/leaks | Separate gated supervisor and shell pidfds are captured before Bash execution. The supervisor is a Linux subreaper; disconnect/error/parent death triggers descendant cleanup. Tests use real PTYs, ignored TERM, new process sessions, non-UTF-8 process names, shell exit, launch/capture failure, Ctrl+C, shutdown and repeated cancellation. Descriptor EIO cannot skip reaping; transient group-signal failure retries the same capability. Cleanup uncertainty retains ownership and blocks another shell. |
| WebSocket authentication | HTTP middleware is not trusted for upgrades. Tests reject Basic Auth alone, query tickets, invalid/expired/reused tickets and cross-Origin upgrades; ticket consumption has no intervening await. Real loopback HTTP/WebSocket integration exercises the handshake, shell stream, disconnect/reaping and replay refusal. Tokens are not put into client URLs or echoed in exception output. |
| Frontend async behavior | The actual inline JavaScript runs under deterministic Node fixtures. Tests cover stale diagnostics, log-source changes, closed sections, duplicate connects, disconnect during handshake and stale socket callbacks. Raw logs/PTY output use text content; transcripts are bounded and commands are not persisted. Existing batch/approval/lifecycle retry and race tests remain. |
| Historical material | Package integrity checks verify all 66 received original files. `migration/` and `archive/` have no diff from the main baseline. No checksum expectations or existing safety tests were weakened. |

## Separately pending: final model decision and live acceptance

The current model manifest remains provisional. Final production model sources,
revisions and checksums were not selected or frozen by this work.

CPU evidence cannot verify:

- Actual model loading and the real ComfyUI/custom-node `/object_info` contract.
- Real H3 inference, output quality and playback of an actual final rendered MP4.
- Render-only versus Parallel overlap throughput, peak VRAM, OOM/restarts and
  the final measured prefetch target on the single target GPU.
- Actual Vast CLI authorization, billing, STOP/DESTROY, provider volume attachment
  proof, data retention and reattachment/bootstrap after compute destruction.
- Service paths/restarts and optional terminal transport through the chosen
  trusted/private deployment connection.

Follow [ROADMAP.md](ROADMAP.md): feature completion -> full CPU regression/audit
-> separate final-model selection/freeze -> one consolidated
[Vast/GPU acceptance session](GPU_ACCEPTANCE.md). No rental, paid inference or
real Vast STOP/DESTROY was performed. GPU/live acceptance remains **pending**.
