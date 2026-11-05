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
After a successful CI build and publication, use the **verified registry report's
image digest** to deploy that exact artifact. The local command does not publish.
Building and publishing a qualified prebuilt artifact remains a prerequisite
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

## CI build and publication

Existing CPU CI is unchanged. The separate, existing
[`aj-docker-image.yml`](../.github/workflows/aj-docker-image.yml) builds only on
`feat/optional-vast-docker` pushes affecting the image inputs, or explicit dispatch
on that branch. It uses standard free `ubuntu-24.04` runners and ephemeral
`GITHUB_TOKEN` GHCR authorization. It does not run on forks or production branches.

Runner SDK cleanup and 8 GiB additional swap protect the build's resource budget.
BuildKit stages and native compilation run with one worker; the image is pushed
directly without a local image load or a second exported tarball. Compiler
objects and apt/pip caches are removed before the provisioning layer is committed.

Publication is accepted only after fetching and SHA256-checking the actual GHCR
manifest and config, checking the tag, linux/amd64 platform and source revision,
and checking every layer's availability and size. The build log, BuildKit metadata
and registry evidence are retained as a 14-day workflow artifact. A fresh runner
then pulls the complete image **by digest** and checks dependencies, imports,
native file/build receipts and absence of weights, runtime credentials and SSH
host keys. It runs with networking disabled and does not start services or claim
GPU acceptance. New GHCR packages can be private; authenticated CI pull success
does not establish anonymous Vast pull access.

The first run is [42115360524](https://github.com/ziomalskit/ziomalskit/actions/runs/42115360524).
The resource and verification update is
[`f728933368d0a2dd36a06c5d2b2fe8a3b8f52c9b`](https://github.com/ziomalskit/ziomalskit/commit/f728933368d0a2dd36a06c5d2b2fe8a3b8f52c9b),
with [run 42116392351](https://github.com/ziomalskit/ziomalskit/actions/runs/42116392351).
Accept an image only when the complete image workflow, including registry
verification and fresh-runner smoke, succeeds. Copy its immutable `image` from
the `registry.json` artifact or the build summary; the published reference is also
reported in [PR #10](https://github.com/ziomalskit/ziomalskit/pull/10).
Local setup passed; 32 Docker/Sage/registry tests and 34 deployment,
dependency and fallback-node tests passed. The full local CPU audit passed
**1,053 tests in 578.390 seconds**, with no failures, errors or skips and all seven
historical baseline defects still detected. Dockerfile checks and workflow lint
also passed. These checks do not replace a complete image build.

[PR CPU CI 42116401555](https://github.com/ziomalskit/ziomalskit/actions/runs/42116401555)
also passed: setup, 1,053 tests (653.650 seconds), 16 HTTP smoke checks, and 1,053
audit tests (645.785 seconds), with no skipped tests. Its generated merge commit
is `0d679f4` (head `f728933368d0a2dd36a06c5d2b2fe8a3b8f52c9b` into the unchanged
requested base).

### Actual first image build and dependency-snapshot correction

Run 42115360524 failed after **successfully compiling both llama.cpp binaries and
the SageAttention 2.2.0 CUDA wheel** against the pinned CUDA 13 / torch 2.11 stack.
The failure was the post-build dependency guard: importing setuptools through
torch's C++ extension helper appended `setuptools/_vendor` to `sys.path`, and the
last-wins distribution snapshot incorrectly reported four vendored versions as
installed dependency changes (`packaging`, `typing-extensions`,
`importlib-metadata`, `zipp`). No replacement of those installed packages occurred.
An isolated environment with the actual setuptools 78.1.0 reproduces all four
false changes without CUDA.

Commit `e83f5852942dea8873e3edb364594b173a998841` preserves the first visible
distribution, matching `importlib.metadata.version()`. A real metadata-directory
regression covers the vendored path appearing and still rejects actual installed
version drift. All 33 Docker/Sage/registry tests pass, as does the corrected guard
against a real setuptools import. No package pin or dependency-mutation guard is
removed. The obsolete resource-only run 42116392351 was cancelled; the replacement
is [42121151372](https://github.com/ziomalskit/ziomalskit/actions/runs/42121151372).
Branch-scoped concurrency now cancels obsolete builds when a fix is pushed.

The first runner measured a 145 GiB root filesystem: 86 GiB initially free,
116 GiB after SDK cleanup, and 96 GiB still free at failure. It had 15 GiB RAM and
3 GiB existing swap. This was not an out-of-disk or compiler failure. The revised
workflow retains one native build worker and swap protection for memory spikes.
**No complete image or GHCR digest was produced by the first run.**

The corrected commit's [PR CPU CI 42121160693](https://github.com/ziomalskit/ziomalskit/actions/runs/42121160693)
passed setup, **1,054 tests** (520.173 seconds), **16 HTTP smoke checks**, and
**1,054 audit tests** (515.532 seconds), with no failures, errors or skips. The
seven historical baseline defects were still reproduced. CI checked generated
merge `8e4cfce`, with `e83f5852942dea8873e3edb364594b173a998841` merged into the
unchanged base; neither PR was merged on GitHub. A real public GHCR protocol probe
also verified manifest/config SHA256s and layer HEAD sizes; that probe is not AJ
publication evidence.

| Proof | Result |
| --- | --- |
| CPU setup/tests/audit | **PASS**; corrected-source CI: 1,054 tests, 16 HTTP smoke checks, 1,054 audit tests |
| Local full CUDA image build | **NOT RUN**; development workspace is too small |
| Complete image / registry digest / measured image size | [Image CI gate](https://github.com/ziomalskit/ziomalskit/actions/runs/42121151372): require success, registry artifact and fresh-pull summary |
| Clean native CUDA compilation with torch 2.11 / CUDA 13 | **PASS** in first CI run; complete artifact still requires the image CI gate |
| Real RTX PRO 6000 KJ SageAttention + llama/model load | **PENDING** |
| One H3 Full + one 10Eros Full manual 5s render, MP4 | **PENDING** |
| Vast ENTRYPOINT/SSH/disk/host integration | **PENDING** |

CI performs model-free image publication, registry verification and CPU image
smoke only. No GPU rental, production model download, render submission, Vast
lifecycle call or PR merge is part of that workflow. CPU PASS is not end-to-end
GPU readiness.

## Initial CPU-only verification — 2026-11-05, before image CI

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
`nvcc` and `nvidia-smi` were unavailable in that local workspace. That initial
CPU-only record did not prove a full image build or CUDA compilation; the
subsequent CI record above covers those gates. Real KJ kernel execution, model
loading, Full videos and Vast integration remain PENDING.

An early focused run exposed a temporary subshell that broke the service launch
controller-parent relationship (four service test failures). It was removed
before the successful full runs; pidfd/ownership checks were not weakened. An
initial new-test fixture setup error was also corrected before the 28-case PASS.
