# v20 Heretic / Full model migration — 2026-10-09

The CPU architecture implements **H3 Full** and **10Eros Full** from PR #4's
accepted runtime. Production readiness is **blocked by incomplete canonical 9B
BF16 source metadata**. This report does not claim that the complete manifest is
frozen, that a real 9B conversion succeeded, or that GPU acceptance passed.

## Starting point and immutable prompt source

Fetched `main`, verified PR #4 was merged, and verified the required merge
commit before edits. The exact starting commit is
`bfcc2bb22ff9691fda924fe17bd060a7e131c2e2`. Work uses the fresh branch
`feat/v20-full-production-models`; no previous stuck-task branch or worktree was
recovered. The PR is not automatically merged.

The supplied `H3_v20_heretic_MASTER_DURATION_EXACT.json` was verified before use:

- Exact size: **3,084,071 bytes**.
- SHA256: `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
- The repository copy is byte identical to the supplied attachment.

Both small render-template descriptors compose the same immutable v20 source.
They contain no copies of Step 0–4 instructions. The instruction lock covers
both LLM prompt widgets and the connected `PrimitiveStringMultiline` / string
instruction sources. Tests compare composed instructions with the baseline and
detect changes to the connected Step 1–4 instruction primitives. No v21/v22
Director Refined, later action-budget, motion framework, continuity or timing
instructions were imported. The original v20 duration locks and six-section
compiler contract remain exact.

## Production routing and retained runtime behavior

Only `h3_full` (**H3 Full**) and `10eros_full` (**10Eros Full**) are normal UI
choices. Every new batch and all ten jobs persist the selected profile, routing
identity, requested duration, resolved LoRA selection and bridge defaults before
publication. A later default or LoRA-default change cannot change queued work.
Recovery validates the stored job/batch identities; changed checkpoint/writer
routing requires explicit reconciliation rather than silently drifting.

The shared Step 0–2 stack is JoyCaption F16 plus its F16 projector, NSFW Caption
V4.5 F16 plus its F16 projector, and Philadelphia Gemma F16 plus its matching
F16 projector. The forensic prompts, full image and overlapping 3×3 tiles are
preserved. One batch analysis seed and the original candidate seed relationship
remain: Step 1 = analysis seed, Step 2 = analysis seed + 1, Step 3 = candidate
seed, Step 4 = candidate seed + 1; Step 0 retains its stable baseline seeds.

H3 Step 3/4 use the independently pinned Heretic 4B Q8_0 writer. 10Eros Step 3/4
route to the intended generated 9B **BF16** GGUF, whose build is currently
blocked. The two stages preserve separate prompts, seeds, temperatures and
outputs, with no vision projector. Finalization changes Step 3 to native thinking
OFF and Step 4 to ON with a 4,096-token native budget. Step 3 remains 8,192 /
24,576; Step 4 uses 12,352 generation / 29,184 context to retain final-answer and
input capacity. The CLI native file adapter separates final text safely. See
[PR #5 finalization](PR5_FINALIZATION_2026-10-09.md) for temporal guards, exact
duration arithmetic, pinned-runtime evidence and updated validation counts.
A configuration-only writer-to-Gemma Step 4
alternative is an acceptance benchmark option; it is not a normal UI control.

Existing OPEN MP4, render timing, authenticated Range playback, bounded prefetch,
Diagnostics, Live Logs, optional terminal, durable queue, idempotency, uncertain
POST recovery, cancellation, watchdog, worker recovery, review priorities and
STOP/DESTROY plans remain covered by the accepted regression suite. Normal
production cannot silently select the preserved diagnostic INT8 workflows.
Old jobs without production identities retain remote-submission reconciliation
but require explicit migration before new production dispatch.

## VRAM, overlap and analysis epochs

The topology remains one instance, Render ComfyUI `127.0.0.1:8188`, Prompt
ComfyUI `127.0.0.1:8189`, panel `:7860`, one worker of each type and at most one
heavy render. No global GPU mutex was introduced.

Both render conditioning paths must complete before the AJ-owned encoder-release
boundary, which must complete before the late transformer loader and either
sampling pass. The separate video boundary releases managed render resources
after video creation. The controller also releases idle Render ComfyUI models
at dispatch boundaries. This establishes explicit dependencies for the roughly
51 GB encoder and 66 GB H3 transformer; CPU tests cannot prove the actual GPU
unload timing or allocator behavior. There is no new per-sampling-step streaming
loop. Memory policy remains configurable and visible in Diagnostics.

H3 sampling defers cold Step 0–2 regardless of a permissive-looking free-memory
snapshot. Reused analysis permits the smaller writer only during a known
sampling phase with fresh measured headroom. 10Eros permits cold analysis during
sampling with measured headroom. Conditioning, decoding, unknown phases and
recovery close the overlap window. Current headroom thresholds are conservative
configuration candidates, not measured GPU recommendations. Render preparation
reserves the renderer before its first await; admission is rechecked immediately
before durable submission arming. Automatic candidates retain priority over
warm review candidates even when automatic analysis is phase deferred.

The shared analysis key excludes profile, candidate seed and LoRAs. Three exact
text outputs persist per batch and replace the heavy analysis graph on subsequent
candidates. A Prompt service UUID and controller cache epoch distinguish live
cache assumptions from durable text. Restart clears live residency assumptions;
old completions may preserve text but cannot assert new-epoch residency. The
pinned LLM node executes and reaps a standalone `llama-cli` process per stage;
this implementation does not claim that those LLM weights stay resident between
candidates.

## Metadata, storage and the outstanding blocker

[models_manifest.json](../h3/config/models_manifest.json) records **17** trusted
download identities with repository, immutable revision, exact repository path,
destination, size, SHA256, profile ownership and GPU status. Public Hugging Face
Git/LFS metadata was independently inspected; all user-supplied hashes match.
The 13 required core downloads total **219,551,087,416 bytes**. Including three
pinned optional LoRAs and Bunny gives **221,692,946,200 bytes**. A cold install
needs at least **287,973,433,568 bytes** for these downloads plus peak staging,
before the unresolved 9B source/output, external LoRAs and runtime/data space.
The provisioner reports disk requirements and refuses insufficient space.

The incomplete artifact is
`DavidAU/Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic`.
Source revision `72f2d803ae63499fc463607d997fe48abd38f093` was observed, but a
complete set of exact shard, configuration, tokenizer and index sizes/hashes
could not be independently retrieved. It remains an observed revision in
`pending_artifacts`, not a frozen generation recipe. No guessed shard sizes,
public IQ4_XS substitute, alternative serving runtime or real generated GGUF
was introduced.

The managed network policy still excludes `huggingface.co`. The network proxy
blocks direct metadata CONNECT requests with HTTP 403; the public browsing tool returns HTTP 401 for the
remaining canonical source pages. The user authorized adding the domain, but
the available cloud-environment tool only reads the enforced configuration.
No network-policy bypass was attempted.

Runtime pins are unchanged:

| Component | Immutable revision |
| --- | --- |
| ComfyUI v0.38.0 | `6b747c0428c343e1417219641db93a4fb7cb69ae` |
| llama.cpp b10472 | `60eeeb6082c1126bb8bc72902c83123cd056811b` |
| LLM custom node | `65983de33681816f856db7e16767da906084c688` |

The pinned converter's `Qwen3_5ForConditionalGeneration` and
`Qwen3_5ForCausalLM` registrations were inspected. This is architecture-family
evidence, not proof that the inaccessible exact source converts and loads.
The converter script SHA256 is
`e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`.
No incompatibility or controlled-upgrade requirement has been established.
If the exact source fails conversion or the CPU load probe, the builder stops
and requires a documented smallest pinned toolchain upgrade.

Once trusted source metadata is available, the existing BF16 recipe/build path
verifies every source file, source architecture/dtype and shard index, checks
the pinned clean toolchain, converts with a fixed argument list and performs a
CPU-only zero-generation-token load probe. Private durable provenance records
all inputs, tool/converter revisions, exact command and output name/size/SHA256.
A completed-build journal supports crash recovery on either side of output
publication without another conversion. Stale or conflicting output/provenance
fails closed. A verified future bootstrap requires no download credentials.

Downloads use a separate pinned `huggingface-hub==2.2.0` / `hf-xet==1.7.0`
environment, support `HF_XET_HIGH_PERFORMANCE=1`, and reuse resumable staging.
Final same-name files are trusted only after exact size/hash verification.
Wrong files are not silently repaired. All path components reject symlinks;
regular-file checks reject FIFOs, and publication cannot overwrite a competing
destination. Receipts distinguish verified files from mere presence. Config and
Diagnostics remain read-only and never download, build or perform Vast control.

## LoRAs and ephemeral credentials

[lora_registry.json](../h3/config/lora_registry.json) holds the seven existing
ordinary LoRAs and separate Bunny bridge metadata. Sixteen dynamic slots
accept registered IDs; unused slots are disabled with `None` and zero strength.
Browser requests cannot supply paths, model repositories, URLs or loader fields.
Unknown IDs, path-like names, duplicates and invalid strengths fail closed.

H3 keeps intended HMBreasts 1.0, MysticXXX 0.6 and movement 0.5 defaults; 10Eros
ordinary LoRAs and Bunny default off. Full-model GPU compatibility remains pending
for every entry. HMBreasts, HMNSFW, Turbo8 and Bunny have trusted source metadata.
MysticXXX, movement, MPOV and Combat are explicitly external/user-provided with
verification pending. Their explicit local enrollment command records the exact
installed size/hash without fabricating upstream provenance. An enabled external
LoRA must be enrolled and verified before submission. Bunny's pinned node prefers
a bundled copy, so dispatch/preflight verify all known bundled copies as well as
the persistent canonical file to prevent a stale shadow weight being selected.

`HF_TOKEN` exists only in the provisioning environment and the scoped download
child. It is removed before service launch and never stored in runtime.env,
queue/controller state, manifests, receipts, conversion provenance or reports.
Conversion children receive an allowlist of local runtime variables, excluding
provider/panel/Vast credentials. SDK exceptions and Python/native output are
suppressed; generic failures do not echo secret values or request URLs. Sentinel
tests inspect runtime.env, logs, config/Diagnostics, state and generated evidence.

## Original migration CPU validation

Evidence for the original PR #5 head `b19b54adb28fa5e851ce35d09ef1351940fa1777`.
Finalization evidence and current counts are in the linked report above.

| Command/check | Result |
| --- | --- |
| `make setup` | PASS; pinned development dependencies and pip check |
| `make test` | PASS; **439 tests + 16 HTTP smoke checks** |
| `make audit` | PASS; **439 tests**, all seven original defects still reproduced by historical probes |
| Focused migration checks | PASS; **81 tests** |
| Final failures / errors / skips | **0 / 0 / 0** in both full suites and the focused run |
| Package checks | 66 original files unchanged; 29 JSON documents; 17 shell scripts; 1 inline JavaScript syntax check |
| Historical directory inventory | All 72 files unchanged, with no additions/removals |
| Local panel | Authenticated HTML/config, two profile labels, registry, Diagnostics and logs verified; owned preview stopped |

The full audit was rerun with the local preview stopped: an earlier attempt
overlapped that preview on port 7860 and four existing unused-port checks failed.
The isolated final run passed unchanged assertions and timeouts. No production
service-control changes were made to hide the environment collision.

Tested source identities (the final documentation commit contains these trees):

- `h3/` Git tree: `3c5edc802e06e25ec6358fa32e22fe04419ab31e`.
- `tests/` Git tree: `6cfcd1c3a61dbaf6848e39eb54feb5e61e94975e`.

The focused production fixtures include exact attachment integrity, all connected instruction
sources, both profile/checkpoint/writer routes, durable analysis/epoch reuse,
admission races, one-heavy-render reservation, review priority, registry/slots,
checksum/provenance failures, conversion journal recovery, external enrollment,
ephemeral secrets, read-only Diagnostics and actual frontend JavaScript behavior.

A long twenty-second six-section fixture round-trips through history capture and
both render profiles without truncation or loss of its final 15–20 second interval.
Converter tests verify the requested duration reaches both unchanged v20 locks.
These are transport/graph tests; actual new-writer schema reliability, duration
coverage and truncation rates remain unrun model benchmarks.

Strict offline production preflight and provisioning currently return **NOT
READY** for incomplete 9B provenance. Tests explicitly assert this fail-closed
outcome. It must not be relabeled production readiness or a GPU acceptance pass.

## Final self-review

| Area | Result and limit |
| --- | --- |
| Duplicate paid submission | The accepted single POST boundary, durable arm/UUID and uncertain-POST recovery remain; new admission checks are before arming. No real paid submission occurred. |
| Durable profile persistence | Every new batch/job freezes profile, core routing, LoRA/bridge selection and duration before RAM publication. |
| Recovery profile drift | Recovery uses persisted identity; changed routing or inconsistent batch/job identity fences loading/dispatch. Lost-ACK replay preserves the original browser request. |
| Cache/restart races | Service epoch checked around graph preparation; stale completions cannot establish current residency; exact durable text survives restart. |
| One heavy render | Preparation/submission/running/recovery/cancellation reservations block another heavy render before awaiting preparation. |
| Overlap admission | Cold H3 deferred; warm writer and balanced 10Eros require sampling plus fresh measured headroom. No global GPU mutex. |
| H3 VRAM assumptions | Both conditioning dependencies precede encoder release and late model load; actual peak memory and unload timing remain live-only. |
| Simultaneous heavy models | One Render Worker, render release boundaries and exclusive conditioning window; no second instance or parallel heavy profile worker. |
| LoRA path safety | Canonical registry IDs only; filenames trusted and validated, traversal/unknown IDs rejected, symlink/FIFO reads refused. |
| Checksum/provenance bypass | Strict frozen manifest and exact size/hash required; diagnostics distinguish presence, verification and GPU status. Pending source metadata blocks production. |
| Partial downloads | Resume staging never becomes a model until verified; no-overwrite publication and wrong-file fail-closed behavior tested. |
| Generated stale/source mismatch | Recipe fingerprint, all input hashes, pinned toolchain and full output provenance required; completed-build crash journal tested. |
| API-token disclosure | No token arguments or persisted SDK login; Python/native SDK output and exceptions cannot reach reports. Fake sentinel tests cover failure and reuse. |
| runtime.env leakage | Provider keys excluded by allowlist and explicitly unset before runtime service launch. |
| Logs/Diagnostics leakage | Sentinel tests inspect both; Diagnostics performs only bounded read-only probes and file-health reads. |
| Legacy production fallback | Exactly two normal profiles; legacy manifest/workflows available only through explicit diagnostics, never automatic fallback. |
| v21/v22 contamination | Canonical bytes and connected instruction snapshots checked for both templates; no prompt rewriting or later-rule imports. |
| v20 duration | Unchanged duration locks, requested runtime dependency and full six-section transport fixture; actual writer output benchmark pending. |
| Historical integrity | All 72 files under migration/ and archive/ retain their initial bytes; the 66 received package originals also retain their expected checksums. |
| Accepted lifecycle/security | Existing idempotency, watchdog, cancellation, ownership, persistence fencing, review/drain, playback, STOP/DESTROY and cost regressions retained. |

## Pending before and during the consolidated GPU session

Before renting: obtain authoritative complete 9B source identities, freeze its
generated recipe, perform and verify the real BF16 conversion/CPU load, enroll
the user-provided enabled LoRAs, and pass strict production preflight. The draft
migration is not yet ready for the single consolidated GPU session.

The subsequent controlled session must verify real H3 BF16 loading and black
frames, actual encoder release/peak VRAM, 10Eros Full loading, H3 → 10Eros → H3
switching, every intended LoRA and optimal strength, actual Prompt/Render overlap,
Step 0–2 cache under GPU pressure, writer schema/duration/truncation, render speed,
actual MP4 playback, OOM/restart recovery, volume retention/reattachment, real
Vast lifecycle/STOP/DESTROY and billing/cost behavior. Full/BF16 H3 has historical
black-frame reports on some ComfyUI/Blackwell combinations; CPU success does not
establish a fix. No real Vast rental/mutation, GPU inference or H3/10Eros rendering
was executed in this task.
