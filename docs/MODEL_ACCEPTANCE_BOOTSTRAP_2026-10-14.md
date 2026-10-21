# Native model acceptance bootstrap — 14 October 2026

Historyczny raport wcześniejszego HEAD. Aktualna instalacja:
[Personal First Run](PERSONAL_FIRST_RUN.md). Nieużywany DavidAU9B jest usunięty,
retirement nie jest wymagany. Wagi mają pełne source/revision/size/SHA, GPU nadal
pending; ephemeral i jeden store zastępują dawny plan retencji/dwóch store.


Follow-up in existing Draft PR #8, starting at
`6185b0e5c63ffb6b2f583502beb780b0a629f1be`. PR #6/#5 bases remain unchanged.
No GPU rental, real model download/inference, Vast operation or merge occurred.

## Change inventory

This follow-up changes 15 files:

| Files | Result |
| --- | --- |
| `h3/config/models_manifest.json` | Promote the immutable Qwen3-14B source identity; retain the entire DavidAU pending entry. |
| `h3/config/model_acceptance.json` | Lock exactly 11 verified sources, both profile routes/stages, native pins and the retained production gate. |
| `h3/INSTALL_MODEL_ACCEPTANCE.sh`, `h3/scripts/provision_model_acceptance.sh` | Separate readonly plan/explicit install, private virtualenvs, pinned native build and downloads; no service/inference/lifecycle launch. |
| `h3/scripts/model_acceptance.py` | Fail-closed selection, isolated store, disk budget, safe pinned node publication, downloads and full file/runtime re-verification. |
| `h3/scripts/check_model_completion.py` | Offline natural-EOS/final/timeline checks using the existing native parser, without granting acceptance. |
| `tests/test_model_acceptance.py` | 22 safety regressions, including an actual shell bootstrap with tiny synthetic files and offline fake build/provider tools. |
| `tests/test_dual_llm_profiles.py`, `tests/test_production_controller.py` | Assert verified Qwen metadata and retained DavidAU gate; change the saved-job mutation fixture to the authoritative SHA256. |
| `README.md`, `h3/README.md`, `docs/ROADMAP.md`, `docs/GPU_ACCEPTANCE.md`, `docs/DUAL_LLM_PROFILES_2026-10-14.md`, this report | Current source status, separate bootstrap, subsequent manual GPU gates and historical report boundary. |

Normal production provisioning/preflight/service scripts, profile defaults,
controller/native implementations, all workflows and historical received sources
are unchanged.

## Immutable Qwen3-14B metadata resolved

The [immutable upload commit](https://huggingface.co/mradermacher/Qwen3-14B-heretic-GGUF/commit/88cac1a5c19ac015402ec94884fabad4e3cfeaf2)
is now independently accessible. Its added Git LFS pointer contains:

```text
version https://git-lfs.github.com/spec/v1
oid sha256:4d529e77f0f1755de42e6f81d7c62309d5f39a4968049d817862d8a9263c317f
size 9001753952
```

Repository: `mradermacher/Qwen3-14B-heretic-GGUF`.
Revision: `88cac1a5c19ac015402ec94884fabad4e3cfeaf2`.
Path: `Qwen3-14B-heretic.Q4_K_M.gguf`.
Destination: `LLM/Qwen3-14B-heretic.Q4_K_M.gguf`.
The observed values agree exactly; `h3-writer-replacement` is promoted from
`pending_artifacts` to `artifacts`, with authoritative revision/bytes/SHA256 and
immutable evidence. GPU validation remains **pending**. Nothing was downloaded.

There are now **22 verified-source artifact entries**. The sole required pending
entry remains **`10eros-writer`**, the DavidAU retirement gate. Its contents and
`required=true` are unchanged. No fake recipe, alternate writer or local enrollment.
The [previous report](DUAL_LLM_PROFILES_2026-10-14.md) records the earlier blocked
Qwen metadata state and earlier CPU validation at its exact head.

## Circular dependency and its separation

Normal `provision.sh` runs `provision_models.py --plan` before package/native
installation. The latter prints the disk plan and then calls strict
`validate_manifest(manifest)`, even for a plan. Required pending DavidAU therefore
blocks installing the runtime/models needed to perform its replacement's first
acceptance. Merely switching 10Eros's writer never supplies that missing evidence.

Normal production provisioning/preflight/submission gates are **unchanged**.
The new `INSTALL_MODEL_ACCEPTANCE.sh` is a separate entry point that installs
an isolated **native LLM acceptance runtime**, not the production ComfyUI/panel
deployment. This is sufficient to load/generate with the exact Mistral/projector
and Qwen artifacts before retiring DavidAU; full ComfyUI/render acceptance follows
only after a reviewed gate retirement. No production `allow_pending` option or
environment switch was added.

## Approved scope and boundaries

`config/model_acceptance.json` is a reviewed source lock for exactly **11** GGUFs:

- JoyCaption F16 and its projector;
- Qwen3-VL Caption V4.5 F16 and its projector;
- Gemma Philadelphia F16 and its projector;
- llmfan46 Mistral Q4_K_M, Q6_K and their matching BF16 projector;
- Qwen3-14B Heretic Q4_K_M and Qwen3.5-27B Heretic Q5_K_M.

Each approved entry binds ID, provider, repository, immutable revision, exact
repository/destination paths, byte count, SHA256, required ownership and source
verification status. Both profile routes, writer stages and native runtime pins
must agree with production configuration. Missing, pending, duplicate, mutated
or substituted selections stop before installation/download. New unknown pending
entries also fail. Only the **named, unchanged, required DavidAU retirement gate**
may remain pending in this dedicated acceptance scope; it is never selected,
downloaded or converted.

The root must be a separate absolute private directory. Production workspace
roots, model/input/output/build/PID directories, package containment, linked
ancestors, unrelated existing stores, linked markers/locks/model files and shared
production inodes are refused. A private marker binds the root and approval
identity. A changed approval requires a new store rather than relabeling old files.
Git node publication is staged, pinned and atomic with no overwrite; interrupted
clone/fetch can retry without replacing an existing checkout. Exact owned Linux
selector patching is preserved; unrelated source edits fail.

Environment/native build and HF downloads use separate virtualenvs under that
root; inherited production interpreters cannot receive packages. Native binaries
retain llama.cpp `60eeeb6082c1126bb8bc72902c83123cd056811b` / `b10472`, LLM node
`65983de33681816f856db7e16767da906084c688`, configured CUDA and Blackwell arch 120.
Existing pin/build/hash/version checks are reused. No comfy-cli global settings
or production deployment/state/runtime.env/onstart are written. The installer
requires preinstalled Git/CMake/CUDA devel tools and does not run apt/Vast installers.

Submissions, diagnostic submissions and terminal are forced OFF; lifecycle/service
commands are disabled. No panel, ComfyUI worker, render, inference, background
server or Vast action starts. The one-time HF token reaches only the dedicated
download subprocess and is never persisted or printed. Existing resumable
download staging, full rehash, no-overwrite publication and fail-closed size/hash
checks are reused. The private token variable's inherited Bash export attribute
is explicitly cleared; an adversarial full-bootstrap test reproduced its leak
before the fix and requires its absence from every child environment. Verified
reuse is offline.

## Commands — future installation requires a separately authorized environment

The default is a **read-only plan**; it creates no directory, venv or weight file:

```bash
H3_MODEL_ACCEPTANCE_ROOT=/persistent/aj-native-model-acceptance \
  bash h3/INSTALL_MODEL_ACCEPTANCE.sh --plan
```

Known model bytes: **113,395,969,792**. All-missing model/download staging minimum:
**132,795,578,176** bytes (largest download staging **19,399,608,384** bytes).
These are disk requirements, not VRAM estimates, and exclude native build/venv
space. The plan reports actual disk availability. Installation refuses insufficient
model/staging space before preparing the environment.

Only in a later explicitly authorized retained-volume/CUDA devel environment:

```bash
H3_MODEL_ACCEPTANCE_ROOT=/persistent/aj-native-model-acceptance \
  bash h3/INSTALL_MODEL_ACCEPTANCE.sh --install
```

This builds/verifies the pinned CLI and server and downloads only the 11 approved
models. It starts no services or inference. The final check rehashes every model,
checks nonempty GGUF framing and native source/binary/build/version provenance.
Tensor shapes, tokenizer/template/context, actual load and generation remain
unverified; `gpu_acceptance=not_run`, `production_gate_retired=false` are explicit.

Readonly re-verification and explicit download retry are separate:

```bash
ACCEPTANCE_ROOT=/persistent/aj-native-model-acceptance
"$ACCEPTANCE_ROOT/.h3-venv/bin/python" h3/scripts/model_acceptance.py \
  --root "$ACCEPTANCE_ROOT" --verify
"$ACCEPTANCE_ROOT/.aj-download-venv/bin/python" h3/scripts/model_acceptance.py \
  --root "$ACCEPTANCE_ROOT" --download
```

`--verify` makes no network request, download, conversion, model load or
generation. Native executables are invoked only with `--version`. `--download`
is an explicit large-weight operation for the future session; it is never run
against real models during this CPU task.

## Manual GPU probes and subsequent production acceptance — not executed

1. Record checkout, approval/model hashes, driver/CUDA/GPU and native build receipts.
   Confirm production workers are stopped/settled before isolated model probes;
   the preparation script does not stop existing services or coordinate overlap.
   Remove one-time HF/provider credentials from the manual probe environment.
2. Use the verified native binaries under
   `native/custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b10472/linux-x64-cuda`.
   Inspect real GGUF architecture, tensors, tokenizer/template, EOS and context
   limits. Run explicit foreground load/minimal text generation for both writers
   and image generation for both Mistral quantizations with the BF16 projector.
   CLI load/text output alone is not proof of natural Step 4 completion.
3. For Step 3 use the profile writer, reasoning OFF, budget 0, max 8192/context
   24576. For Step 4 use the same pinned sibling server only for a controlled
   foreground completion probe: `--reasoning on --reasoning-format deepseek`,
   budget/max/context **6144/14400/32768** for H3 and **8192/16448/36864** for
   10Eros, `--parallel 1 --no-context-shift`, localhost/private unused port.
   Capture one nonstreaming JSON response with `verbose=true` and the exact
   generation limit, then close that owned server. No persistent server migration.
4. Use unchanged canonical v20 instructions and a canonical Step 3 timeline
   derived from requested duration. Preserve six references/audio and capture
   separately the input/source identities, exact command, JSON request/response,
   reasoning/final, natural EOS/no-truncation, context/KV and elapsed/peak VRAM.
   Do not substitute test fixtures for evidence of real reference analysis.
5. Offline validation of a captured Step 4 response uses the **same production
   parser** and unchanged temporal contract, without a ComfyUI import/server:

   ```bash
   "$ACCEPTANCE_ROOT/.h3-venv/bin/python" h3/scripts/check_model_completion.py \
     --completion /private/evidence/step4-response.json \
     --timeline /private/evidence/canonical-step3.txt --duration 20
   ```

   It rejects missing/empty/reasoning-only final, length/context truncation,
   unnatural EOS, incomplete sections and invalid timelines. It prints no private
   trace/prompt. A valid local receipt is **not a GPU attestation** and cannot retire
   DavidAU: `gpu_acceptance=not_attested`, `production_gate_retired=false`.
6. Review actual Qwen3.5-27B replacement load/minimal generation and native
   reasoning/final/EOS/context/timeline evidence. Only then retire DavidAU's
   required pending gate in a reviewed follow-up; no script does so automatically.
   This bootstrap deliberately refuses a changed retirement gate/scope.
7. Freeze the complete production manifest and run the normal production
   installer/preflight/smoke with submissions initially OFF. Continue controlled
   full Step 0–4, cache/recovery and render/MP4/VRAM/overlap/retention/lifecycle
   gates in `GPU_ACCEPTANCE.md`, under separate authorization.

Production v20, both profiles/budgets, cache, VRAM thresholds, native leases,
Duration Sync, LoRAs/render settings, Manual Final Prompt, immutable persisted
job parameters, journal/recovery and STOP/DESTROY logic are unchanged. Promoting
writer provenance changes its persisted identity; incompatible jobs require
explicit reconciliation and are never silently rewritten. Completed outputs and
saved Step 0–2 analysis remain preserved.

## CPU validation

| Check | Final local result |
| --- | --- |
| `make setup` | PASS; pinned dependencies and pip check. |
| `make test` | **879 PASS** in 403.101 s, plus **16 HTTP checks**. |
| `make audit` | **879 PASS** in 404.885 s; all **7** original defects reproduced, baseline untouched. |
| Focused safety/profile suite | **41 PASS** in 7.491 s, including inherited-export token isolation. |
| Added coverage | **22** new regressions; no test removed or skipped. |
| Local panel | Owned start, authenticated HTML/configuration check, owned stop: PASS. |
| Diff/shell/integrity | `git diff --check` and Bash syntax PASS; same **250** functional source/fixture files through both full checks. |
| Protected existing files | All **259** tracked files outside the eight intentional existing-file changes are byte-identical to the starting head. This includes production scripts/profiles/controller/native code, workflows, migration and archive. |
| Canonical v20 | **3,084,071 bytes**, SHA256 `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`. |

Final local failures/errors/skips: **0/0/0**. The first development full run
found a stale controller fixture looking for Qwen3-14B in pending observations.
It now mutates the authoritative verified checksum and still requires saved-job
reconciliation; no production guard/assertion was relaxed. The later adversarial
export-attribute probe failed before its fix. Full test/audit above ran sequentially
on the final functional sources, with unchanged test timeouts and harness. The
local-only subreaper collects its own exited descendants without signals.

Exact final head and both complete CI run URLs/results are recorded in existing
Draft PR #8. GPU acceptance, real model downloads/loading/generation and
live/provider actions remain unperformed.
