# Trusted workflow adapters and render controls

PR #6 remains stacked on PR #5 commit
`c6f033ab41a4456fd55558e652a563d8c31418d1`. This extension continues PR #6 head
`a527817ce67cb93ee606243ff5fa7d05c26e7d53`, on the same branch. AJ production
composition follows `job → frozen workflow_id → trusted adapter → verified
immutable baseline → validated runtime graph`. The controller does not address
production node IDs. H3 Full and 10Eros Full both select `h3_v20` in
`h3/config/production_profiles.json`; no other production workflow is installed.

## Immutable baseline and product defaults

`h3/workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json` remains **3,084,071 bytes**,
SHA256 **276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222**.
Bytes and all required anchors are verified before runtime adaptation. The file's
nodes, prompt text, model provenance, duration expressions and saved widgets are
unchanged. All adaptation happens in a newly loaded runtime graph.

| Resolved setting for new jobs | Product default | Meaning |
| --- | --- | --- |
| `target_mpx` | `0.4` | Initial render area |
| `first_steps` | `24` | First sampler's scheduler interval count |
| `latent_upscaler_enabled` | `false` | Existing video latent upscaler and AV helpers bypassed |
| `latent_upscaler_mpx` | `1.2` | Upscale target, retained in job state while OFF |
| `second_pass_enabled` | `false` | Final switch selects incoming first-pass latent |
| `second_steps` | `4` | Second scheduler interval count, retained while OFF |
| `second_pass_loras` | `[]` | No second-pass LoRAs; selection strength initially `1.0` |

The canonical upscaler's saved value remains **0.8 MPx**. When OFF, its runtime
widget also remains 0.8 and the converter removes the bypassed node. The product
value 1.2 is frozen in job state and applied only when ON. Neither this value,
second-step changes nor second-pass LoRA selections implicitly enable a feature.

The first/second inner schedulers' saved `20`/`6` are connected-widget mirrors,
not effective defaults: parent widgets supply **24/4**. The legacy first-pass
API map's `20` is not the production default. Existing sampler names, schedules,
denoise values and first-pass LoRA defaults remain unchanged.

The unchanged fixture `tests/fixtures/v20_pr5_parity.json` was captured from
PR #5 before the initial adapter refactor. It records sorted, compact canonical
JSON digests for **22** profile/job UI, prompt/render API and cached-analysis
compositions across both profiles and audio/no-audio variants. Only new
`extra.aj_production` identity metadata is excluded; all original metadata,
prompts, widgets, seeds, edges and guards remain compared. Default settings,
including inactive product target 1.2, match every digest. This establishes CPU
composition parity, not GPU execution parity.

## Authoritative controls and semantics

Discovery uses the checksum-verified canonical graph, scoped graph edges,
Set/Get resolution, the pinned converter and pinned backend implementations.
Titles and canvas positions do not establish identity or ordering.

**The earlier informal “on/off” means toggling the entire second pass. There are
no requested numeric `on_steps` or `off_steps` controls.** Spectrum's separate
`warmup_steps=5` and `tail_actual_steps=1` remain untouched and unexposed; they
control model forecast/cache acceleration rather than an extra sampling stage.

| Control | Exact canonical source/type/widget | Prepared API destination | Type, product range and default |
| --- | --- | --- | --- |
| Render area (MPx) | Root `1731`, subgraph `a4078340-52f5-4ab4-a7db-04dea29501b2`, FLOAT `value_1`; graph input 3 → edge `22101` → `1735.megapixels` | `1731:1735`, `ResolutionSelector.megapixels` | Finite number [0.1, 2.0], default 0.4 |
| First-pass steps | Root `7334`, subgraph `62fc1cf4-4ac3-413d-bafc-12558c24914e`, INT `steps`; graph input 6 → `46047` → `6771.steps` | `7334:6771`, `BasicScheduler.steps`; SIGMAS → `45242` → `7334:6773`, `SamplerCustomAdvanced` | Integer [1, 100], default 24 |
| Latent upscaler | Root `6175`, `MinimaxH3LatentUpscaler3D`, node mode 4; companions `6176`, `6177`, `6178` also mode 4 | Existing `6175` with canonical AV split/rejoin nodes | Exact boolean, default false; modes 4=OFF, 0=ON for all four |
| Upscale target (MPx) | Root `6175`, `MinimaxH3LatentUpscaler3D`, FLOAT `mode.megapixels`, saved 0.8 | `6175.mode.megapixels`, only when upscaler ON | Finite number [0.1, 2.0], product default 1.2 |
| Second pass | Root `6263`, `Bool-🔬`, saved `value=True`, node mode 4; real chain detailed below | `6263.value=True` present only when ON; `7335:6266` → `7335:6268` → `7335:6173` | Exact boolean, default false; root 6263 mode 4=OFF, 0=ON |
| Second-pass steps | Root `7335`, subgraph `f9092623-6d48-4b5a-b478-6b65bfaf31eb`, INT `steps`; input 5 → `46090` → `360.steps` | `7335:360`, `BasicScheduler.steps`; SIGMAS → `45242` → `7335:469`, `SamplerCustomAdvanced` | Integer [1, 100], default 4; executes only when second pass ON |
| Second-pass LoRAs and strengths | Root `6165`, `Power Lora Loader (rgthree)`, canonical empty stack, mode 4; MODEL-only branch | Existing `6165.model`, ordered `lora_1`…`lora_16`; output to `7335:6179.anything` | Registered IDs plus exact bool `enabled` and finite strength within registry bounds (global envelope [-2, 2]); default empty |

ResolutionSelector calculates area as **MPx × 1024 × 1024**, retaining canonical
`2:3 (Portrait Photo)` aspect and rounding dimensions to multiples of 32.
`match_reference_size=False`, `custom_width=0`, `custom_height=0` choose its
area-driven route. This controls render latent area, not codec quality or
reference-image preprocessing. Upstream MPx range is [0.1, 16.0]; AJ narrows it
to [0.1, 2.0]. UI increments of 0.1 are hints; finite intermediate values are
accepted. Reference-resize MPx fields remain independent and unchanged.

Step values are BasicScheduler interval counts, not writer generation budgets.
Upstream steps range is [1, 10000]; AJ uses [1, 100]. First pass retains
`res_multistep`, `simple`, `denoise=1`; second retains `er_sde`,
`linear_quadratic`, **`denoise=0.29999999999999993`**. Below denoise 1, upstream
computes `total_steps=int(steps/denoise)` and retains the last `steps+1` sigma
values. There is no canonical ordering constraint between the two step counts.

All controls address the same graph for both profiles. LoRA compatibility and
strength ranges remain registry/profile-specific. Higher area and activating
upscaling/second pass can materially increase work and VRAM. More steps increase
sampling work/time. AJ's bounds are input limits, **not measured GPU safety**.

## Second-pass enable chain and lazy output route

The exact canonical chain is:

```text
6263 Bool-🔬 (saved True, bypass mode 4)
  --45851--> 6264 SetNode (Constant=second_pass_apply, mode 4)
  [virtual Set/Get resolution]
7335:6267 GetNode
  --45852--> 7335:6266 easy PassOrNone.anything
    output --45855--> 7335:6268 ComfySwitchNode.on_false
    is_none --45853--> 7335:6268.switch
7335:6269 PrimitiveBoolean (False)
  --45854--> 7335:6268.on_true
7335:6268 --45856--> 7335:6173 ComfySwitchNode.switch
```

OFF keeps 6263 mode 4. The API contains no `6263`, and `7335:6266.inputs={}`.
Pinned PassOrNone returns `(None, True)`; 6268 therefore selects the fallback
false value. Output switch 6173 selects its **on_false incoming latent**.

ON changes **only 6263's node mode to 0**, retaining its true widget. SetNode
6264 remains mode 4: the pinned converter's virtual Set/Get resolver still
resolves its source. The API includes `6263` (`Bool-🔬`, `value=True`) and
`7335:6266.anything=["6263",0]`. PassOrNone returns `(True, False)`; 6268 selects
true, so 6173 selects **on_true=["7335:469",0]**. Returning OFF restores the exact
baseline graph. No parallel enable switch is inserted.

Both output-switch branches are **lazy** in pinned ComfyUI. The adapter requires
live `/object_info` to declare `lazy=True` for both `on_false` and `on_true`;
missing, eager or malformed branch metadata fails with an explicit adapter
compatibility error. An OFF second sampler remains structurally present in the
API ancestry but is not selected for execution by this switch. CPU tests evaluate
this branch contract with opaque sampler markers, without inference.

## Second-pass MODEL ancestry and existing LoRA path

Tracing MODEL edges, rather than node labels, establishes:

```text
4595 renderer-loader subgraph (MODEL output of 4595:4529 UNETLoader)
  --45545--> 6091 attention subgraph
    6091:6079 easy anythingIndexSwitch (selected attention MODEL)
  --> 6161 second-model patches subgraph (bypassed, mode 4)
  --45637--> 6165 Power Lora Loader (rgthree) (empty, mode 4)
  --45638--> 6166 SetNode (Constant=model_second, mode 4)
  [virtual Set/Get resolution]
7335:6170 GetNode
  --45676--> 7335:6179 easy PassOrNone.anything
  --45679--> 7335:29 BasicGuider.model
  --45680--> 7335:360 BasicScheduler.model
```

The second-model patch subgraph type is
`5118e6a5-671c-4fc6-9e58-805df7045e52`; its bypassed Spectrum/model patch nodes
stay bypassed. The shared attention subgraph type is
`5abc5ffb-b441-44d8-893e-92ebd215b94c`.

Inside renderer subgraph `3a3c555b-0f97-43fb-a8ca-cc713f8a6e3e`, parent
`unet_name` feeds 4529 through edge `22116`; MODEL output edge `45543` maps to
parent output slot 3, then root edge `45545` enters attention graph input 2.
Its complete MODEL alternatives are:

- Sage branch: input 2 → `45497` → `6081 MiniMaxH3MemoryEfficientSageAttentionPatch`
  → `45489` → `6078 PathchSageAttentionKJ` (`sage_attention=auto`,
  `allow_compile=False`) → `45467` → `6079.value2`.
- PyTorch/Kitchen branches: input 2 → `45496` → `6085 PathchSageAttentionKJ`
  (`sage_attention=disabled`, `allow_compile=False`), then `45463` →
  `6069 ModelAttentionBackend` (`pytorch attention`) → `45464` → `6079.value0`,
  or `45465` → `6070 ModelAttentionBackend` (`comfy kitchen attention`) →
  `45466` → `6079.value1`.
- Parent `choice=sage attention` enters `6080 CustomCombo` through `45471`;
  its selected index output → `45462` → `6079.index`. Parent Sage option
  `auto` enters 6078 through `45472`. Output edge `45507` maps 6079 to the
  parent MODEL output. The saved inner index 0 is connected, not authoritative.

These exact paths are preserved in the prepared API under `6091:*`; the
renderer API node becomes `AJLateUNETLoader` at the unchanged release boundary.
No attention backend choice or patch is changed by either new toggle/LoRA.

**Canonical loader 6165 is reused; no adapter-inserted LoRA node is needed.**
With no enabled selection, or second pass OFF, the API has no 6165 and
`7335:6179.anything=["6091:6079",0]`. With second pass ON and an enabled registered
selection, only loader 6165 changes to mode 0 and receives AJ's existing
sixteen-slot stack. API `6165.model=["6091:6079",0]` feeds
`7335:6179.anything=["6165",0]`, before both scheduler and guider. It has **no
CLIP input**. Off/empty restores the canonical named **and positional** empty
header exactly, including its extra positional divider.

The first branch remains `6091 → 4537 first-model patches → 6164 first-pass
LoRA loader → 1710 model_first alias → 4596 GetNode → 1977 bypassed preview →
7334`. Both first API MODEL consumers (`7334:6769` guider and `7334:6771`
scheduler) retain `["6164",0]`. Tests compare the entire first-sampler ancestry
before/after second-LoRA selection. Pinned rgthree delegates each row to core
LoraLoader; core `load_lora_for_models` clones the MODEL patcher before applying
patches. This isolates graph/model patches, not a claim about live memory usage.

The same trusted registry, provenance enrollment, profile compatibility,
strength bounds and stack marshaler serve both passes. Browser input is canonical
IDs plus strengths/enabled flags, never filenames. No selected second-pass LoRA
means exact baseline semantics. Selected inactive settings remain persisted;
selection alone never activates second pass.

The adapter's generic `active_render_loras(context)` supplies only active stacks
to the existing controller provenance/file verifier. It merges both passes by
registered ID, checks each file once, and uses the same trusted size/SHA or
explicit external-enrollment receipt. An external second-pass LoRA with no
receipt, changed bytes, a wrong destination or a symlink is rejected before
dispatch. A local receipt cannot override a verified-source hash. OFF selections
add no file requirement and do not execute. This reuses enrollment/verification
logic; it neither invents provenance nor creates another validation path.

## Latent upscaler position, mechanism and fixed options

The complete real latent path is:

```text
7334 first-pass sampler output
  --46745--> 6176 LTXVSeparateAVLatent.av_latent
  --46543 (video slot 0)--> 6175 MinimaxH3LatentUpscaler3D.latent
  --45657--> 6178 LTXVConcatAVLatent.video_latent
7334 output
  --46746--> 6177 LTXVSeparateAVLatent.av_latent
  --45658 (audio slot 1)--> 6178.audio_latent
6178 --46747--> 7335 second-pass subgraph input on_false (slot 1)
  --46762--> 7335:469 SamplerCustomAdvanced.latent_image
  --46763--> 7335:6173 ComfySwitchNode.on_false
7335:469 --46765--> 7335:6173.on_true
7335 output --46932--> 6960 AV decode subgraph
  [API 6960:6959.samples=["7335:6173",0]]
  --> 7456/7457 --> 6182 CreateVideo --> 7331 SaveVideo
```

Thus ordering is **first pass → optional video upscaler with unchanged audio
rejoin → optional second pass → AV decode → final video**. The second sampler
consumes upscaler output when both are ON. When second pass is OFF, the upscaled
first-pass AV latent can go straight to decode.

OFF retains canonical mode 4 for **6175, 6176, 6177 and 6178**. Their bypass
resolution passes first-pass AV latent directly to 7335. ON activates **all four
existing nodes in mode 0**, without new nodes, links or a substitute upscaler.
Mode change of 6175 alone is insufficient: it expects a plain image/video tensor,
not the joint AV latent; the canonical split/rejoin nodes supply its video tensor
and preserve audio. No separate loader node is needed: 6175 loads its own named
model internally. Its actual installation/model availability remains a live gate.

Prepared ON inputs are fixed except for `mode.megapixels`:

| Input | Canonical value kept in production | Pinned upstream effect |
| --- | --- | --- |
| `model_name` | `minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors` | Internal model load/cache; no new model/provenance entry added |
| `mode` | `megapixels` | Target video spatial area; neither initial/reference MPx nor codec quality |
| `align` | `32` | Rounds target pixel dimensions to multiples of 32, then VAE downsample 16 |
| `enable_temporal_chunking` | `true` | Processes long latent sequences in temporal chunks of 32 with kernel-derived overlap, padding and blending; T stays unchanged |
| `force_unload` | `false` | Retains the node's cached model; true would offload it to CPU after inference. This widget remains fixed |
| `device` | `cuda` | Upstream selects CUDA if available, otherwise CPU; AJ does not invoke this path during CPU validation |
| `precision` | `fp16` | Model/computation precision; output returns to CPU in original latent dtype |

`mode.megapixels` is target spatial video area **MPx × 1024²**. The implementation
preserves incoming latent aspect, aligns pixel dimensions, converts to latent
H/W at downsample 16, and keeps temporal length T. **1.2** is a product target
area, not a 1.2× scale. Exact pixel dimensions depend on input aspect/alignment.
Upstream DynamicCombo limits are [0.1, 16.0], step 0.1, factory default 1.0;
canonical saved value is 0.8; AJ's product default is 1.2 with [0.1, 2.0] bounds.
The node rejects spatial downscaling. AJ therefore requires target MPx at least
initial render MPx **only while enabled**; equal aligned dimensions can be a
no-op. Disabled values may remain lower than the initial target.

No option above is derived from render controls or the frozen memory policy.
In particular, `force_unload=false` means the custom node cache may retain GPU
weights. Cache residency, AV mask behavior, end-of-video release interaction,
peak VRAM and overlap with prompt work need GPU acceptance. Existing AJ release
boundaries/memory policy remain unchanged; CPU analysis cannot certify them for
these newly enabled combinations.

### All four combinations

| Upscaler | Second pass | Incoming latent to 7335:469 and 7335:6173.on_false | Selected final latent | Selected work |
| --- | --- | --- | --- | --- |
| OFF | OFF | `["7334:6773",0]` | First-pass result via 6173.on_false | First sampler |
| ON | OFF | `["6178",0]` | Upscaled/rejoined first-pass result via 6173.on_false | First sampler + video upscaler |
| OFF | ON | `["7334:6773",0]` | `["7335:469",0]` via 6173.on_true | First and second samplers |
| ON | ON | `["6178",0]` | `["7335:469",0]` via 6173.on_true | First sampler → video upscaler/rejoin → second sampler |

Second-pass LoRAs affect only the second MODEL for either upscaler state. Neither
toggle changes the other. An OFF second pass executes no selected second-pass
LoRA. Step/strength/MPx edits alone never enable a branch.

### Inspected pinned implementations

- ComfyUI `6b747c0428c343e1417219641db93a4fb7cb69ae`:
  [resolution](https://github.com/Comfy-Org/ComfyUI/blob/6b747c0428c343e1417219641db93a4fb7cb69ae/comfy_extras/nodes_resolution.py),
  [samplers](https://github.com/Comfy-Org/ComfyUI/blob/6b747c0428c343e1417219641db93a4fb7cb69ae/comfy_extras/nodes_custom_sampler.py),
  [lazy switch](https://github.com/Comfy-Org/ComfyUI/blob/6b747c0428c343e1417219641db93a4fb7cb69ae/comfy_extras/nodes_logic.py),
  [AV split/rejoin](https://github.com/Comfy-Org/ComfyUI/blob/6b747c0428c343e1417219641db93a4fb7cb69ae/comfy_extras/nodes_lt.py),
  [MODEL clone](https://github.com/Comfy-Org/ComfyUI/blob/6b747c0428c343e1417219641db93a4fb7cb69ae/comfy/sd.py).
- Easy Use alt `450b1ce4ce43b2280521c87f5fa388a898fb2ad2`:
  [PassOrNone](https://github.com/altoiddealer/ComfyUI-Easy-Use-alt/blob/450b1ce4ce43b2280521c87f5fa388a898fb2ad2/py/nodes/logic.py).
- rgthree `2c5342a8cb0eaecaabf61435a5f37dd594c510ba`:
  [Power LoRA loader](https://github.com/rgthree/rgthree-comfy/blob/2c5342a8cb0eaecaabf61435a5f37dd594c510ba/py/power_lora_loader.py).
- Existing upscaler fallback `40316cf008b2fd8663263270669eb4da23f89d2c`:
  [3D upscaler](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler/blob/40316cf008b2fd8663263270669eb4da23f89d2c/nodes/minimax_h3_latent_upscaler_3d.py).

Only static source was inspected. No model was loaded and no GPU was exercised.

## Descriptor, trusted engine and fail-closed validation

`h3/app/workflow_adapters.py` is the generic engine/registry;
`h3/workflow_adapters/h3_v20.json` is the canonical schema-version-1 descriptor.
It binds baseline filename/size/SHA, **88 graph-scoped anchors** and **239 required
edges**, strengthening the initial PR #6 checks. Anchors validate exact type,
mode, widget names/default hashes, ports/slots/link mirrors, LoRA row structure,
empty positional headers and subgraph interfaces. Required edges record
`[id, origin, origin_slot, target, target_slot, type]`. Prompt instructions are
not duplicated in the descriptor.

Declarative controls bind trusted anchor/widgets, mode changes, baseline versus
product defaults, dependencies and conditional API edges. `minimum_from` encodes
the enabled-only upscale constraint. Existing renderer/encoder/VAEs, Step 3/4
writers, reference/audio controls, seeds, frame/duration expressions, conditioning
release and final video path remain bound. Every required anchor is checked
before mutation; there is no nearby-node/title guessing or mutable fallback.

The only registry dispatch selects statically imported repository hooks. JSON
cannot import Python, and users cannot upload adapters. `h3/app/adapters/v20.py`
handles necessary v20 normalization and validates exact enabled/disabled API
routes. `h3/app/adapters/v20_api.py` retains temporal, durable-analysis and release
guards. Generic operations are `load_and_validate`, `apply_profile`,
`apply_duration`, `apply_references`, `apply_audio`, `apply_loras`,
`apply_render_settings`, `prepare_api_prompt`, `install_runtime_guards` and
`active_render_loras` and `validate_final_graph`. Compatibility facades and diagnostic legacy IDs stay
outside the production controller's node addressing.

Validation binds conditional latent/MODEL edges before guards and again after
guards, fixed upscaler inputs, lazy switch contract, boolean logic, scheduler
settings, isolated LoRA ancestry, decoder source and intended SaveVideo sink.
Wrong bytes, nodes/types/modes/widgets/ports/edges/defaults or swapped writers
raise `WorkflowAdapterCompatibilityError`. Worker catalog and UI saved-widget
validation remain required; output-node registration and phase isolation remain
fail-closed. No validation or production gate was weakened.

Temporal/writer guarantees remain **20 s → 481 frames → 20.040 s**; both
AJCreativeTimelineGuard and AJFinalPromptTimeGuard remain. Step 3 thinking OFF
(8192/24576), Step 4 thinking ON (budget 4096, generation/context 12352/29184),
H3 4B Heretic Q8 writer, 10Eros DavidAU 9B BF16 target and EOS completion checks
are unchanged. Render priority, conditioning/encoder release, prompt cache/service
epochs and frozen memory/overlap policy remain unchanged, with no global GPU mutex.

## Frozen jobs, recovery and browser capabilities

Each production job and durable batch context freezes:

- `workflow_id`, `workflow_baseline_sha256`, `workflow_adapter_id`,
  `workflow_adapter_schema_version`, `workflow_adapter_identity`;
- all seven resolved `render_settings`, including inactive targets, steps and
  explicit LoRA IDs/enabled flags/strengths;
- `workflow_render_identity`, hashing workflow identity plus resolved settings.

Adapter identity hashes the descriptor and trusted implementation closure,
including hooks, converter/validation, writer/temporal and typed render code.
These identities integrate with existing profile/model/provenance, writer,
duration, first-pass LoRA and functional memory/overlap identities. Recovery
uses the complete frozen values, never new defaults or newly registered LoRAs.
Missing fields, hash tampering, changed adapter implementation/descriptor or
profile workflow route require **reconciliation_required**. Earlier PR #6
three-field jobs and PR #5 jobs are not silently migrated. Completed MP4 links,
remote submission IDs and existing lifecycle/service fencing remain intact.

`/api/config` supplies each trusted profile's workflow ID/label and
`render_controls`. Public fields are label/type/default, min/max/step, group,
help, `enabled_when`, `minimum_from` and LoRA `default_strength`; graph addresses
are omitted. Registry capabilities supply canonical IDs, labels, compatibility
and strength bounds. All controls appear in the compact main form. Dependent
numeric/LoRA fields remain visible, grey and disabled while their toggle is OFF,
but submission still collects their unchanged values. The dynamic registered
LoRA stack starts with none selected and supports multiple entries.

The server's typed vocabulary rejects unknown fields, node IDs, graph paths,
widgets, LoRA filenames/paths, nonfinite values, disguised booleans, fractional
steps and nulls. Second-pass LoRA entries allow only ID, strength and exact
boolean enabled; default strength/enabled can be resolved for new requests, but
frozen rows must contain every field. Registry enrollment, compatibility,
duplicate IDs, strength and capacity checks run server-side. Both browser and
server check enabled-only upscaler/downscale constraints; the browser is never
authoritative. Existing UUID/idempotency fencing and localStorage retries retain
the exact original request body, even after reload/default changes.

## Adding a future workflow

1. Authorize and freeze its real baseline; inspect graph/backend semantics and
   establish size/SHA before mapping controls.
2. Add a reviewed descriptor with scoped structure/edge assertions, baseline and
   product defaults, trusted controls and production input/output contract.
3. Register its ID and any necessary statically imported hook at the single
   dispatch point. Prefer declarative mappings over Python transformations.
4. Point a separately reviewed profile at the ID. Extend the typed vocabulary
   only for verified controls; derive UI from backend capabilities/dependencies.
5. Add corruption, default parity, conversion, typed security, frozen recovery and
   actual-JavaScript retry tests. Identity changes require explicit reconciliation.
   Perform separate GPU/live acceptance before claiming production safety.

## Adversarial review and validation boundary

Review covered accidental default activation, latent/MODEL leaks, canonical
versus product defaults, adapter drift, disabled-value loss and capability/request
mismatch. Concrete safeguards have regression coverage:

- Activating only upscaler 6175 is rejected; split/rejoin companions and the exact
  video/audio API slots must match the frozen toggle.
- Lazy switch worker metadata is mandatory, including explicit errors for
  malformed options; OFF branches cannot depend on eager switch behavior.
- API guider MODEL edges are validated even when conditioning edges are rewritten
  by release guards; the second LoRA loader cannot enter first-pass ancestry.
- Switching OFF restores canonical 0.8 and the exact empty second-LoRA positional
  header, preserving all 22 default parity digests.
- Disabled controls remain in submission/retry state without changing values;
  downscale validation happens before uploads for enabled settings.
- Frozen settings cannot refill from descriptor/registry defaults or migrate
  across adapter hashes. Browser graph mutation and type coercion are rejected.
- Active second-pass LoRAs are included in the original checksum/enrollment gate;
  external entries cannot bypass verification by being selected only for pass 2.

The extension adds **67 tests**: 40 route/control/security tests in
`test_v20_render_features`, 14 persistence/controller/HTTP tests in
`test_render_feature_recovery`, seven real-file/enrollment/blocker tests in
`test_render_feature_provenance`, and six additional actual-JavaScript tests in
`test_adapter_frontend`. Existing adapter/parity, recovery, temporal and Ultra
assertions remain. All four combinations and second LoRAs with upscaler OFF/ON
are checked against actual prepared API edges and an independent lazy-route
CPU evaluator; no inference is mocked into a GPU safety claim.

Required checks are `make setup`, `make test`, `make audit` and the focused
adapter/render/frontend suite. CPU validation covers structural compatibility,
default parity, routes, settings security, persistence and frontend behavior.
It does not establish real model loading, live catalog compatibility, video
quality/duration, performance, VRAM coexistence or safe GPU overlap. The upscaler
cache and all enabled combinations remain GPU acceptance gates. The DavidAU 9B
provenance/BF16 build/load/generation blocker remains unchanged. No Vast/GPU
operation or merge belongs to this change.

## Recorded CPU acceptance for this extension (2026-10-11)

| Check | Final result |
| --- | --- |
| `make setup` | PASS; pinned dependencies installed, `pip check` clean |
| `make test` | PASS; **644 tests + 16 HTTP smoke checks** |
| `make audit` | PASS; **644 tests**, all **7** historical defects reproduced |
| Focused suite | PASS; **251 tests** |
| Added tests | **67**: routes/settings/security 40, recovery/controller/HTTP 14, file/enrollment/blocker 7, frontend 6 |
| Default parity | All **22** original canonical-JSON digests match |
| Final failures / errors / skips | **0 / 0 / 0** |
| Local panel | `make panel`, authenticated HTML/config and seven-control capability checks PASS; owned panel stopped |
| Adapter validation | All **69 / 220** original anchor/edge checks retained; now **88 / 239** |
| Canonical attachment | **3,084,071 bytes**; SHA256 **276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222** |
| Historical integrity | All **72** tracked migration/archive files byte-identical to both PR #5 and this extension's starting head |
| Protected source | Four native modules unchanged; eleven protected controller function ASTs identical |
| Provenance/config | All six config files unchanged from this extension's start; production model/custom-node/LoRA manifests unchanged from PR #5 |

The focused suite includes `test_workflow_adapters`, `test_render_settings`,
`test_adapter_recovery`, `test_adapter_frontend`, `test_production_architecture`,
`test_production_api`, `test_temporal_contract`, `test_production_controller`,
`test_ultra_fixes`, `test_step3_api`, `test_v20_render_features`,
`test_render_feature_recovery` and `test_render_feature_provenance`. Frontend
coverage executes the actual JavaScript; file/enrollment coverage uses only tiny
synthetic bytes in disposable directories. Its model-manifest prerequisite is
stubbed to isolate the LoRA gate; a separate test requires the unmodified
DavidAU manifest blocker to reject production despite otherwise healthy files.

Full suites run sequentially under the same ignored disposable CPU subreaper
approach used for PR #5. It collects only its own exited adopted descendants,
signals no process, and leaves process teardown assertions/timeouts unchanged.
No model weights were downloaded, no real ComfyUI submission or Vast/GPU action
ran, and no PR was merged. GitHub Actions on the final pushed head is verified
separately in PR #6's validation report.
