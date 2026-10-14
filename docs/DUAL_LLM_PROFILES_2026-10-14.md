# AJ Cloud: PR #8 completion — 14 October 2026

**Historical report for `6185b0e5c63ffb6b2f583502beb780b0a629f1be`.**
The Qwen3-14B metadata blocker and bootstrap circular dependency below are
superseded by the [native model acceptance follow-up](MODEL_ACCEPTANCE_BOOTSTRAP_2026-10-14.md).
The metadata/validation below remain evidence of that earlier head.

This updates the existing Draft [PR #8](https://github.com/ziomalskit/ziomalskit/pull/8),
branch `feat/dual-llm-profiles`, starting at
`cf6591789af1665e1bda48e830a2617047fb9a3f`. At the initial and pre-push checks,
PR #6 remains at `84af26e26c0ec85a40efe712e5e32506c5a9af3f`, based on PR #5 at
`c6f033ab41a4456fd55558e652a563d8c31418d1`; neither dependency was merged or rewritten.
The [13 October report](DUAL_LLM_PROFILES_2026-10-13.md) describes the previous
head. Its Mistral selection/projector and Step 4 defaults are superseded here.

## Changes and final stack

| Stage | H3 Full | 10Eros Full |
| --- | --- | --- |
| 0A | JoyCaption F16, unchanged | shared with H3 |
| 0B | Qwen3-VL Caption V4.5 F16, unchanged | shared with H3 |
| 0C | Gemma Philadelphia F16, unchanged | shared with H3 |
| 1/2 | llmfan46 Mistral Small 3.2 24B Instruct 2506 Ultra Uncensored Heretic Q4_K_M | same fine-tune, Q6_K |
| 1/2 projector | published matching Mistral BF16 GGUF | same exact artifact |
| 3/4 | Qwen3-14B Heretic Q4_K_M | Qwen3.5-27B Heretic Q5_K_M |

`production_profiles.json` and the three Mistral manifest entries now select
the requested llmfan46 fine-tune, exact published filenames and its projector.
Heretic-v1.2-2 and its F16 projector are no longer provisioned or routed in
production. Existing correct unrelated artifacts, required DavidAU gate, prior
diagnostic 4B artifact and both writer selections are retained. There is no
automatic alternative model, renaming of old weights or deletion of local files.

Step 4 defaults for **new jobs** are:

| Setting | H3 Full | 10Eros Full |
| --- | ---: | ---: |
| reasoning | on | on |
| reasoning_budget | 6144 | 8192 |
| final_answer_tokens | 8192 | 8192 |
| framing_tokens | 64 | 64 |
| max_tokens | 14400 | 16448 |
| ctx_size | 32768 | 36864 |

Step 3 stays reasoning **off**, budget 0, max 8192, context 24576, final 8192,
framing 0. Native `--reasoning-format deepseek --reasoning-budget` is preserved.
The existing stage validator accepts these policies while reserving at least
16,384 input tokens; it still rejects starvation, invalid types and excessive
budgets. The native compiler now independently enforces the same context/input
reserve and generation/context bounds before launch, including direct node use.
Its successful frozen 4096/12352/29184 policy remains supported.

No unfinished thinking or empty/missing final channel may publish a prompt.
Natural EOS, successful native finish, explicit no-truncation receipt, separated
channels, six complete sections and temporal guards remain mandatory. CPU tests
exercise both new command/context/reasoning settings and JSON token limits,
empty/reasoning-only/incomplete finals and pre-launch budget refusal. Step 0–3
keep `llama-cli`; Step 4 keeps the existing private same-pin one-shot server
receipt. No persistent server or blanket CLI migration was introduced.

## Verified primary-source metadata

The author's [source model](https://huggingface.co/llmfan46/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic)
links the [GGUF repository](https://huggingface.co/llmfan46/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-GGUF/tree/main).
Its immutable [upload commit `701e8e336a199135e36aef8ed15a0be2954753f0`](https://huggingface.co/llmfan46/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-GGUF/commit/701e8e336a199135e36aef8ed15a0be2954753f0)
contains all three exact Git LFS pointers and the README identifying its BF16
vision projector for every listed quantization. That explicit pairing, rather
than similarity to the previous model, is the projector source evidence.

Repository for all three: `llmfan46/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-GGUF`.
Revision for all three: `701e8e336a199135e36aef8ed15a0be2954753f0`.
Repository paths below are also the basenames under the local `LLM/` destination.

| Artifact / exact repository path | Exact bytes | SHA256 |
| --- | ---: | --- |
| `Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-Q4_K_M.gguf` | 14,333,922,464 | `5b0e27f5e3c2e5ef1809115c26efe247d72b46c218025acf1673ccd0e6045c20` |
| `Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-Q6_K.gguf` | 19,345,951,904 | `125805cfbf0af1d8ee1070e49dda670d16b7d2ecc706f9ee1ddbe3fc5de5e996` |
| `Mistral-Small-3.2-24B-Instruct-2506-mmproj-BF16.gguf` | 887,646,304 | `0a3a2dbf3b97d38d839a036b2abe0cac0f28bc66cf91ce0f43cea8affa68f937` |

The 10Eros writer remains `mradermacher/Qwen3.5-27B-heretic-GGUF`, path
`Qwen3.5-27B-heretic.Q5_K_M.gguf`, immutable revision
[`81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5`](https://huggingface.co/mradermacher/Qwen3.5-27B-heretic-GGUF/commit/81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5),
19,399,608,384 bytes, SHA256
`66787dfbf5462ac355457efaf24e503d244f1083945243ba029080a8e6276322`.
Its immutable upload pointer was rechecked and agrees with the unchanged manifest.

These are verified **source metadata**, not hashes of downloaded weights or a
model loading PASS. No model weights were downloaded. Hugging Face metadata was
read through the web connector; the execution network policy does not allow
direct Hugging Face access. No network-policy bypass was used.

### Required pending entries

**Qwen3-14B remains pending.** The [published Q4_K_M blob](https://huggingface.co/mradermacher/Qwen3-14B-heretic-GGUF/blob/main/Qwen3-14B-heretic.Q4_K_M.gguf)
and its mutable `main` raw pointer again report 9,001,753,952 bytes and SHA256
`4d529e77f0f1755de42e6f81d7c62309d5f39a4968049d817862d8a9263c317f`.
The previously observed full upload revision is
`88cac1a5c19ac015402ec94884fabad4e3cfeaf2`.
The upload commit, raw and blob at that full revision, and the correct HF
revision API all still returned access/internal errors through the connector.
Thus no independently fetched immutable pointer binds that revision to the
size/hash. Only `observed_*` fields remain; no authoritative revision, SHA256 or
size is enrolled. A new reason documents the 14 October recheck.

**DavidAU retirement remains pending.** Its required artifact and observed
revision `72f2d803ae63499fc463607d997fe48abd38f093`, converter revision
`60eeeb6082c1126bb8bc72902c83123cd056811b` and converter SHA256
`e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`
are unchanged. This gate requires real replacement load/minimal generation,
reasoning/final/EOS/context/timeline acceptance before retirement. Configuring
Qwen3.5-27B does not authorize gate removal or a fake BF16 recipe.

Strict provisioning, production preflight and Autoprompt stay fail-closed.
Manual Final Prompt keeps its existing independently verified render scope.

## Runtime, cache, jobs and render priority

Pins remain llama.cpp **`60eeeb6082c1126bb8bc72902c83123cd056811b` / b10472**,
LLM node **`65983de33681816f856db7e16767da906084c688`**, and ComfyUI
**`6b747c0428c343e1417219641db93a4fb7cb69ae`**. Small upstream source files were
re-read at the exact llama.cpp revision, with no model download/build/inference.
The [Mistral/Mistral3 converter](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/conversion/mistral3.py),
[Pixtral graph](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/tools/mtmd/models/pixtral.cpp),
[GGML BF16 type](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/ggml/src/ggml.c),
[CUDA BF16 matrix paths](https://github.com/ggml-org/llama.cpp/blob/60eeeb6082c1126bb8bc72902c83123cd056811b/ggml/src/ggml-cuda/ggml-cuda.cu),
Qwen3/Qwen3.5 models and native reasoning flags are present. Previous source
copies matched the authoritative pin. Exact GGUF headers, tensors, templates,
projector image inference and real binary/model compatibility remain pending.

The functioning PR #8 architecture is preserved: shared Step 0 and cache,
Step 1/2 keys bound to exact source/revision/path/hash/size/projector/instructions,
durable reuse for candidates #2–10 and partial analysis recovery after writer
failure. A previous Mistral identity can supply only the unchanged Step 0, never
Step 1/2 under the new identity. Writer budgets do not invalidate analysis.

Existing jobs retain their saved model, stage, memory, render, LoRA and duration
parameters. CPU recovery tests distinguish default-only budget changes (old
budgets still execute verbatim) from model/provenance/native-package changes
(explicit reconciliation, preserved records, no silent replacement). Deploying
the native compiler change requires both workers to load the current verified
package; existing implementation provenance fences remain active.

Render priority, native GPU leases, phase/ownership/heartbeat checks, current
VRAM measurement, OOM admission protection and STOP/journal ordering are unchanged.
Conditioning, decoding, recovery and unknown state forbid new LLM loads. Step 0–2
waits during sampling. Step 3/4 requires a safe sampling window and fresh measured
headroom; the actual native child retains its lease until reap.

| Conservative headroom, MiB | H3 Full | 10Eros Full |
| --- | ---: | ---: |
| analysis | 49152 | 57344 |
| writer | 24576 | 40960 |

No threshold was lowered. GGUF bytes remain disk/integrity information, never
an estimate of full VRAM cost. Larger context/KV/workspace peaks require actual
GPU measurement before calibrating thresholds or claiming overlap performance.

Canonical v20 remains **3,084,071 bytes**, SHA256
**`276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`**.
Workflow/migration/archive bytes, all 22 received PR #5 graph digests, instructions,
Duration Sync, six references/audio, LoRAs, render settings, Manual Final Prompt,
idempotency/recovery and STOP/DESTROY contracts are preserved. Parity comparison
normalizes only exact requested model/projector substitutions, known Step 4
budget triples and native gates. An adversarial test proves unrelated writer
changes and unknown budget/context settings still change the digest.

## CPU validation

| Check | Result |
| --- | --- |
| `make setup` | PASS; pinned dependencies and pip check |
| `make test` | **857 PASS + 16 HTTP checks**, 403.012 s for the regression suite |
| `make audit` | **857 PASS**, 401.462 s; all 7 historical defects reproduced |
| Affected focused suites | **132 + 1 PASS** |
| Failures / errors / skipped, final full runs | **0 / 0 / 0** |
| Local panel | `make panel`, authenticated HTML/configuration check, owned stop: PASS |
| `git diff --check` | PASS |
| Integrity | **244 tracked non-documentation source/fixture files** unchanged through both full runs; **78 workflow/migration/archive/parity files** unchanged from starting head |
| Full GitHub Actions | Exact final commit and run/results are recorded in the existing Draft PR #8 description |

Full checks ran sequentially. The local-only subreaper collects only its own
already-exited adopted descendants; no signals, timeout overrides, check
disabling or harness modification. GitHub Actions uses the normal repository
`make setup`, `make test`, `make audit` workflow. CPU PASS does not certify GPU
model loading, rendering, memory or provider lifecycle.

**13 new regressions** cover immutable Mistral/projector metadata, rejecting old/absent
projectors, both model/projector cache identities, old-source isolation,
writer-budget-independent saved analysis, previous-job/default recovery,
quarantine without record mutation, exact new native command/JSON settings,
reasoning/final/context boundary refusal, incomplete/empty finals and measured
GPU admission at unchanged thresholds. Previous runtime/lifecycle tests remain.

## Remaining gates and later GPU acceptance — not executed

1. Obtain an independently readable immutable pointer for Qwen3-14B; promote
   only confirmed facts. Preserve fail-closed provisioning until provenance
   requirements are met. No substitute writer is authorized.
2. Under a separately authorized GPU acceptance session, verify exact downloaded
   hashes, GGUF architecture/tensor/quantization/template/context/EOS metadata,
   same-pin binaries and BF16 projector load/image generation for both Mistrals.
3. Run minimal generation and controlled six-reference prompt-only tests for
   both writers, with/without audio and zero render POST. Verify Step 3 OFF,
   both larger Step 4 budgets, finished reasoning, nonempty six-section final,
   natural EOS/no truncation and exact Duration Sync/timeline.
4. Retire DavidAU's required gate only after real replacement evidence passes,
   in an explicitly reviewed follow-up; a route or metadata update is insufficient.
5. Verify candidates #2–10 reuse, profile switches, saved analysis, writer
   failures and worker/controller recovery against actual model execution.
6. Measure idle and sampling memory at contexts 32768/36864, including model,
   BF16 projector, KV cache, CUDA buffers and workspaces. Keep conservative
   thresholds pending measurements. Compare render-only speed with overlap,
   test threshold boundaries, sampling-to-decoding/recovery/unknown transitions,
   native child reap, stale telemetry and OOM/restart recovery.
7. Complete single Full renders, MP4 quality/duration, six references/audio,
   LoRAs, retained storage and live STOP/DESTROY gates in `GPU_ACCEPTANCE.md`
   only under separate explicit authorization.

No new model choice or user decision is needed for this CPU update. The blocked
immutable source and real acceptance evidence remain required. Nothing was
merged; no Vast action, GPU rental, paid operation, large model-weight download
or costly inference was performed. Status: **Draft; model/GPU acceptance pending**.
