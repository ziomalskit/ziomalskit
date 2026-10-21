# AJ Cloud: dual LLM profiles — 13 October 2026

**Historical report for `cf6591789af1665e1bda48e830a2617047fb9a3f`.**
The selected Mistral fine-tune/projector and Step 4 budgets below are superseded
by the [14 October follow-up in the existing Draft PR #8](DUAL_LLM_PROFILES_2026-10-14.md).
The old source table remains evidence of that earlier head, not current routing.

Draft implementation on PR #6 head `84af26e26c0ec85a40efe712e5e32506c5a9af3f`,
stacked on `feat/workflow-adapters-render-controls` (PR #6), which depends on
`feat/v20-full-production-models` (PR #5). No merge, Vast operation, GPU rental,
model-weight download, real inference or paid-provider operation was performed.

## Result and production gates

| Stage | H3 Full | 10Eros Full |
| --- | --- | --- |
| 0A | JoyCaption F16, unchanged | shared with H3 |
| 0B | Qwen3-VL Caption V4.5 F16, unchanged | shared with H3 |
| 0C | Gemma Philadelphia F16, unchanged | shared with H3 |
| 1/2 | Mistral Small 3.2 24B Instruct 2506 Heretic v1.2-2 Q4_K_M | same source family, Q6_K |
| 3/4 | Qwen3-14B Heretic Q4_K_M | Qwen3.5-27B Heretic Q5_K_M |

Both Mistral quantizations preserve v20's vision inputs and use their matching
**GGUF F16 mmproj**. Gemma's projector is not reused for Mistral. The Qwen writers
receive text and use `mmproj=none`; no Qwen vision projector is needed for Step 3/4.
No additional or replacement instructions are introduced.

**Production remains blocked.** Qwen3-14B immutable source metadata is pending,
and the existing required DavidAU pending artifact remains as the retirement
gate. Draft routing to Qwen3.5-27B is not evidence of a completed replacement
load/generation acceptance. The DavidAU gate can be removed only in a reviewed
follow-up after the replacement passes exact-artifact load, minimal generation,
reasoning/final-channel, native EOS, context and timeline probes. Provisioning,
strict production preflight and Autoprompt retain their fail-closed manifest gates.
Manual-final retains PR #6's independent, verified render-artifact scope.

## Primary-source verification

Immutable Hugging Face upload-commit diffs contain the Git LFS pointer's full
SHA256 and byte count. These are metadata evidence, **not downloaded-file or
GPU load evidence**. No revisions, hashes or byte counts were synthesized.
The shell executor allows GitHub/package hosts but does not allow Hugging Face;
public Hugging Face metadata was inspected using the web connector. Raw immutable
GGUF pointer URLs could not be fetched there. For the four pinned artifacts below,
the immutable upload-commit diff itself supplied the exact pointer.

| Artifact | Immutable upload commit | Exact bytes | SHA256 |
| --- | --- | ---: | --- |
| Mistral Q4_K_M | [`04c6f30963effffcc99c91ca35cc21386c924fde`](https://huggingface.co/mradermacher/Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2-GGUF/commit/04c6f30963effffcc99c91ca35cc21386c924fde) | 14,333,923,456 | `18d53514719aa2e19f9a43077f9e75d96a28af704322dc9d40fbfe26992c2f7d` |
| Mistral Q6_K | [`b4de58b54ccb88234fdd8eba9c84491030ca1c6a`](https://huggingface.co/mradermacher/Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2-GGUF/commit/b4de58b54ccb88234fdd8eba9c84491030ca1c6a) | 19,345,952,896 | `73457595863bff09c3308ced004760e0ae47151673291ad9cb37816ab8a801f1` |
| Mistral mmproj F16 | [`6f54b7e5a5044bfcda44c877b213bee5b7c5792a`](https://huggingface.co/mradermacher/Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2-GGUF/commit/6f54b7e5a5044bfcda44c877b213bee5b7c5792a) | 878,053,952 | `a5a617cfe08b8d10f9a02a9efedba1a767711685ed4ab8a2c50644e710ed7f0c` |
| Qwen3.5-27B Q5_K_M | [`81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5`](https://huggingface.co/mradermacher/Qwen3.5-27B-heretic-GGUF/commit/81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5) | 19,399,608,384 | `66787dfbf5462ac355457efaf24e503d244f1083945243ba029080a8e6276322` |

The filenames in `models_manifest.json` are the actual published basenames:
`Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.{Q4_K_M,Q6_K}.gguf`,
`Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.mmproj-f16.gguf`, and
`Qwen3.5-27B-heretic.Q5_K_M.gguf`.

[Qwen3-14B repository and variant](https://huggingface.co/mradermacher/Qwen3-14B-heretic-GGUF/blob/main/Qwen3-14B-heretic.Q4_K_M.gguf)
and its `main` raw pointer were observed. The pointer reports 9,001,753,952 bytes
and SHA256 `4d529e77f0f1755de42e6f81d7c62309d5f39a4968049d817862d8a9263c317f`;
the upload link reports commit `88cac1a5c19ac015402ec94884fabad4e3cfeaf2`.
The immutable commit diff, immutable raw pointer and revision API could not be
read independently (timeout/internal access error). These values are stored only
as `observed_*` on a required **pending** entry, with no authoritative `revision`,
`size_bytes` or `sha256`. They do not authorize download or substitution.

All existing Step 0 artifacts, renderer/encoder/VAE/LoRA source identities and
checks remain intact. The prior H3 4B artifact is retained for diagnostics with
`required=false`. DavidAU's observed revision and converter pin remain intact;
there is no fake BF16 recipe, alternate quantization, local checksum enrollment
or provenance bypass.

## Pinned runtime assessment

The pin remains llama.cpp `60eeeb6082c1126bb8bc72902c83123cd056811b` / `b10472`,
and LLM node `65983de33681816f856db7e16767da906084c688`. Source inspection at those
exact commits confirms family and quantization implementations:

- [Mistral conversion](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/conversion/mistral.py),
  [Mistral3 conversion](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/conversion/mistral3.py)
  and [Pixtral vision projector](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/tools/mtmd/models/pixtral.cpp).
  Older Mistral exports may use `llama`; both `llama` and `mistral3` are supported.
- [Qwen conversion registrations](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/conversion/qwen.py)
  include Qwen3 and Qwen3.5, with native [Qwen3](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/src/models/qwen3.cpp)
  and [Qwen3.5](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/src/models/qwen35.cpp) implementations.
- [K-quant implementations](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/ggml/src/ggml-quants.c)
  include `dequantize_row_q4_K`, `dequantize_row_q5_K`, `dequantize_row_q6_K`.
- [Pinned native LLM invocation](https://github.com/KingManiya/ComfyUI-LLM-text-processor/blob/65983de33681816f856db7e16767da906084c688/nodes.py)
  accepts image+mmproj and reaps its per-call CLI process; AJ delegates to it.

**Family support is confirmed in source; exact GGUF header/tensor/chat-template
compatibility and actual model load/generation remain pending.** No real model
weights were downloaded or loaded. Acceptance must verify `general.architecture`,
tokenizer/template, quantization, projector pairing and limits on the exact hashed
files. This report does not turn generic family support into an exact-artifact PASS.

Step 0–3 retain `llama-cli`. Step 4 retains PR #5's private one-shot sibling
`llama-server` for machine-readable completion from the same verified pin. There
is no blanket server migration or persistent server. Step 3 thinking OFF,
8192 generation/24576 context; Step 4 ON, reasoning budget 4096, total 12352,
context 29184, 8192 final + 64 framing remain frozen. Native EOS/no truncation,
separate final/reasoning channels, six sections and timeline guards are unchanged.
A persistent server/KV reuse benchmark is a separate future optimization.

## Cache and memory behavior

The durable record carries both `step0_key` and the Step 1/2 `key`. Step 0 binds
its actual six model/projector source identities, pinned runtime, canonical
instruction baseline and assets. Step 1/2 additionally bind their actual model
and projector identities (source revision/path/hash, hence quantization), analysis
seed and instructions. Renderer/writer/LoRAs/candidate seed are excluded.

Matching Step 0 can be recovered from other persisted batches/profile records;
Q4_K_M analysis can never masquerade as Q6_K analysis. Partial Step 0 prunes only
Step 0 models, leaving Step 1/2 and Step 3/4. Full analysis prunes all Step 0–2
models, leaving exactly Step 3/4 for candidates #2–10. Missing identities or old
unsplit records cause fresh analysis, never an automatic migration. A terminal
writer failure can retain independently completed, graph-bound analysis. Service
restart clears ephemeral residency while preserving durable text.

Controller admission gives ready renders priority, reserves one heavy renderer,
requires sampling phase and telemetry no older than three seconds for writer
overlap, and freezes thresholds per job. Cold Step 0–2 waits during sampling for
both profiles. Native gates also check free VRAM immediately before every LLM
invocation. Unknown/unavailable GPU measurements pause even in an idle window.

Native phase leases coordinate both ComfyUI processes through the same output
volume's `.aj-gpu` directory. The render worker publishes a closed phase **before**
waiting for a resident LLM call to finish/reap. Only then may conditioning,
decoding or an unknown GPU node execute. Subsequent prompt model stages wait
outside their native invocation, preserving inputs and cancellation. A fresh
sampling heartbeat permits measured Step 3/4 only after actual sampler progress.
The initial sampler executing event remains closed because ComfyUI can load the
transformer onto CUDA inside that node; pre-load free memory is not an overlap
budget. Wrong/missing progress node identity never opens the window. Stale/missing/malformed state, owner
mismatch, missing renderer ownership, zombie owners, unknown phases and recovery close the window. OS locks also refuse a
second renderer or concurrent LLM. Configured render/prompt ports are preserved.
STOP checks remain active during waits; durable
journal fsync occurs before queue ownership release and reopening idle admission.

The native CLI/server child inherits the inference lease descriptor. Even if
its Prompt Worker parent dies, the renderer cannot obtain the decoding lease
until the actual native process exits. A CPU process test releases the parent
lease while the child remains alive and proves that decoding stays blocked.
Missing worker queue ownership fails closed before any model invocation.

This is cooperative pausing between model invocations, **not CUDA process
suspension**. An already-running LLM is allowed to settle within its native time
limit; decoding waits for its reap. A failed or unsettled native result keeps
prompt admission in recovery. It does not authorize blind cancellation, duplicate
submission or a worker restart.

| Configurable acceptance threshold | H3 Full | 10Eros Full |
| --- | ---: | ---: |
| Analysis headroom (MiB) | 49152 | 57344 |
| Writer headroom (MiB) | 24576 | 40960 |

These are conservative **unvalidated acceptance inputs**, not measured model
costs. They budget weights, context/KV, workspace and reserve. File sizes are only
used for disk planning/integrity. Real memory usage depends on model architecture,
context, offload, backend and renderer phase. Hardware acceptance must calibrate
both thresholds and render-slowdown limits. Reinstall the full owned native
package and restart both workers; filesystem identity cannot attest already
cached in-memory Python, as in PR #6.

## Preserved behavior and CPU verification

Canonical v20 remains 3,084,071 bytes with SHA256
`276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
All workflow attachments, migration and archive files remain byte-identical.
Instructions, duration synchronization (20 s → 481 frames → 20.040 s), six
references/audio, LoRA and adapter validation, manual-final, idempotency,
uncertain submission/recovery and STOP/DESTROY contracts remain intact.

The received 22 PR #5 graph digests are preserved. Comparison explicitly
normalizes only the requested exact model/projector substitutions and per-LLM
gate class/inputs, then asserts every other graph field/edge against the old
hashes. Separate tests assert the exact new routes, projector/vision inputs,
immutable instruction snapshot and frozen gate controls. No canonical or golden
checksum was changed to conceal a modification.

29 new tests cover both profiles, quantization/source/instruction isolation,
shared Step 0, candidates #2–10, persisted/partial recovery, writer failure and
replacement, graph pruning/tampering, render priority, thresholds, unknown/stale
GPU state, symlinks/hardlinks, cancellation and real cross-process phase leases.
Existing CPU fixtures now use the same production runtime-guard adapter as real
submissions. CI additionally runs `make audit` after setup/test.

| Check | Evidence |
| --- | --- |
| `make setup` | PASS; pinned dependencies and pip check |
| Final `make test` | **844 PASS + 16 HTTP smoke checks**, no failures/errors/skips |
| Full `make audit` | **844 PASS**, all seven original baseline defects reproduced; the final rerun is recorded in the Draft PR |
| Native phase gate suite | **16 PASS**, including real process leases, inherited child lease, startup/progress/decoding and configured ports |
| Pinned Step 3 CLI delegation probe | PASS: unchanged command/channel parser, inherited lease, context reset |
| Local panel | `make panel`, authenticated HTML/configuration check and owned stop: PASS |
| Historical/attachment integrity | All migration/archive/workflow files unchanged; canonical v20 size/SHA256 unchanged |
| CI | Final run URLs, exact head and completion results are recorded in the Draft PR description |

The final verification freezes **133 functional source/fixture files** and checks
that their bytes remain unchanged through the checks. The cloud environment
restart interrupted one earlier run; that run was not counted as PASS. Development
runs exposed an outdated history fixture (now uses the production runtime guards)
and one manual-prefetch expectation (now asserts render priority and warm writer
overlap). No timeout, assertion or production lifecycle safeguard was relaxed.
Final full CPU checks run sequentially. A local subreaper collects only its own
already-exited descendants; it sends no signals and changes no test harness.
GitHub Actions runs the repository's normal `make setup`, `make test`, `make audit`.

This document records implementation/source evidence; the Draft PR description
records the final check outcomes for its exact immutable head. GPU/model loading,
real rendering and provider lifecycle acceptance remain unverified.

## Later acceptance plan (not executed)

1. Confirm Qwen3-14B's immutable pointer; promote only actual evidence, retaining
   exact hashes and revisions. Verify every downloaded file against its source.
2. On an explicitly authorized retained-volume/GPU session, validate the pinned
   binaries/build receipts and exact GGUF architecture, tensors, quantization,
   chat template/EOS/context and Mistral F16 projector. Verify minimal vision
   generation for both analysis quantizations and text generation for both writers.
3. Run separate prompt-only six-reference tests for both profiles with/without
   audio, zero Render POST. Verify reasoning OFF/ON, complete final channel,
   natural EOS, no context/length truncation and valid full-duration timelines.
4. Prove shared Step 0, Q4/Q6 Step 1/2 isolation, candidates #2–10 reuse, writer
   swap, saved-analysis recovery after controller/worker restart and writer failure.
5. After replacement evidence passes, remove the DavidAU required pending gate
   in a reviewed follow-up. Do not remove it merely because a new route exists.
6. Run one controlled render per profile, then render-only versus writer-overlap
   measurements. Record peak/free VRAM, actual KV/workspace, startup allocations,
   wall time and useful writer throughput. Calibrate headroom/reserve above and
   below the boundaries with no OOM, restart loop or unacceptable render slowdown.
7. Force sampling→decoding and unknown/recovery transitions with an active writer:
   prove the closed phase prevents new loads, waits for native reap before decode,
   resumes correctly and never duplicates POST. Test missing/stale telemetry and
   controller/worker restart, cancellation, draining and all lifecycle barriers.
8. Apply the remaining render/MP4/retention/STOP/DESTROY acceptance in
   `GPU_ACCEPTANCE.md` only under separate explicit authorization. Record exact
   checkout/model/binary identities, GPU/driver/CUDA and output evidence.

Until those gates pass, status remains **Draft; model/GPU acceptance pending**.
