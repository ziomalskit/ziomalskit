# Four runtime blocker fixes: CPU acceptance

Base: `f5067c2c94d4ea6f90727c3493a3af35bfbf76fb` on
`fix/aj-runtime-blockers`, confirmed after `git fetch origin`.
Its parent is `652a7b64b625d5fcd696c8ce6e0ec16d15aeccf9`.
Scope is the four blockers in the final independent audit. Migration and archive
source files remain unchanged.

## Behavior

- Batch creation commits its client UUID, request fingerprint, original response,
  jobs and batches in one atomic queue snapshot. Retries after restart replay the
  response. Different UUIDs permit intentional identical requests. Missing UUIDs
  fail closed with HTTP 400; changed payloads for an existing UUID return 409.
  AJ stores the exact pending request before POST and retains it across reloads.
  Approval stores its response and approved prompt with its job, making retries
  harmless even after completion. Existing legacy publication markers still
  require manual reconciliation.
- Lifecycle arms capture the safety generation. Queue persistence errors
  immediately invalidate armed/executing plans even when lifecycle persistence
  also fails. Recovery never restores an old arm. The guard and final dispatch
  boundary verify the generation; restart interrupts old arms and executing
  actions. Explicit re-arm is required.
- Confirmed ComfyUI terminal results stay in memory after local EIO. Persistence
  degradation blocks dispatch, workers remain alive, and recovery saves the same
  outcome. Successful prompts subsequently render once without re-submission.
- Service launch and pidfd registration share the same parent, keeping the
  child's PID unreaped until pidfd capture. Every registration exception after
  capture performs TERM, bounded wait, then KILL through that same pidfd, removes
  the PID record and waits for exit. Existing foreign/reused PID protection stays
  in force.

## Final validation

All commands below exited 0 on the final code:

| Check | Result |
| --- | --- |
| `make setup` | PASS; dependency integrity clean |
| `make test` | 155 tests, 0 failures/errors/skips; 97.484 s |
| `make audit` | 155 tests, 0 failures/errors/skips; 95.817 s |
| Original audit probes | All 7 original defects reproduced in preserved baseline |
| Offline selftests/integration | 4 PASS results: both suites for baseline and active `h3` |
| HTTP smoke | 16 functional checks PASS |
| New repository tests | 19 PASS: 15 runtime/adversarial and 4 frontend JS behavior tests |
| Existing persistence/PID adversarial tests | 23 PASS, included in the 155 |
| `git diff --check` | PASS |
| Independent reproductions outside repository | 26 scenarios PASS |

The independent script `/tmp/aj-fixes-independent.py` did not import the new test
classes. It used actual SIGKILL, ASGI HTTP requests, real worker loops and real
child processes, while mocking remote ComfyUI/Vast boundaries. Its scenarios:

- Eight crash/restart/retry cases: batch and approval at temporary-file fsync,
  replace, directory fsync and after memory publication. Each batch performed
  exactly five mocked render submissions; each approval performed exactly one.
- One HTTP contract case: 32 concurrent same-key requests, changed-body conflict,
  missing/invalid UUID and intentional identical content with different UUIDs.
- Three lifecycle arms: EIO in queue and lifecycle writes, recovered persistence,
  zero old-plan dispatches, restart interruption and explicit re-arm.
- Two terminal outcomes: prompt and render success with one-shot actual fsync EIO,
  observable ready=false, live worker, recovered ready=true and no duplicate POST.
- Ten PID registration failures: `/proc/stat`, cwd read, identity timeout, temp
  creation/write, replace, file fsync, directory open/fsync and ignored TERM.
  Every owned child died; the unrelated process remained alive; no PID/temp file
  remained. Ignored TERM required pidfd KILL.
- Two uncertain destructive outcomes: STOP and DESTROY timeout after one mocked
  call, followed by guard ticks with no automatic retry.

No remaining blocker was reproduced in this scoped CPU validation. No live GPU
render, real ComfyUI submission or Vast lifecycle action was performed. Native
INT8 loading, dual-worker VRAM behavior, actual generated media and live Vast
storage/STOP/DESTROY behavior still require deployment acceptance; these are
separate from merge acceptance for the four fixes.
