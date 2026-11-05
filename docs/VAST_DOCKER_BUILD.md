# Optional Docker track: build and CPU evidence

Based on `feat/dual-llm-profiles` at
`ef84f8e3066e3d14beeba6bb4e87393d675ef6f1`, independently of PR #8.
[Operator first run](VAST_DOCKER_FIRST_RUN.md). Git installation remains default
until the RTX PRO 6000 proof is recorded. No production workflows, app/profile
code, manifests, prompt/schema/LoRA/render settings or model pins are changed.

## Build once, without a visible GPU

Commit the reviewed source, then from the repository root:

```bash
bash h3/docker/build.sh
```

This archives **only committed `h3/` files** into a temporary build context,
uses a digest-pinned BuildKit frontend, targets linux/amd64 and creates a local `aj-vast:<source-sha12>`.
Untracked secrets, `.git`, weights, uploads, outputs and state are excluded.
It does **not** publish. Docker's final report includes image ID and actual
uncompressed `UncompressedBytes`; preserve it with the build log. Registry
compressed transfer size and instance peak disk must also be measured before
choosing an offer. The development workspace has only about 7.5 GiB free and
8 GiB cgroup RAM; a full CUDA/torch/native image build is **NOT RUN** here.
A builder needs substantially more disk/RAM and time than those limits.

In a managed environment with an HTTPS inspection proxy, the script passes the
combined system CA as a BuildKit secret when `CODEX_PROXY_CERT` is configured.
No session certificate or proxy credentials are copied into layers. On an
ordinary builder, use its normal trusted network. Never disable TLS checks.

CUDA 13.0.0 devel / Ubuntu 24.04 is pinned to the NVIDIA registry's amd64 manifest
`sha256:435220c0fef35cbf712e11999f8670a83835ef3cdd18564e5e8122f83078c88c`
(resolved 2026-11-05; parent index
`sha256:1e8ac7a54c184a1af8ef2167f28fa98281892a835c981ebcddb1fad04bdd452d`).
Python 3.12 + pip/venv and system/native/video/SSH packages are established before
the original provisioner. The provisioner is reused through `--image-build`:
ComfyUI 0.38.0 / exact commit, comfy-cli 1.21.0, the existing custom-node and
llama.cpp pins, torch 2.11.0+cu130, existing focused constraints and exact
SageAttention v2.2.0 source. llama CPU code is portable (`GGML_NATIVE=OFF`) while
CUDA architecture remains 120 (upstream maps it to 120a where required).

There is **no full hash lock**: apt updates, Python patch/security releases,
remaining upstream pip transitive dependencies and compiler helpers can change.
`/etc/aj-image.json` records the source revision and resolved apt/pip versions.
After a successful build and separately authorized publication, use the **resulting
image digest** to deploy that exact artifact. No AJ registry reference is invented
here. Building and publishing a qualified prebuilt artifact remains a prerequisite
to a one-command rental experience; no operator should repair packages per rental.

Build compiler objects, apt/pip caches and image SSH host keys are removed in the
same RUN layer. Keep the devel toolkit because runtime checks require nvcc.
No production model is fetched into the image. Comfy's seeded models directory
is checked for unexpected weights. Small vocabulary files shipped in pinned
llama.cpp source are toolchain data, not the production weight store.

## Image versus instance

The image holds dependencies at `/opt/aj/venv`, pinned source at
`/opt/aj/ziomalskit/h3`, Comfy/node/native files under `/opt/aj/seed/ComfyUI`, and
llama converter sources at `/opt/aj/build`. This avoids losing the environment
when `/workspace` is mounted empty. Bootstrap links immutable Comfy files into
`/workspace/ComfyUI`, stages the small panel package into
`/workspace/H3_VAST_MOBILE`, and exposes `/workspace/ziomalskit` as the source link.
All mutable Comfy directories, especially `models`, are real instance directories.
No weights, GGUF copy, symlink model store or second store is created in `/opt`.
Unrelated existing paths and a panel from another image revision are rejected
and retained. Do not run the Git installer
inside this optional image; it intentionally has a separate layout.

Sage `build` verifies toolchain/ABI and installed-file hashes without querying a
GPU, then writes **only** `.aj-sageattention-build.json`. Normal `install` and
`verify` still require visible sm_120 and the actual pinned KJ kernel smoke.
An image receipt is not accepted by runtime `verify`; `install` can promote it
only after successful real smoke. File/ABI drift fails before promotion. No GPU
receipt is written on failed smoke. The existing KJ source checksum and numerical
kernel assertions are unchanged. llama publication similarly records runtime
execution PENDING instead of attempting to load host `libcuda.so.1` in a CPU
builder. The actual executable/version test runs at instance health before weights.

ENTRYPOINT generates per-instance SSH keys and a new `/run/aj-boot-id`, then runs
key-only SSH under tini. No model/service/provider action runs automatically.
`init` and `models` validate every visible cgroup-v2 ancestor, affinity, available
RAM, disk and driver/GPU, process-control capabilities, dependency pins, native
version and Sage kernel before transfer. Hidden host limits remain the provider's
responsibility; Linux/kernel/driver/container policy are real prerequisites.

A boot/image/store-bound ready receipt is written only after the existing model
downloader succeeds. It also binds model inode/size/timestamps, and is invalidated
before each initialization/transfer attempt. Direct service start/restart inside
this image fails without it; ordinary deployments have no image marker. The
original service lock, ownership, pidfd cleanup and readiness logic remain in use.
Panel binding changes to loopback **only in the image**, and its password is not
exported to Comfy workers. The panel retains existing private mode-600
`runtime.env` authentication; no new secrets mechanism is added. Download tokens
are hidden interactive input scoped to the isolated download child. Vast CLI is
absent and its configured command is `/bin/false`; automatic provider lifecycle
is unavailable. Submission gating remains OFF until deliberate operator acceptance.

`start` reapplies submissions OFF by restarting only the panel, then repeats preflight (including full model integrity and GPU checks) and
no-POST conversion smoke. A later failure may leave owned services running with
submissions OFF; use the existing controller, not broad process kills. Process
stop is never billed-resource destruction. No automatic migration/recovery of a
foreign workspace, package updater or new orchestration layer is included.

## Reproducible CPU-only recipe

```bash
make setup
.venv/bin/python -m unittest tests.test_docker_deployment tests.test_sageattention_runtime tests.test_deployment tests.test_service_ctl -v
python3 h3/docker/instance.py plan
make test
make audit
```

`plan` only calculates bytes and says GPU PENDING; it creates no instance files
and does not contact a provider, start services, compile CUDA, download weights
or render. The Docker and Sage tests use temporary directories and inert
GPU/build/download doubles. Production-preservation checks use SHA256s captured
from the exact requested base, including canonical v20
`276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`.
Tests cover empty mounted workspace and repeated bootstrap, retained partials,
foreign/symlink stores, early resource/driver failures, credentials scoped to the
download child, missing/stale/changed readiness receipts and direct start gating,
image-build early exit, build-to-runtime Sage promotion and failed smoke.

Optional fast Dockerfile lint (no CUDA layers or compilation):

```bash
docker build --check --platform linux/amd64 -f h3/docker/Dockerfile .
```

Existing CPU CI is unchanged. There is no mandatory heavyweight Docker build or
publication workflow; the explicit local build command above is the manual build
validation path.

| Proof | Result |
| --- | --- |
| CPU setup/tests/audit | **PASS**; 1049 tests, 16 HTTP smoke checks; record below |
| Local full CUDA image build / measured image size | **NOT RUN / unknown** |
| Clean native CUDA compilation with torch 2.11 / CUDA 13 | **NOT RUN** |
| Real RTX PRO 6000 KJ SageAttention + llama/model load | **PENDING** |
| One H3 Full + one 10Eros Full manual 5s render, MP4 | **PENDING** |
| Vast ENTRYPOINT/SSH/disk/host integration | **PENDING** |

No rented GPU, production model download, image publication, render submission,
Vast lifecycle call or merge was performed. CPU PASS is not end-to-end readiness.

## Verification record — 2026-11-05

- `make setup`: **PASS** — `No broken requirements found.`
- `make test`: **PASS** — `Ran 1049 tests in 586.540s`, `OK`,
  `HTTP SMOKE PASS: 16 functional checks`.
- Final focused Docker/Sage suite: **28 PASS**, using the recipe above's first
  two modules; no GPU/build/download operations were performed by those tests.
- `scripts/check_package.py`: **PASS** — 66 original files unchanged,
  33 JSON documents parsed, 22 shell scripts checked; frontend syntax PASS.
- Docker `build --check`: **PASS** — `Check complete, no warnings found.`
- `instance.py plan`: **PASS** — 278009016136 model bytes,
  `cpu_plan_only: true`, `gpu_smoke: PENDING`, no duplicate download staging.
- Preservation: **51 production file SHA256s PASS**; no Git diff in `migration/`,
  `archive/`, `h3/app/`, `h3/config/`, `h3/workflows/`, `h3/custom_nodes/` or existing
  `.github/workflows/` against the requested base. `git diff --check`: PASS.
- Full final-source `make audit`: **PASS** — `Ran 1049 tests in 599.857s`,
  `OK`, `ACTIVE CPU AUDIT PASS: 1049 regression tests; no skipped tests.`

The container's PID 1 is `tail`; full tests run under a temporary verification-only
Linux child subreaper, so adopted inert test processes are reaped. No production
process-control change or timeout adjustment is used for that environment detail.
`nvcc` and `nvidia-smi` are unavailable. Full image build, actual CUDA source
compilation, image size, real KJ kernel, model loading, Full videos and Vast
integration retain the NOT RUN/PENDING labels above.

An early focused run exposed a temporary subshell that broke the service launch
controller-parent relationship (four service test failures). It was removed
before the successful full runs; pidfd/ownership checks were not weakened. An
initial new-test fixture setup error was also corrected before the 28-case PASS.
