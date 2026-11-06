# AJ functional end-to-end readiness — 6 November 2026

Issue: [#11](https://github.com/ziomalskit/ziomalskit/issues/11).
Fix and regression review: [Draft PR #12](https://github.com/ziomalskit/ziomalskit/pull/12).
Download [evidence and reproduction scripts](evidence/FUNCTIONAL_E2E_EVIDENCE.zip).
Review starts at PR #10, `d5b71fa4ca3dd085abf570ad369cac4007226d1d`, including
PR #8 at `ef84f8e3066e3d14beeba6bb4e87393d675ef6f1`.

**Recommendation: conditional GO for an explicitly approved, init-only GPU
trial using the existing verified digest; NO-GO for Full rendering or
Autoprompt today.** Init must stop before `models`. The corrected panel requires
a separately approved image build/publication and a fresh complete pull/smoke
before testing its UI on GPU. No rental or publication is authorized by this report.

The offline application integration works through the exercised boundaries.
One confirmed functional gap was fixed: the first-run instructions require an
explicit render seed, but the panel had no seed input and omitted `seed` from
its request. The fix adds an optional seed, preserving automatic selection when
blank, including seed zero and freezing its value before uploads. No backend,
workflow, profile, model, LoRA registration or user-facing default was changed.

GPU inference, model compatibility/quality, actual ComfyUI schemas, target-host
SSH, VRAM and provider behavior remain unproven. Synthetic completion and a
playable synthetic MP4 do not establish a successful Full render.

## Evidence boundaries and execution

The new `tests/test_functional_e2e.py` journeys use authenticated FastAPI requests,
multipart uploads, real private file publication, real queue snapshots and
idempotency, actual controller functions/workers, adapters, model integrity
checks, real loopback TCP HTTP servers, real `/prompt` requests and UUIDs,
history reconciliation, and the actual streaming proxy. Every backend POST
checks the queue file for its already-durable `armed` UUID.

Only unavailable execution is simulated: tiny inert artifacts have their own
valid checksums and receipts in a disposable controller copy; fake ComfyUI
serves the existing CPU schema fixture and synthetic history/model output.
Production manifests on disk are unchanged. These files are not production
weights or LLM evidence. The fake backend does not execute the graph or run
ComfyUI's native input validation/lazy execution.

Two modes are distinguished:

* The default regression suite replaces the CLI preview/validation/watch boundary
  with the existing SHA-verified upstream converter fixture, AJ's actual API
  validator and an immediate watcher result. HTTP, dispatch, persistence and
  history remain real. This is controller/adapter integration, not full CLI proof.
* The independent review sets `AJ_E2E_COMFY_PYTHON` to an installed **comfy-cli
  1.21.0** interpreter. It runs AJ's unchanged preview, schema validation and
  watcher subprocesses. No preparation, dispatch, file check, history function
  or watcher is mocked in this mode. A startup shim redirects only CLI configuration
  into `work/` and disables telemetry; it changes no conversion/execution logic.
  Fixture jobs have already completed when the CLI watcher attaches, so its
  history path is exercised; live WebSocket progress is not proven by these journeys.

The independent CLI interpreter was Python 3.12.14, FastAPI 0.142.2, httpx 0.28.1,
Pydantic 2.13.4, uvicorn 0.42.0. It is a CPU review environment, not a recreation
of every dependency in the published image. The normal regression uses the
repository's locked development environment.

Recorded evidence (included with the review's downloadable evidence bundle):

| Evidence | Command / method | Observed result |
| --- | --- | --- |
| `real-cli-traced.log`, `traces/*.json` | `AJ_E2E_COMFY_PYTHON=... python -m unittest tests.test_functional_e2e -v` | **8 PASS**, 122.126 s; real CLI + TCP; request paths, UUIDs, model routing, durations and durable-arm checks recorded |
| `failure-injection-final.log` | Re-run the two failure-injection journeys after adding endpoint-unavailable and interrupted-stage cases | **2 PASS**, 14.978 s; trace files updated for these expanded cases |
| `browser.log`, `browser.py`, `browser.js` | Real Chromium → uvicorn panel → real render worker → real CLI → fake TCP backend | Both profiles completed with seed 12345; six multipart images each; zero Prompt Worker calls; 2 OPEN MP4 links; HTTP 206; browser decoded 64×64, 1 s synthetic H.264 MP4; no page errors |
| `seed-before.log` / `seed-final.log` | Execute actual inline panel JS with existing frontend harness | Seed regression **FAIL before fix**; **17 PASS after fix**, 2.623 s, including existing reload/retry behavior |
| `deployment.log`, `deployment.py` | Empty disposable workspace; real Docker bootstrap/health/stage controller; hardware/native dependencies and service commands simulated only after actual host refusal | Actual undersized host refused before token/download/service calls; tiny real downloader/provisioner transaction succeeded only at explicit `models`; stale boot/model rejected |
| `entrypoint-case/entrypoint.log`, `entrypoint-namespace.sh` | Unmodified entrypoint in isolated user/mount/network namespaces | Fresh keys + boot ID and listening sshd; login **FAIL** at rootless `setgroups`; daemon cleaned up |
| `boundaries.log` | Focused existing Docker, native completion receipt, writer provisioning, Sage and process compatibility suites | **52 PASS**, 4.453 s; hardware/native executables simulated where stated by their fixtures |
| `panel.log` | `make panel`; `.venv/bin/python scripts/dev_panel.py check`; `make stop` | Authenticated HTML/configuration PASS; only owned panel started/stopped |
| `setup.log`, `full-test.log` | `make setup`; `make test` | See final validation below; full regression is supporting evidence, not the end-to-end conclusion |

## Functional matrix

Severity describes the consequence of a failure or the remaining gap: P0 would
mean unbounded paid work/data loss, P1 blocks a reliable render/deployment, P2
is a bounded operator/UX problem. PASS on an injected failure means the intended
rejection or recovery was observed.

| User flow / boundary | Type and expected behavior | Observed evidence | Status | Severity / reproducer |
| --- | --- | --- | --- | --- |
| Published image versus requested head | Exact source diff; later head should change docs only | `git diff --name-only e83f585..d5b71fa` lists only `docs/VAST_DOCKER_BUILD.md` and `docs/VAST_DOCKER_FIRST_RUN.md`; original `h3/` trees match | **PASS** | P1; exact refs above |
| Empty workspace bootstrap | Real code; immutable links and real mutable model/input/output directories; no boot inference | Docker journey stages package and one model store; initial host refusal leaves no runtime.env or ready receipt | **PASS** | P1; `deployment.py`, Docker tests |
| Pre-model init refusal | Real host probes before any transfer | Runner has 8 GiB cgroup memory, 2 effective CPUs; rejects “Need at least 96 GiB available RAM and 8 effective CPUs”; zero downstream commands | **PASS** | P1; `deployment.log` |
| SSH initialization | Unmodified entrypoint; key-only SSH, fresh per-instance host keys/boot ID | 3 host keys generated, boot ID created, sshd listens; no AJ/model/service/provider launch | **PASS** | P1; namespace script/log |
| Rootless SSH login | Real ssh client/server; successful private command | Connection reset; `setgroups: Operation not permitted [preauth]` in this single-UID user namespace | **FAIL — environment** | P1 on this layout; use the supported rootful template, verify actual Vast SSH before proceeding |
| Explicit models/start stages | Real stage control, real tiny-file downloader/hash/publication; hardware/native checks and service/preflight/smoke commands stubbed | Init alone cannot start services; explicit `models` writes ready receipt; start checks identity, orders service start/panel restart/preflight/smoke and restores submissions OFF | **PASS — staged** | P1; `deployment.py`; actual service startup acceptance remains below |
| Six images, optional audio, profile and settings | Real multipart/API/controller code | Browser sends six real PNGs; controller journeys also use a valid WAV and the no-audio silence path; request retry keeps identical durable jobs | **PASS** | P1; manual/browser and controls journeys |
| Explicit seed from panel | Real panel JS and browser; operator can set the documented seed | Missing input/request field at baseline; fixed optional field sends exact 12345/0, rejects unsafe integers before uploads, freezes during upload; blank still omits seed | **FAIL at baseline → PASS in source** | P2; `ManualPromptFrontendTests.test_seed` / `test_invalid_seed`; published image still has old UI |
| Manual H3 → 10Eros → H3 | Real API/CLI/dispatch/history/proxy; one render each, no LLM | 3 unique Render POSTs, zero Prompt Worker requests; exact manual text retained; completed jobs survive reload | **PASS** | P1; `test_manual_profile_switches_upload_snapshot_dispatch_result_and_range` |
| Browser → MP4 | Real browser/HTTP/worker/CLI/proxy; completed result opens and supports seeking | 2 completed profiles, real 206 responses, synthetic MP4 metadata decoded; no browser errors | **PASS — synthetic media** | P1; `browser.log`; real Full video remains NOT RUN |
| Autoprompt auto/review contract | Real controller/CLI/TCP; ten candidates, five automatic renders per profile | 20 prompt submissions across both profiles; 10 auto renders + 2 explicitly approved review renders; approval retry idempotent; remaining 8 reviews rejected | **PASS — simulated execution** | P1; `test_autoprompt_both_profiles_cache_auto_five_and_review_approval` |
| Step 0 shared; Step 1/2 independent | Submitted graph inspection + durable analysis cache | H3 cold graph 413 nodes; next candidate 97 nodes; switching to 10Eros reuses Step 0, retaining Q6_K Mistral Step 1/2 (336 nodes), then writer-only 97-node graph | **PASS — routing/cache** | P1; same journey trace; model behavior NOT RUN |
| Step 3/4 writer policy | Real graph guards and pinned CLI validation | H3 routes Qwen3-14B Q4_K_M; 10Eros routes Qwen3.5-27B Q5_K_M; Step 3 OFF/0, 8192 max/24576 ctx; Step 4 ON, budget 6144/8192, max 14400/16448, ctx 32768/36864 | **PASS — contract** | P1; controls journey + trace; inference NOT RUN |
| Natural completion / no silent truncation | Real AJ node adapters and local fake llama-server children; frozen upstream native preparation | Missing EOS, length/context truncation, wrong channels, empty/reasoning-only output rejected; timeout reaps child/releases lease; no inference evidence claimed | **PASS — boundary fixtures** | P1; `tests.test_prompt_completion_receipts` in `boundaries.log` |
| Interrupted analysis and malformed response | Real controller completion/cache handling; only model output synthetic | Bad final/timecodes become prompt_failed, never renderable. Interrupted Step 0 result retained alone; next candidate reuses Step 0 and still executes independent Step 1/2 | **PASS** | P1; final failure-injection run |
| v20 identity and timing | Exact bytes + submitted graph; align locks/frame counts | Canonical 3,084,071 bytes, SHA256 `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222` unchanged. Requested 5 s → 124 frames → 5.16 s effective; requested 20 s → 481 frames → 20.04 s | **PASS — contract** | P1; manual/auto traces; synthetic video's 1 s duration is not render-timing proof |
| Full model/encoder/VAE and LoRAs | Real adapter and integrity gates; tiny artifact fixtures | Exact profile checkpoints and shared encoders/VAEs in submitted graphs; default H3 bridge/LoRAs preserved; explicit first-pass .5 / second-pass .75 reach correct stacks; explicit disabled rows stay off | **PASS — routing/integrity** | P1; controls/manual traces; compatibility remains GPU-pending |
| Missing, changed or unverified model | Real file/receipt checks before POST | Deleted model, wrong bytes and removed hash receipt each reject before any Render POST | **PASS** | P1; `test_missing_and_changed_model_receipts_block_before_submission` |
| HTTP rejection / unavailable protocol | Real TCP 400 and 503 injection | No successful submission after 400; terminal render_failed; unavailable protocol blocks before `/prompt`; no automatic paid retries | **PASS** | P1; final rejection/unknown-state journey |
| Lost acknowledgement / service-history loss | Real TCP disconnect after accepted POST, then controller reload/recovery | Known history settles original UUID with one POST. Empty history/queue/unknown receipt after simulated service reset remains recovery_render, never auto-resubmits | **PASS** | P0 guard; lost-ack and unknown-state journeys |
| Queue corruption | Real disk/HTTP | Invalid JSON preserved; new batch gets 503; no overwrite or replacement queue | **PASS** | P0 guard; unknown-state journey |
| STOP / drain / restart | Real lifecycle HTTP + real prompt/render worker loops; provider worker not launched | STOP blocks picks; controller reload turns armed plan into action_interrupted; explicit cancel/re-arm drains 6 renders + 5 prompts, skips 5 unstarted reviews, preserves completed state | **PASS — control plane** | P0 guard; `test_real_workers_stop_drain_restart_and_durable_auto_five` |
| Low RAM/CPU/disk, old driver, wrong CUDA/native state | Actual host resource refusal plus targeted hardware fixtures | Early resource/driver/capability refusal and wrong CUDA/ABI/missing native receipts reject before kernel promotion/transfer; actual CUDA verification not executed | **PASS — failure gates** | P1; deployment + Docker/Sage boundary suites |
| Real GPU, real object_info, lazy graph execution | Target hardware/real ComfyUI | Not available; a fixture catalog cannot establish upstream node/model load compatibility | **NOT RUN** | P1; mandatory target gate |
| Actual Full MP4 / VRAM / performance / overlap | Production models on RTX PRO 6000 | No weights, inference, actual 5 s videos, VRAM benchmark or real overlap | **NOT RUN** | P1; mandatory render gate |
| Live Vast template/tunnel/restart/STOP/DESTROY | Actual provider | No resource rented, API credentials used or lifecycle action executed | **NOT RUN** | P1; explicit operator approval and instance evidence required |

## Image provenance and deployment gaps

Existing artifact:

```text
ghcr.io/ziomalskit/aj-vast@sha256:d8bb2e7b6b567ab1f123ab9b2721b3fa91e8d0f60567a6a2f5013ffb6357e7a1
source: e83f5852942dea8873e3edb364594b173a998841
```

Independently retrieved [CI run 42121151372](https://github.com/ziomalskit/ziomalskit/actions/runs/42121151372)
job results: build **success**, separate fresh-runner smoke **success**. Retrieved
smoke logs show the exact digest fully downloaded, **16,050,426,172 uncompressed
bytes**, `No broken requirements found`, and `Complete image CPU smoke PASS;
GPU kernels, model loads and renders PENDING`. This is reviewed CI evidence,
not a fresh pull on this runner. The build artifact is 54,267 bytes, unexpired
at review, source SHA matches; the connector returned a downloadable archive.
The archive contents were not separately unpacked here. No new registry
verification, image build, push or publication was performed.

The source smoke checks dependency receipts, imports, native checksums, pinned
nodes, model-free plan and absence of baked credentials/host keys. Actual GPU
receipts remain pending. The local 7.5 GiB free filesystem cannot hold the full
16.05 GB extracted image, so local pull/build is **NOT RUN**, with a resource
reason rather than a fabricated pass.

Operational actions still required:

1. Use the exact ENTRYPOINT template, mapped SSH port 22 and an SSH public key.
   The image requires container UID 0 with working sshd privilege separation; this review's rootless single-UID
   namespace cannot complete privilege separation. Verify a real private SSH
   command and loopback panel tunnel before transfers.
2. Confirm **available** RAM ≥96 GiB and ≥8 effective cgroup CPU cores, not just
   advertised host totals. This runner had only 2 effective CPUs despite its
   broader host affinity. A nominal 96 GB RAM allocation may fail the available
   RAM requirement. Confirm cgroup v2, NVIDIA Container Toolkit, one sm_120
   RTX PRO 6000, compatible pidfd control and driver ≥580.65.06 for CUDA 13.
3. Measure the actual `/workspace` filesystem after extraction. The default 19
   files total 278,009,016,136 bytes; with the 32 GiB reserve, a fresh store needs
   at least **312,368,754,504 free bytes** at this gate. A nominal 350 GB offer
   is conditional on image/writable-layer accounting and actual free capacity.
4. Init does not transfer weights or start workers. `models` is the explicit
   transfer boundary; interruption preserves source-bound partials. Restart
   requires re-init/models, compatible image/store identity and explicit start.
   If start's later preflight fails, started services need an owned stop via
   service_ctl; it is not an atomic rollback of all startup side effects.
5. Docker provider telemetry/lifecycle uses `/bin/false`. The panel cost guard
   is not a provider spending cap and can hold dispatch when cost is unknown.
   Supervise the bill externally; do not arm telemetry-dependent automation.
   Ephemeral `/workspace` is not keep-data proof. No keep-data destroy approval
   follows from this review.
6. `completed` means settled successful history with a video descriptor; it
   does not independently certify container/codec integrity or motion quality.
   The injected missing descriptor is rejected. Real zero/truncated/unplayable
   media and actual model-generated timing/quality still need the live
   download/playback gate. This is a known evidence limit, not a claim of a
   newly reproduced production file corruption defect.

## Remaining blockers and trial decision

**Init-only trial: conditional GO**, using the already verified immutable
digest, only after explicit rental approval and review of the real offer/cost,
private SSH access and allocation. Run only `aj-docker init`; record image,
driver/toolkit/torch/ABI, visible allocation/disk and actual KJ MiniMax Sage
kernel result. Stop there. A failed health gate is a host/runtime blocker;
do not repair it by transferring models, changing the Full stack or falling
back to different attention/model settings. Init success proves neither Full
weights nor the Full graph.

**Corrected-source Full render trial: NO-GO now.** P1 prerequisites:

* Build/publish a new immutable image from the reviewed seed fix only with
  explicit publication approval, then complete fresh-runner full pull/smoke.
  The existing digest never gains the changed HTML. Do not relabel it as fixed.
* On the target, pass init, deliberately approve the 278 GB transfer, then verify
  model integrity, both actual workers and their real object_info, preflight
  and non-submitting conversion smoke. No offline fixture resolves these gates.
* Only after those gates and explicit render approval, submit one H3 Full
  **manual_final**, batches 1, requested 5 s, explicit seed; verify one POST,
  playable and downloaded MP4, effective duration, motion/audio, logs and peak
  VRAM. Then repeat one 10Eros Full manual job. Keep production defaults.

**Autoprompt: NO-GO until a separate real prompt-only Step 0–4 probe passes**,
including the current 27B writer probe for 10Eros, real natural EOS/channel
receipts, temporal guards, cache reuse and measured memory. A normal batch
schedules **five** renders; it must not be used for prompt-only acceptance or
the first inexpensive render. Native lease behavior under real CUDA load,
service restart during GPU work, VRAM overlap and live provider lifecycle remain
separate later gates. There was no new reproduced P0 backend defect in this review.

## Reproduction and change scope

Normal targeted regression, with repository development dependencies:

```bash
make setup
.venv/bin/python -m unittest tests.test_functional_e2e tests.test_manual_prompt_frontend tests.test_frontend_retry -v
.venv/bin/python -m unittest tests.test_docker_deployment tests.test_prompt_completion_receipts tests.test_writer_manifest tests.test_sageattention_runtime tests.test_process_compatibility -v
```

For the actual CLI mode, install comfy-cli 1.21.0 plus the panel dependencies in
a disposable interpreter, copy the evidence bundle's `python-startup/` and CLI
config into `work/functional-e2e/`, and run from the repository root:

```bash
AJ_E2E_COMFY_PYTHON="$PWD/work/functional-e2e/venv/bin/python" \
AJ_E2E_CLI_CONFIG="$PWD/work/functional-e2e/cli-config" \
AJ_E2E_TRACE_DIR="$PWD/work/functional-e2e/traces" \
PYTHONPATH="$PWD/work/functional-e2e/python-startup:$PWD" \
work/functional-e2e/venv/bin/python -m unittest tests.test_functional_e2e -v
```

The bundle also contains browser/deployment/entrypoint reproduction scripts;
browser execution requires Playwright and `/usr/bin/chromium`. The namespace
script requires user/mount/network namespace support and stages SSH keys only
inside its disposable directories. Never run it against real home/SSH paths.

Only production edit: `h3/static/index.html` (six added lines). Added regression
journeys and two frontend seed cases. `git diff` against PR #10 confirms
`migration/`, `archive/`, `h3/app/`, `h3/config/`, `h3/custom_nodes/` and
`h3/workflows/` are byte-for-byte unchanged. No checksum baseline was updated.
The historical preserved-file tests remain intact. The fix therefore does not
change queued backend/workflow identities.

Final validation: `make setup` **PASS**; `make test` **1,064 PASS** in 667.643 s
plus **16 HTTP smoke checks PASS**, zero failures/errors/skips. The final
browser-input validity guard was additionally checked by the **17 PASS**
frontend/retry run and the repeated Chromium journey; the expanded failure
fixtures also passed in both CLI modes. `git diff --check` and preserved-tree
comparison **PASS**. No additional general security audit is used as
end-to-end proof.

No GPU rental, paid runner/service, production model transfer, actual model
inference, image publication, provider lifecycle operation or merge was performed.
PR #8 and PR #10 remain Draft/open/unmerged; fixes are isolated from both heads.
