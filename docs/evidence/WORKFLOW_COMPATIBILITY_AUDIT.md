# Workflow compatibility audit — 2026-10-06

Read-only review of the uploaded migration. No source workflows were changed, no model weights downloaded, no ComfyUI jobs submitted, and no GPU was rented. The source hierarchy in README_FIRST was followed: deployment RC5 is authoritative. This review does **not** establish that GPU generation works.

## Findings requiring correction before deployment

### 1. Pinned comfy-cli emits frontend-only rgthree nodes

**Confirmed source-level incompatibility.** Both workflows contain two `Label (rgthree)` nodes and seven `Fast Groups Bypasser (rgthree)` nodes. The actual PyPI `comfy-cli==1.21.0` converter excludes only `Note`, `MarkdownNote`, `PrimitiveNode`, `GetNode`, `SetNode`, and `Reroute` (`workflow_to_api.py:43`). It emits the other nine nodes into the API prompt (`:153–181`, `:682–702`), regardless of whether they are wired to output sinks.

The pinned rgthree commit `2c5342a8cb0eaecaabf61435a5f37dd594c510ba` has no Python mappings for Label or Fast Groups Bypasser. Label's JavaScript extends `RgthreeBaseVirtualNode`. ComfyUI `v0.38.0/execution.py:1130–1162` rejects any unregistered class before selecting output nodes. Its missing-node check applies even to unwired nodes. Consequently, these exported workflows are incompatible with the declared CLI/server combination unless conversion removes those virtual nodes or the environment provides explicit compatible shims. The preflight's six critical-class checks do not detect this; a real conversion validation should.

Evidence includes source-only conversion with an **empty synthetic catalog**, not a real `/object_info`: virtual-node emission is independent of catalog contents and was confirmed in source. All values produced without a real catalog must not be mistaken for a valid executable API prompt. The downloaded wheel's SHA256 matches authoritative PyPI release metadata: `659b1701801b3d905a8d866fe5eb28db2352bb266569ba6e4a36ff0c14da9e14`.

Recommended future coding task: produce separate API exports against the actual installed catalog, explicitly handle frontend-only classes, and add a conversion test that fails when any emitted class is absent from that catalog. Keep uploaded originals intact.

### 2. Render worker retains the complete autoprompter as independent outputs

**Confirmed graph design defect once conversion succeeds.** The prompt-only graph differs from RC5 master solely by removal of SaveVideo node `7331` and links `46735/46736`. Both have active LLM dashboard `2551`, final preview `5732`, five dashboard stage previews, full/tile/verifier/audit previews, and nine tile-image previews. Official ComfyUI `nodes_preview_any.py:16` declares `PreviewAny.OUTPUT_NODE = True`.

Setting approved prompt override `2632` changes the conditioning prompt selection; it does not remove these independent output sinks. Conversion retains all 16 `LLMTextProcessor` nodes as ancestors of preview outputs on **both** workers. `app/main.py:708–710` submits the whole graph with `comfy run`, with no partial output selection. On a new render-worker cache, the render request therefore executes the LLM pipeline again. Cached results may reduce later work but do not guarantee the intended separation. The renderer also retains default Step 1–4 seed controller values `1,2,3,4`, because `patch_llm_pipeline_seeds` is called only for the prompt template (`main.py:406–410`). Its extra LLM output is not necessarily the approved candidate.

This threatens the planned concurrent prompt/render memory budget and spends GPU time repeating analysis. Recommended future coding task: a render-only API graph with approved prompt as a direct input and no LLM/preview sinks; prove no LLM processors are reachable from renderer outputs. The prompt-only graph has no sampler ancestors of its preview outputs in the structural conversion, which supports its intended role after the converter issue is fixed.

### 3. Documented prompt-only acceptance starts automatic renders

`LIVE_ACCEPTANCE_TESTS.md` step 3 instructs creating a normal batch as a prompt-only acceptance test. `BatchRequest` has no dry-run/prompt-only option (`main.py:88–98`). `/api/batches` creates ten candidates, the first five auto-approved (`:1474–1543`); completed auto prompts transition to `render_queued_auto` and are dispatched by the renderer. The stop modes do not allow prompts to continue while reliably preventing those renders.

The included smoke script's `comfy run --print-prompt` is a real no-submit operation. For later GPU prompt-only acceptance, submit the prompt graph directly outside the ordinary batch API, or implement a tested prompt-only controller mode first. Do not follow the normal-batch acceptance instruction as written.

## Consistency and validated static relationships

- RC5 master: 159 top-level nodes / 117 links, 1309 nodes / 2095 links including definitions. Prompt-only: 158 / 115 at top level, 1308 / 2093 including definitions. No duplicate numeric node IDs across definitions were found.
- Picture slots `3554,4613,4624,4635,4646,4657`, audio slots `3944,4734,4749`, prompt inputs `2624/2632`, model `4595`, samplers `7334/7335`, latent upscale `6175`, and LoRAs `6164/6165` exist and match the RC5 map.
- Render sampler seed sockets trace to the actual rgthree sources `1854/6067`. LLM stage seeds trace through controller `4022` into nested primitive integer nodes, rather than using the local LLM widgets. The existing patcher updates both authoritative positional host values and mirrors.
- Native H3 INT8, Blackwell Qwen AWQ encoder, video INT8/audio FP32 VAEs, latent upscaler, JoyCaption + matching projector, Qwen verifier + matching projector, Gemma + matching projector, Violet-Lotus, three enabled LoRAs, and Bunny bridge adapter are represented in the model manifest.
- Disabled LoRAs, the muted native TextGenerate alternative, the bypassed tiny VAE preview, and 10Eros are optional; they should not be confused with the default native production stack.
- Native First Pass remains 20 steps / `res_multistep` / `simple` / denoise 1.0; Second Pass remains 4 / `er_sde` / `linear_quadratic` / 0.30; latent upscale 0.8 MP / `force_unload=false`. No explicit unload types remain in the RC5 master.
- Step 0 tiles use 46% dimension crops at normalized origins 0, 0.27 and 0.54. These cover the whole anchor with overlap for ordinary input sizes; absolute crop-widget defaults are superseded by connected calculations.
- Prompt-capture stage titles survive converter metadata serialization; core PreviewAny produces history `ui.text`, matching the extraction contract. Actual live history must still be verified.

## Multiple truth versions and dormant graph defects

Do not use the standalone `01_CURRENT_TRUTH/VAST_H3_MASTER_NATIVE_INT8_96GB.json` or `04_MANIFESTS/VAST_H3_API_MAP.json` with the RC5 app. The standalone master still contains nested unload node `7364` and 30 additional stale/missing-link discrepancies cleaned in RC5. The older map lacks `workflows` and `prompt_capture`, which the current app requires. The local current workflow has the same 32 intrinsic link inconsistencies as the standalone master and also the deliberately local unload helpers.

RC5 retains two old orphan interface links in `Stitch Extension if Applicable`: `46252/46253` claim swapped output slots from node `6962` and are absent from its output lists. They have been superseded by declared interface output links `46524/46525`; pinned CLI uses those `linkIds` and ignores proxy edges when expanding. These are cleanup defects, **not a proven blocker for the default references-only path**. The remaining two audio link-type labels (`20856/20858` marked IMAGE from AUDIO sources) are stale metadata feeding wildcard paths; runtime types depend on the real node catalog. Full graph-check evidence is in WORKFLOW_GRAPH_CHECKS.txt.

## Capacity and reproducibility limitations

Step 0C context is 12288 with an output allowance of 2800. Nominal upstream output caps are full caption 1600 + nine tiles × 650 + Qwen audit 1800, for 9250 evidence tokens in their generating models; evidence + requested final output totals 12050 before fixed instructions, labels, vision tokens and chat template. Only 238 tokens remain nominally. Different tokenizer counts mean this is a capacity warning, not proof of every input failing. Long outputs can overflow or truncate. Step 1's static format at node `5180` alone is 20376 characters, plus user text and images, against ctx 8192 and output 2048. Real tokenizer measurements, maximum-image/input limits and context handling need explicit acceptance checks.

The manifest lists filenames but generally omits canonical download URLs, sizes and expected checksums; file existence does not establish model integrity or loader compatibility. Exact required weights are absent from the migration. No unattended authoritative download plan can be inferred for every weight. The adapter has a source repository but still needs a pinned artifact/checksum.

Custom-node metadata mixes multiple commits per package (rgthree, Easy Use, KJNodes, essential-er) and even the Easy Use alternative fork. `node install-deps` plus fallback latest clones is not a fully locked environment. Only the LLM Text Processor fallback explicitly pins a commit. A compatible runtime needs a recorded, tested custom-node revision set and Python dependency lock. Missing classes, quantization kernels, extension imports, attention patches and sampler availability cannot be established by JSON parsing.

## Checks still requiring the real GPU runtime

1. Both workers' exact `/object_info`, successful full UI→API validation, absence of unsupported virtual classes, all default model dropdowns and paths.
2. Real model integrity/load checks and Qwen NVFP4/AWQ support on RTX PRO 6000 Blackwell with the pinned PyTorch/CUDA toolchain.
3. GGUF architecture/mmproj pairing with llama.cpp b8840, real image handling, complete text output, context overflow behavior and per-node timeouts.
4. Candidate #2 onward Step 0–2 cache reuse, diversity in Step 3/4, captured prompt sections and assurance the render graph uses precisely the approved text.
5. A controlled single render followed by concurrent prompt/render VRAM and throughput measurements. Torch's DynamicVRAM headroom is not by itself a reservation that guarantees memory for a separate llama-cli process.
6. Actual no-audio and one-audio behavior, video/audio output decode/playback, watchdog interruption and recovery semantics.

Source evidence URLs: PyPI comfy-cli 1.21.0; Comfy-Org/ComfyUI `v0.38.0` execution.py and comfy_extras/nodes_preview_any.py; rgthree/rgthree-comfy commit `2c5342a8cb0eaecaabf61435a5f37dd594c510ba` __init__.py and web/comfyui/label.js. Machine-readable details and checksums are in WORKFLOW_EVIDENCE.json. Source-only conversion did not replace live GPU/catalog validation.
