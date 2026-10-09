# PR #5 finalization — 9 October 2026 (Europe/Warsaw)

PR #5 remains **Draft, not merged**. The independent CPU work implements native
stage-specific thinking and deterministic timeline arithmetic. Canonical DavidAU
9B source retrieval is still blocked by the enforced network policy; its real
conversion and CPU load have **not run**. GPU acceptance has **not run**.

## Starting state

- Continued existing branch `feat/v20-full-production-models` at exact head
  `b19b54adb28fa5e851ce35d09ef1351940fa1777`; initial working tree was clean.
- Fetched/checked `main` and PR #5. Its base is merged PR #4 commit
  `bfcc2bb22ff9691fda924fe17bd060a7e131c2e2`; required ancestry passed.
- Before edits, PR #5 CPU Actions run **37940804048**, run 31, was **success**.
- Existing draft: <https://github.com/ziomalskit/ziomalskit/pull/5>. No new PR,
  new branch/worktree, rental, provider mutation, paid submission or merge.
- Canonical v20 remains **3,084,071 bytes**, SHA256
  `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
  All saved instruction strings match the original baseline. No v21/v22 content.

## Pinned runtime and reasoning evidence

| Component | Unchanged immutable revision |
| --- | --- |
| ComfyUI | `6b747c0428c343e1417219641db93a4fb7cb69ae` |
| llama.cpp b10472 | `60eeeb6082c1126bb8bc72902c83123cd056811b` |
| LLM custom node | `65983de33681816f856db7e16767da906084c688` |

The planned converter is `convert_hf_to_gguf.py` at that same llama.cpp revision,
SHA256 `e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`.
Its identity is pinned; real canonical 9B conversion/load evidence is unavailable.

Inspected exact pinned `common/arg.cpp`, `common/chat.cpp`,
`common/reasoning-budget.cpp`, `tools/server/server-context.cpp`,
`tools/cli/cli-context.cpp`, `tools/cli/cli-ui.h`, and the LLM node/CLI Python.
The existing runtime supports `--reasoning on/off`, `--reasoning-format deepseek`,
`--reasoning-budget N`, and `--output-file`. No invented flags or runtime upgrade.

Built that exact llama.cpp commit CPU-only, with CUDA/OpenMP disabled. Its real
`llama-cli --help` confirms the flags. Its native reasoning-budget executable
passed **12 sampler tests** and the UTF-8 boundary check. No model was loaded by
these checks. Actual model-template/quality/VRAM acceptance remains pending.

| Profile | Step 3 | Step 4 |
| --- | --- | --- |
| H3 Full | Heretic 4B Q8_0, thinking OFF | Same 4B, thinking ON |
| 10Eros Full | Intended canonical DavidAU 9B BF16 GGUF, OFF | Same intended 9B, ON; source/build blocked |

Step 3 retains generation **8,192**, context **24,576**. Step 4 uses native
reasoning budget **4,096**, total generation **12,352** = 8,192 final + 4,096
thinking + 64 framing headroom. Context **29,184** rounds up to the node's 512
step and retains at least the original 16,384 input-token allowance. Reasoning
consumes the same total generation counter. The native sampler budgets each
reasoning block, waits for a UTF-8 boundary, then forces its end tag; it can
re-arm on another block. This is a finite configuration allowance, not a claim
that real writers cannot truncate. Incomplete/ambiguous output fails closed;
actual structured-output reliability and optimal budgets remain benchmarks.

Jobs persist a deep copy of the stage policy, core model routing, exact runtime
pins, and a fingerprint of immutable required manifest facts. Recovery uses
those values. Changed checkpoint/writer filenames, same-name artifact hashes,
conversion identity or runtime pins require explicit reconciliation. GPU labels
and diagnostic wording do not change artifact identity. Changed stage defaults
apply only to newly created jobs. Older jobs without these identities are fenced.

### Final-channel separation

The pinned CLI exposes native `content` and `reasoning_content`, but its Python
node combines stdout/stderr and splits at the first prompt/timing marker. That
can truncate legitimate quoted answer text. `AJCompilerTextProcessor` delegates
exactly one invocation, cancellation and temporary-input cleanup to that same
pinned node, requests its native output file in a private temporary directory,
and discards the lossy stdout result.

The adapter verifies the exact known `User` record (including the pinned prompt
padding) and parses the native `Assistant` record. It requires an unambiguous
reasoning/final boundary and the original six lowercase sections. It never
regex-strips arbitrary final text; quoted tags, prompt-echo and timing strings
remain intact. Native terminal framing newlines are removed. Only response port
0 can feed the pipeline; reasoning/performance ports are rejected. Missing,
nested, malformed or incomplete output fails before a render can be queued.
Synthetic tests execute the verbatim pinned Python node/command builder against
a fake subprocess, including timeout/reaping and temporary-file cleanup.

## Deterministic temporal correction

The canonical Generation Settings expression is preserved:

```python
max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
```

Its existing duration-length expression is also preserved:

```python
floor(round(a / 24, 4) * 100) / 100
```

One shared pure contract executes those literal expressions and checks them
against the checksum-locked v20 graph. There is no independently rounded second
duration algorithm. Requested duration, legal frame count, effective
seconds/milliseconds and the contract version are represented in the durable
runtime identity. Recovery uses the frozen values; `AJFrozenDuration`
supplies them to both prompt and render API graphs.

| Requested seconds | Legal frames | v20 effective seconds |
| --- | --- | --- |
| 1 | 39 | 1.620 |
| 5 | 124 | 5.160 |
| 8 | 192 | 8.000 |
| 15 | 362 | 15.080 |
| 20 | 481 | 20.040 |
| 30 | 736 | 30.660 |

For 20 seconds, the request-to-effective delta is **+0.040 s**. The exact
frame ratio 481/24 is approximately 20.041667 s; v20 already floors its prompt
duration length to centiseconds. That approximately 1.667 ms distinction is
documented rather than silently changing the original frame or duration rules.
Live acceptance must compare request, legal frames, effective timeline and the
actual MP4 frame count/FPS/duration.

Runtime composition rewires master node 7453 to Generation Settings output 1
and routes Step 3/4 duration getters to its existing `duration_output` alias.
Output 2 remains the raw request. No substantive prompt instruction changes.

`AJCreativeTimelineGuard` runs after Step 3 and before every Step 4 path. It
requires sequential shot identities, one parseable canonical Timeline per shot,
zero first start and positive proposed intervals. Proposed durations become
relative weights. Integer apportionment with deterministic remainder handling
and a one-millisecond lower bound creates contiguous positive intervals ending
exactly at the target. Gaps/overlaps in proposed absolute times are repaired;
malformed/ambiguous shot structure fails. Only Timeline timecodes change, with
no added speed instructions or action-prose rewrite.

The reported 90-second plan becomes:

```text
[Shot 1] Timeline: 00:00.000-00:06.680
[Shot 2] Timeline: 00:06.680-00:13.360
[Shot 3] Timeline: 00:13.360-00:20.040
```

The controller's Step 3 preview captures this guarded plan. A graph check
proves the raw Step 3 path cannot bypass the guard. `AJFinalPromptTimeGuard`
requires the same shot identities in `detailed_description`, no Shot 1 timestamp,
and canonical later cut syntax. Wrong parseable cut times are replaced with
the authoritative guarded starts; extra/missing/duplicate/malformed shots fail.
Descriptions remain unchanged. Every final cut is strictly increasing and below
the effective endpoint because it derives from the canonical positive schedule.

The controller independently validates the frozen duration, submitted guard
mapping, captured plan and final cuts before `pending_review` or automatic
render. Approval/recovery/render preparation repeat validation. Invalid output
gets `prompt_failed` and a short safe diagnostic. Read-only Diagnostics shows
bounded requested/effective/frame/status summaries without giant prompt text.
Step 0–2 caching, epoch invalidation, seed relationships, phase-aware overlap,
one-heavy-render reservation and the single durable POST boundary remain intact.

The node installer atomically installs and hashes both the node module and its
shared temporal contract. It preserves/rejects local edits, symlinks and unrelated
files, and can verify/reuse or upgrade the previous owned single-module layout.

## External 9B blocker — unresolved

Canonical repository:
`DavidAU/Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic`.
Observed revision remains `72f2d803ae63499fc463607d997fe48abd38f093`;
it is **not** a newly verified/frozen production source pin.

The enforced managed policy still excludes `huggingface.co`. Direct CONNECT
metadata requests fail at the proxy with **HTTP 403** (curl exit 56, upstream
HTTP 000). The browsing route returns **401 Unauthorized**. The available cloud
tool is read-only; the user's existing authorization cannot change that policy
through these tools. No bypass or alternative repository was attempted.

| Required real evidence | Status |
| --- | --- |
| Complete required source file count/identities/roles/sizes/SHA256 | Not obtained |
| Independently frozen canonical source revision | Not obtained |
| Real source download and full hash verification | Not run |
| Real BF16-to-BF16 GGUF conversion | Not run |
| Generated GGUF exact size / SHA256 / complete provenance | Not available |
| Real pinned-toolchain CPU model load | Not run |

Planned output filename:
`Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic-BF16.gguf`.
No file, byte count or output checksum is claimed for that planned artifact.

The existing builder, integrity checks, deterministic provenance and verified
persistent reuse remain available, but fixture conversions do not resolve this
blocker. The manifest still has 17 pinned known artifacts plus the pending 9B
entry. Strict production preflight/provisioning/submission remains **NOT READY**.
No IQ4/Q4/Q8 substitution or serving-runtime change was made.

## Validation and review

Final local evidence on implementation commit
`10601ae27b3e18c573cc9eda30a8775d5d2d3130`:

| Command/check | Result |
| --- | --- |
| `make setup` | PASS |
| `make test` | PASS: 479 tests + 16 HTTP smoke checks |
| `make audit` | PASS: 479 regression tests |
| Focused production/writer/temporal/provenance suite | PASS: 120 tests |
| Pinned native CPU reasoning sampler | PASS: 12 tests + UTF-8 boundary check; no model loaded |
| Package integrity/syntax checks | PASS: 66 original files unchanged, 29 JSON, 17 shell scripts, inline JavaScript |
| Local panel start and authenticated check | PASS: HTML/config, exactly two profiles, stage thinking routing and read-only Diagnostics; owned preview stopped |
| Canonical v20 | PASS: exact 3,084,071 bytes and locked SHA256 |
| `migration/` and `archive/` | PASS: all 72 paths and SHA256 values unchanged |
| Strict production preflight | NOT READY: production manifest not frozen; canonical 9B source/build blocked |
| Inspection with pending artifacts explicitly allowed | PASS: CPU invariants, two profiles, v20 lock, 17 known artifacts; not production readiness |

The full/focused suites had **0 failures, 0 errors and 0 skips**. Focused command:

```sh
.venv/bin/python -m unittest \
  tests.test_production_architecture tests.test_production_frontend \
  tests.test_production_nodes tests.test_temporal_contract \
  tests.test_model_provisioning tests.test_production_controller \
  tests.test_production_api tests.test_artifact_storage
```

Tested Git tree identities: `h3` =
`a203a4dd7a1981bfcd19cc3eb9f973dd6f0e1ddb`; `tests` =
`faffd24aefac1d3f94d8b3254ba862c0551d5c5e`. The following documentation commit
does not alter those implementation/test trees. Tests use isolated disposable
state and synthetic subprocess/source/conversion fixtures; their conversion
results are not real 9B download, hash verification, conversion or load evidence.

Reviewed: durable UUID/uncertain-POST boundaries and no retries; persisted
profile/writer/thinking/manifest/duration identity; restart/cache races; one heavy
render and phase-aware admission; 96 GB H3 encoder/transformer lifecycle limits;
LoRA ID/path safety; checksum/provenance and partial-download publication;
generated-artifact/source/toolchain mismatch; token/runtime.env/log/Diagnostics
disclosure; no legacy production fallback; no v21/v22 contamination; duration
rounding/apportionment, malformed plans, cut repair and no prose rewrite.

The path/provenance review includes untrusted repository URLs, shell arguments,
malformed manifest values, same-name wrong files, symlinks/non-regular files,
partial output publication and stale recipe/toolchain reuse. CPU checks found no
remaining regression in these reviewed paths. Real source/model behavior and
measured GPU safety remain unresolved as described above.

All Vast/GPU behavior remains pending: H3 BF16 black-frame risk/loading,
encoder unload/peak VRAM, 10Eros loading, model switches, LoRA compatibility and
strengths, real reasoning/template/schema/truncation behavior, Prompt/Render
overlap and GPU-pressure caches, speed/MP4 playback, OOM/restart behavior,
persistent-volume retention, Vast STOP/DESTROY and billing/cost behavior.
