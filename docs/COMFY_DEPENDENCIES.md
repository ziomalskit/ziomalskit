# ComfyUI deployment dependency pins

Two fresh installs of the previous AJ commit could resolve different versions.
`h3/INSTALL_ON_VAST.sh` calls `scripts/provision.sh`, which selected ComfyUI
v0.38.0 and verified commit `6b747c0428c343e1417219641db93a4fb7cb69ae`, but then
ran `comfy node install-deps --workflow ... --uv-compile`. The workflow records
multiple revisions of several packages, including an Easy Use fork. Registry
discovery and compiling requirements afresh are not a committed environment lock.
The later panel, node and llama.cpp converter pip installs also had open ranges.
Only the three explicit fallback repositories, LLM node and llama.cpp source
were already pinned. The converter requests a CPU torch index after ComfyUI has
selected its CUDA build, so an unconstrained later install could also replace
that platform choice.

The production path now uses the existing Git installer for the complete
`h3/config/custom_nodes_manifest.json` pin set. It does not run registry discovery
or install Manager. The original ComfyUI version and commit checks remain.
Bundled AJ nodes still come from the AJ checkout. Existing local node edits and
unexpected origins still abort installation without discarding those files.
Use a clean ComfyUI/custom-node environment for a fresh deployment; reconcile
older registry installations and unrelated extensions explicitly.

The added Git sources are:

| Package | Fixed revision | Selection evidence |
| --- | --- | --- |
| KJNodes | `3f20054214fec9f9234fd3841ae6f1e4287948f6` | v20 metadata; source declares 1.5.0 and contains its MiniMax classes |
| Easy Use alt | `450b1ce4ce43b2280521c87f5fa388a898fb2ad2` | v20 `aux_id` identifies altoiddealer's fork |
| rgthree | `2c5342a8cb0eaecaabf61435a5f37dd594c510ba` | v20 Power LoRA/Any Switch metadata |
| MiniMaxH3Mod | `6131c1f62c612a074d6aac8435c26ffa19c00563` | v20 loader metadata; source contains both loader and apply classes |
| VideoHelperSuite | `4d907bee61e92c2e65af3bd6383a4e4d356126d1` | v20 metadata |
| Spectrum MiniMax H3 | `beb32dd210ef9e95520453107f158241d4f2ecf3` | v20 metadata |
| JamesWalker various | `5bd85aaf7616878471469c4ec7e11bbd0cef3bf2` | v20 metadata |
| Logic | `214cfba933291be224156d37bc30c25742076b44` | reviewed upstream source with `Bool-🔬`; v20 only records registry version 1.0.0, not a Git revision |

These are source selections, not a claim of completed GPU compatibility testing.
The existing Bunny, essential-er, latent-upscaler and LLM pins remain unchanged.

`h3/runtime-constraints.txt` pins the direct ComfyUI/node runtime requirements,
the shared model Python stack and the CPU-tested panel dependency closure.
`python_env.sh` exports the selected constraint file through `PIP_CONSTRAINT`,
including CLI subprocesses and node lazy pip installs. A file URI handles install
paths containing spaces. Constraints select versions only when a requirement
requests them; they do not install every optional package.

The NumPy 1.26.4 / Transformers 4.57.6 choices come from the pinned llama.cpp
converter requirements. Tokenizers, Hub, Diffusers, Accelerate, PEFT, OpenCV and
serialization pins were checked together with those requirements. In particular,
Hub remains below 1 for Transformers 4.57, and OpenCV 4.11 supports NumPy 1.x.
The isolated downloader retains its separate Hub 2.2.0 / hf-xet 1.7.0 requirements;
its pip invocation clears the runtime constraints.

The three `constraints-cu12.8.txt`, `constraints-cu12.9.txt` and
`constraints-cu13.0.txt` files select **torch 2.11.0, torchvision 0.26.0 and
torchaudio 2.11.0 with the matching `+cu128`, `+cu129` or `+cu130` suffix**.
All nine package/build combinations were checked in the official
`https://download.pytorch.org/whl/cu{128,129,130}/` indexes for Python 3.11/3.12,
Linux x86_64 manylinux 2.28 wheels. The versions match the converter's torch
requirement and torchvision's torch pairing. Wheel METADATA hashes and
requirements against the shared pins were checked for all nine CUDA builds. A missing build or incompatible
requirement fails resolution; the CPU index cannot silently replace CUDA torch.
This does not certify the target image, driver, GPU kernels or attention plugins.
Python 3.11/3.12 and glibc >=2.28 are the reviewed wheel platform; other Python or
OS combinations need an explicit compatibility review.

Provisioning verifies installed package versions and node HEAD/origin before
starting services. Preflight repeats those read-only checks, retains the real
CUDA/Blackwell checks and critical `/object_info` checks. Smoke still converts
and validates both profiles against the live catalog without posting `/prompt`.
Missing imports, classes and changed schemas remain blockers. CPU checks and
dependency metadata resolution do not replace those live checks.

This is a plain pip constraint set, not a complete hash-locked OS/image/build
environment: ancillary transitive dependencies, system tools and optional
extensions are outside the lock. NVIDIA libraries follow the selected torch
wheel's metadata rather than an unrelated machine's `pip freeze`.
The canonical H3 v20 JSON and every model configuration are unchanged.

For a future upgrade, deliberately change `COMFY_COMMIT` in `runtime_config.py`
and both explicit `--version` arguments in `provision.sh`, review the node pins
and requirement/torch pairings, then run CPU checks and target preflight/smoke.
An unavailable pin is an installation error, not permission to use latest.
No automatic update service or package-management framework is introduced.
