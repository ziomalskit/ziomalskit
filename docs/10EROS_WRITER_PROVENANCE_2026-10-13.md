# 10Eros writer provenance closure — 2026-10-13

This note records the currently verified evidence for the canonical 10Eros Step 3/4 writer and the remaining work required before production readiness.

## Canonical target

- Source repository: `DavidAU/Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic`
- Observed exact source revision: `72f2d803ae63499fc463607d997fe48abd38f093`
- Required production output: `Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic-BF16.gguf`
- Required llama.cpp revision: `60eeeb6082c1126bb8bc72902c83123cd056811b`
- Required converter SHA256: `e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`

The production requirement remains a deterministic BF16 GGUF generated from the exact BF16 source. A public quantized substitute is not accepted as the production writer.

Project decision (2026-10-13): retain BF16 as the production requirement. The successful public IQ4_XS run remains diagnostic compatibility evidence only and must not replace, relax, or silently downgrade the canonical 10Eros writer.

## Newly verified public source evidence

The Hugging Face source page for
`model-00001-of-00002.safetensors` identifies the upload commit as
`72f2d80`, consistent with the full observed revision above, and exposes the
following Xet/LFS content identity:

- path: `model-00001-of-00002.safetensors`
- SHA256: `a033682fdc66e0fb66abfaaad811f5719dde8f80e916b70bb3a3b9887f1967bb`
- displayed remote size: 9.93 GB
- evidence URL:
  `https://huggingface.co/DavidAU/Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic/blob/main/model-00001-of-00002.safetensors`

The exact byte size is intentionally not inferred from the rounded UI value.

Public indexing also confirms that the model remains discoverable as the base model
of `nuofang/Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic-GGUF`.
That is lineage evidence only; the quantized repository is not an authoritative
replacement for the canonical BF16 source.

## Independent access probe

A temporary GitHub Actions probe was run from a fresh GitHub-hosted Ubuntu runner,
without user credentials, against the exact source revision. Workflow run
`38404902870`, job `115321854210`, completed normally and independently
reproduced the access restriction.

The exact-revision Hugging Face model-tree API and exact-revision resolver
HEAD/GET requests returned HTTP 401 with:

`x-error-message: Invalid username or password.`

This was reproduced for the model tree and for the requested source paths:

- `config.json`
- `generation_config.json`
- `model-00001-of-00002.safetensors`
- `model-00002-of-00002.safetensors`
- `model.safetensors.index.json`
- `processor_config.json`
- `tokenizer.json`
- `tokenizer_config.json`

Therefore the remaining metadata gap is an upstream Hugging Face authorization
boundary, not a failure of the AJ manifest/builder or the user's Windows machine.

The user also opened the canonical source repository while logged in through the
normal Hugging Face web UI on 2026-10-13 and received HTTP-style 404/not-found
behavior rather than a usable request-access flow. A same-day public search found
the surviving `nuofang/...-GGUF` quantized derivative and cached/indexed evidence
for the original source, but no trustworthy publicly indexed full BF16 mirror or
complete source snapshot. This does not prove that no private/archive copy exists;
it only means none was suitable for freezing into production provenance.
The temporary probe workflow is not part of the production design and is removed
after recording this evidence.

Separately, the user's Hugging Face token successfully authenticated to
`/api/whoami-v2` (HTTP 200) but received HTTP 404 for this model and its exact
revision. That proves the token itself was syntactically valid but did not provide
usable access to this source. No token value or credential is stored in this
repository.

## Local compatibility smoke completed

A Windows-native local smoke was completed with the public IQ4_XS derivative using
llama.cpp build `b11580-4413a1924`.

Observed result:

- model loaded successfully;
- architecture/model metadata were recognized;
- text generation started successfully;
- observed prompt processing: about 27.9 tok/s;
- observed generation: about 110 tok/s;
- the requested four-shot / 20-second planning task was being followed before the
  deliberately small `-n 256` limit truncated the response.

This smoke establishes that the DavidAU model lineage is compatible with a current
llama.cpp runtime and can generate locally. It does **not** prove the canonical
BF16 conversion recipe, pinned b10472 runtime compatibility, exact BF16 output
hash, or production GPU acceptance.

## Remaining blocker

Do not move `10eros-writer` out of `pending_artifacts` yet.

The generation recipe still requires an authoritative complete source-file set at
revision `72f2d803ae63499fc463607d997fe48abd38f093`, including exact byte sizes and
SHA256 identities for every converter input. At minimum this must cover:

- `config.json`;
- `model-00001-of-00002.safetensors`;
- `model-00002-of-00002.safetensors`;
- `model.safetensors.index.json`;
- tokenizer/configuration files actually consumed by the pinned converter.

No hash or byte size may be copied from a related DavidAU/Qwen3.5 model or inferred
from a rounded web UI value.

The pinned converter itself was inspected at
`60eeeb6082c1126bb8bc72902c83123cd056811b`. Its Qwen tokenizer path delegates
to `AutoTokenizer.from_pretrained(dir_model, trust_remote_code=True)`; therefore
the tokenizer/config files used by that exact source are part of the conversion
input identity and cannot be safely guessed from a neighboring Qwen3.5 model.

After authorized source identities are obtained, the existing
`h3/scripts/build_generated_models.py` path should be used unchanged to:

1. verify every pinned source file;
2. verify source architecture, BF16 dtype and shard index;
3. verify the clean pinned llama.cpp/converter toolchain;
4. run the fixed BF16 GGUF conversion;
5. hash and size the generated output;
6. perform the existing CPU zero-generation-token load probe;
7. additionally perform a short generation smoke before consolidated GPU acceptance;
8. freeze the resulting durable provenance and move the artifact from pending to the
   normal trusted artifact set only after all checks pass.

## Safety / release status

- PR #6 remains frozen at `84af26e26c0ec85a40efe712e5e32506c5a9af3f`.
- No production manifest readiness claim is changed by this note.
- No Vast instance was created.
- No paid GPU work was triggered.
- No production render was dispatched.
