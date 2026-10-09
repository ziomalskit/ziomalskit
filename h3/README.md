# AJ / H3 Full production migration

Current AJ/H3 runtime for CPU development and the eventual consolidated Vast
acceptance session. Live GPU/Vast acceptance is still pending. Follow
[`docs/ROADMAP.md`](../docs/ROADMAP.md): finish the feature set, run the full CPU
regression/audit, close the remaining 9B provenance blocker, then perform one
consolidated GPU/Vast session. [Migration evidence and self-review](../docs/MODEL_MIGRATION_2026-10-09.md)
records what is verified and what still blocks readiness.

Target:
- RTX PRO 6000 Blackwell 96 GB
- CUDA devel image, CUDA 13 preferred (>=12.8 required)
- expose only panel port 7860
- ComfyUI 0.38.0 (pinned to the known-good local workflow environment)
- comfy-cli 1.21.0
- Python >=3.11 (development checks use Python 3.12)
- Linux >=6.9 with pidfd process-group signalling for service/lifecycle control

Conservative first-release input contract:
- exactly 6 images: Picture 1 + 5 supporting references
- 0 or 1 audio reference
- exactly two normal production profiles: H3 Full (default) and 10Eros Full
- exact v20 Heretic prompt baseline, shared Step 0–2 and profile-specific Step 3/4
- legacy INT8 graphs are explicit diagnostic artifacts only

If preserving data, set `H3_PERSISTENCE_MODE=volume` and
`H3_PERSISTENT_ROOT=<actual mounted Local Volume path>` BEFORE installation.

Keep-data destroy also requires `H3_PERSISTENT_VOLUME_PROOF`, an administrator
attestation created **after verifying the actual retained Local Volume attachment
in Vast**. A directory, a filesystem type, or a different device is not evidence
of retention. If the attachment cannot be verified, leave destroy disabled.
The private JSON file (owner root or the panel user; no group/other write access)
must contain `provider: "vast-local-volume"`, the positive integer `volume_id`,
`retained_on_instance_destroy: true`, and `mount` containing the exact `source`,
`target`, `fstype`, `fsroot`, `uuid` and `maj:min` fields returned by
`findmnt -T "$H3_PERSISTENT_ROOT" -J -o SOURCE,TARGET,FSTYPE,FSROOT,UUID,MAJ:MIN`.
It must also bind the verified attachment to the current `instance_id` (string),
host `boot_id` from `/proc/sys/kernel/random/boot_id`, and `root_identity` containing
the persistent root's `[st_dev, st_ino]`. An old or mismatched attestation fails closed.
The filesystem must be ext4, XFS, Btrfs or ZFS, separate from the container root;
all protected paths must exist on that exact mount. Reverify the provider
attachment and replace the attestation when mounting a different volume.
Never create this attestation merely because the local mount looks persistent.

Provisioning stages and validates the complete deployment next to `PANEL_ROOT`,
then uses an atomic directory exchange. Retired `.release-*` directories are
retained because a live controller can still use their cwd, and their original
`state/` and `runtime.env` can be referenced by the current release. Do not delete
them during provisioning or while those mutable paths remain referenced.

Run:
```bash
bash INSTALL_ON_VAST.sh
```

The installer downloads/verifies the pinned stack and builds the 9B BF16 GGUF
when its complete recipe is frozen. It currently stops before model downloads
because 9B source metadata is incomplete. After that blocker is closed:
```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Do not start a real H3 render until both pass.

Provisioning leaves `H3_ALLOW_SUBMISSIONS=0`, including on an existing deployment,
so it cannot resume queued paid renders or Vast lifecycle actions automatically.
After preflight, the operator deliberately enables submissions for the controlled
acceptance session. Keep inference/lifecycle controls disabled during setup.

## Profiles, prompts and LoRAs

Choose H3 Full or 10Eros Full and optional registered LoRAs. The UI loads display
names, ranges and defaults from `/api/config`; it submits canonical IDs only.
H3 defaults retain HM Breasts 1.0, MysticXXX 0.6 and Movement 0.5. 10Eros starts
with optional LoRAs off. All Full GPU compatibility and strengths remain pending.
Sixteen dynamic slots support future registered LoRAs; unused slots are disabled.

Jobs freeze their profile, core model route, LoRA selection and Bunny bridge
settings at creation. Changing runtime defaults cannot modify queued work.
Recovery reconciles the original UUID/profile; it never guesses from a loaded
model. Pre-migration jobs without profiles require explicit diagnostic mode or
recreation for new production dispatch. Existing remote UUIDs are still reconciled.

`H3_v20_heretic_MASTER_DURATION_EXACT.json` is byte exact (3,084,071 bytes;
SHA256 `276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222`).
Two small render descriptors compose it without duplicated prompt instructions.
Duration remains synchronized through v20's Generation Settings and both duration
locks. Seeds remain Step 0 stable, Step 1 analysis seed, Step 2 +1, Step 3 candidate
seed and Step 4 +1. Writer/temperature/output separation remains intact.

Normal compilation uses each profile's writer for both Step 3 and Step 4, without
a vision projector. Step 3 thinking is OFF; Step 4 thinking is ON with the pinned
CLI's native 4,096-token reasoning budget. Step 4 allows 12,352 generated tokens
(8,192 final + 4,096 reasoning + 64 framing) and 29,184 context tokens, retaining
the previous input allowance. These settings and runtime pins are frozen in each
job; restart/default changes cannot change queued settings. The Step 4 adapter
reads the CLI's native output file and forwards only its validated final channel.
Actual writer quality and truncation remain model acceptance benchmarks.
`compiler: shared_gemma` is a server-configured
acceptance benchmark only; it retains v20 instructions and raises overlap headroom.
It is not a third production profile or a normal UI choice.

Absolute timeline arithmetic belongs to AJ. The unchanged v20 frame expressions
derive and persist requested duration, legal frames and effective duration. A
20-second request becomes 481 frames and the v20 duration length **20.040 s**
(481/24 is approximately 20.041667 s; the centisecond floor is v20's existing
behavior). Runtime composition feeds effective duration to all three duration
locks. `AJCreativeTimelineGuard` treats proposed shot lengths as weights, assigns
contiguous positive integer-millisecond intervals, and ends exactly at the target.
`AJFinalPromptTimeGuard` binds final cut timestamps to that plan. Only Timeline
fields and cut timecodes change; action prose and v20 instructions stay intact.
The controller validates both again before review/automatic render, approval,
recovery and render preparation. Older jobs missing these identities require
explicit reconciliation/recreation; no new settings are silently applied.

## Phase boundaries and shared analysis

The render graph computes both conditioning branches before its late transformer
loader. An AJ node releases ComfyUI-managed encoder models at that boundary,
then sampling gets VRAM priority. Another boundary releases resources after video
creation. Render `/free` clears the idle renderer at job/model boundaries; it
does not touch Prompt ComfyUI. No per-sampling-step CPU/GPU streaming was added.

H3 Full defers cold Step 0–2 during sampling. Valid saved Step 0–2 text lets the
smaller 4B writer overlap with measured headroom. 10Eros's balanced policy also
permits analysis when measured headroom is sufficient. Conditioning, decoding,
unknown phase and recovery defer prompt work. Thresholds in
`config/production_profiles.json` are conservative, adjustable acceptance inputs.
The 66 GB transformer and 51 GB encoder must not be assumed simultaneously resident.

Step 0–2 text is shared per batch, independent of render profile/candidate seed.
Prompt ComfyUI exposes a process UUID epoch. Restart invalidates assumed live
cache residency, while verified durable text can feed Step 3/4 directly.
The pinned LLM node launches/reaps a separate llama-cli process per stage;
an epoch never claims that those model weights remain resident.

## Persistent model integrity and downloads

Weights, LoRAs, GGUF source/build files, receipts and generated provenance live
under `$COMFY_ROOT/models` on the retained volume. Controller state/input/output
retain the existing persistent deployment layout. Code updates do not change
artifact identities or force verified weights to download again.

`config/models_manifest.json` has shared/profile artifacts and a link to the
canonical LoRA registry. Each downloaded file has a pinned commit, exact bytes
and SHA256. The 13 required core artifacts total **219,551,087,416 bytes**;
all 17 pinned downloads total **221,692,946,200 bytes**, before 9B source/output,
external LoRAs and staging. `provision_models.py --plan` reports disk requirements
and fails when known required storage exceeds available space.

Existing correct bytes are rehashed and reused. Wrong size/hash, symlinks and
untrusted same-name generated files fail closed. HF/Xet downloads resume in
isolated staging; partial files never become runtime models. Generated receipts
bind all source files, toolchain/converter pins, command, output size and SHA256.
Completed build journals recover publication crashes without a second conversion.

Pass `HF_TOKEN` only through the provisioning environment. The separate download
venv uses pinned modern `huggingface-hub` + `hf-xet`; optional
`HF_XET_HIGH_PERFORMANCE=1` is inherited. No login/token-save operation is used.
Download tokens never reach `runtime.env`, model provenance, controller state,
worker environments, Diagnostics or SDK/native output. Verified startup/reuse
requires no download token.

HM Breasts, HM NSFW AIO, Turbo 8-step and Bunny have verified HF provenance.
MysticXXX, Movement, MPOV and Combat remain external/user-provided; put only the
registered files under persistent `models/loras`, then explicitly enroll IDs:

```bash
<COMFY_PYTHON> <PANEL_ROOT>/scripts/enroll_external_loras.py \
  --models-root <COMFY_ROOT>/models --registry <PANEL_ROOT>/config/lora_registry.json \
  mysticxxx-v4 movement-v1
```

Enrollment records actual local bytes, not upstream identity or GPU compatibility.
Changed enrolled bytes fail verification. Bunny bundled copies are verified too,
because the pinned node prefers bundled files over `models/semantic_bridge`.

## Ref2Video results

Normal flow stays **references + parameters -> job -> final MP4 link**.
Queue / Results prioritizes status, render time when known and **OPEN MP4**.
Batch, candidate, controller job ID and seed remain secondary metadata.
There is no media gallery or library.

`GET /api/jobs` and `GET /api/jobs/{id}` expose canonical `video_outputs` for
completed renders, plus `render_started_at`, `finished_at` (Unix seconds) and
`render_duration_seconds` when valid timestamps exist. Duration measures the
controller's render phase, from render preparation through observed settled
history; it is not a GPU kernel benchmark. Recovery retains the original start;
an older job may recover a start from ComfyUI's `execution_start` timestamp.
Otherwise duration is null, including missing timestamps or backwards clocks.
Legacy completed results are normalized on reads without rewriting queue state.

Links keep the authenticated Render proxy (`/api/proxy/render/view`) and its
HTTP Range / streaming behavior. Only settled successful history with a final
video proves completion; preview output, missing history or uncertain submission
still cannot prove success or trigger an automatic paid resubmission.

## Bounded Parallel prompt prefetch

```dotenv
H3_PROMPT_PREFETCH=3
```

Parallel is the only scheduling mode: one Render Worker on `8188` and one Prompt
Worker on `8189` may overlap on the same GPU. There is no global GPU mutex.
The target is a future-render buffer, excluding the active render. Integers clamp
to **1–5**; missing, empty or non-integer settings fall back to **3**. Configure
through runtime environment, then restart the controller. `/api/config` returns
the active integer as `prompt_prefetch`; Diagnostics shows ready/preparing/target.

The one active prompt reserves a slot. Waiting automatic and approved-review
renders count toward the buffer. Existing approved work may exceed the target;
prefetch pauses until the renderer consumes it. Unapproved `pending_review`
decisions do not occupy render capacity: otherwise five reviews could never
finish with a smaller buffer and could block later automatic batches.

All queued automatic candidates #1–5 outrank unstarted review candidates #6–10,
across batches. Review prompts are prepared when automatic work is exhausted and
buffer capacity permits; all five eventually become review decisions for a finite
queue. Approved reviews retain their existing top render priority. STOP AFTER
CURRENT starts no more work. Queue draining finishes automatic/approved work,
skips unsubmitted review preparation and ignores pending/rejected reviews.
Recovery/uncertain execution, persistence and lifecycle fences still apply per
service. A render can never run alongside another H3 render.

## Advanced diagnostics and live logs

The collapsed **Advanced** section contains Diagnostics, Live Logs, service
restart controls and, only when enabled, Terminal. Existing Vast lifecycle,
guard, telemetry and destructive confirmation behavior is preserved.

`GET /api/diagnostics` is read-only. It checks AJ/controller tasks and persistence,
both ComfyUI `/system_stats` endpoints, GPU/VRAM when available, retained-volume
proof, disk free space, queue summary, prefetch and local Vast CLI/control
availability. It never submits, restarts services, writes state or invokes Vast.
Each independent probe has a short deadline (at most two seconds) and runs
concurrently. A dead service produces FAIL while other checks still return.
Vast remote authorization is explicitly **not probed**.

Model diagnostics report missing, wrong size/hash/provenance, hash verification
required or verified file. Fast read-only reports use private verification receipts
bound to file identity/size/mtime/ctime; explicit preflight performs full hashing.
Verified file is separate from GPU validated. Memory/overlap policy and cache epochs
appear in Advanced; opening Diagnostics never downloads or builds models.

Diagnostics and logs refresh manually when their sections are open. Logs use
`GET /api/logs/{service}?lines=200`, with only `render`, `prompt` and `panel`:

| Source | Existing `service_ctl.sh` file |
| --- | --- |
| Render ComfyUI | `$WORKSPACE/comfyui-render.log` |
| Prompt ComfyUI | `$WORKSPACE/comfyui-prompt.log` |
| AJ panel/controller | `$WORKSPACE/h3-mobile.log` |

Line count is 1–1000; the reader takes at most 256 KiB from a regular file's tail,
decodes UTF-8 with replacement and marks truncation. Missing files return a normal
"Not created yet" response. Symlinks/FIFOs/unreadable files fail closed as
unavailable. Clients cannot choose a path. `make panel` writes its separate local
development log to `.local/panel.log`; service logs may not exist in that CPU preview.

## Optional admin terminal

```dotenv
H3_ENABLE_TERMINAL=0
```

Only exactly `1` enables Terminal. Keep it disabled for normal Ref2Video use.
**Enable only when the AJ panel is accessed through a trusted/private connection.**
The terminal grants the panel user's shell privileges. Basic Auth on a public
plain HTTP connection is not a suitable way to expose it. This feature does not
provide HTTPS, credential management or a separate security boundary for commands.

The terminal is an interactive Bash PTY in `WORKSPACE` (normally `/workspace`),
with live output, command input, Enter, Ctrl+C, connect and disconnect. It reads
no user Bash startup files. AJ does not store command history; shell history is
disabled and `HISTFILE` points to `/dev/null`. The browser only holds a bounded
65,536-character transcript in memory and clears unsent input on disconnect. Closing
Terminal/Advanced or leaving the page disconnects the session.

Security contract:

- The authenticated `POST /api/terminal/session` handshake mints a cryptographically
  random 30-second one-use token. It launches no process, uses `Cache-Control:
  no-store` and retains at most 32 pending tickets in controller memory.
- `/api/terminal/ws` authenticates independently; FastAPI HTTP middleware does
  **not** protect WebSockets. The client offers `h3-terminal` and `h3-session.<token>`
  as WebSocket subprotocols. The server selects only `h3-terminal`; the token is
  never a URL/query parameter in AJ's client or normal access logs.
- Disabled, invalid, expired and reused tickets fail closed before shell launch.
  Browser Origins must match the panel Host. Only one terminal session is allowed.
  Controller restart clears tickets; shutdown refuses upgrades and cleans sessions.
- A private gated supervisor captures process ownership before Bash starts,
  handles parent death and reaps shell/background descendants as a Linux subreaper.
  Cleanup also covers descendants that create another process group/session.
  The controller retains original supervisor and gated-shell pidfds through group cleanup, never a numeric
  `killpg`, and waits for reaping on disconnect, error or cancellation. It requires
  the same Linux >=6.9 group-control capability as existing service controls.

CPU tests exercise tickets, real local PTYs and loopback HTTP/WebSockets, Ctrl+C,
ignored TERM, detached children, shell exit, launch/capture errors, descriptor
errors, repeated cancellation and WebSocket cleanup. Live transport
through the chosen private deployment must still be checked during acceptance.

Batch API retries require a client-generated UUID `request_id` in the JSON body.
Reuse the same UUID and exact body after an uncertain response, including after
a controller restart. The committed response and key live in the same atomic
queue snapshot as the jobs and batches. A committed key returns its original
`created_batches`; reuse with a different body returns 409. Missing keys fail
closed with 400. New intentional requests, including identical content, use new
UUIDs. AJ keeps an unconfirmed body/key in browser localStorage before submission
and reuses it after reload; the separate new-request action asks the user to
confirm creating another operation. Keep the queue snapshot and its request
ledger together; deleting old ledger entries loses their retry guarantees.

Review approval is one operation per job. Its result and approved prompt are
committed with that job; retrying the same approval returns the original result
even after rendering, while changing an already approved prompt returns 409.
AJ preserves the approval body for retries. Cancel/reject cannot create paid
submissions and retain their existing terminal-state checks.

A queue persistence failure invalidates an already armed STOP/DESTROY plan.
Storage recovery does not re-arm it, and controller restart interrupts old armed
or executing plans. Explicit re-arm is required. Confirmed remote terminal
results survive local write failures in memory, block dispatch while degraded,
and resume through persistence recovery without another remote submit.

Service control launches and registers each child in the same parent so PID
reuse cannot occur before pidfd capture. Failed registration reaps that exact
child via pidfd TERM, then KILL if necessary, and removes the failed PID record.
The default ownership store is `$WORKSPACE/.h3-service-pids`, independent of
`PANEL_ROOT` and `COMFY_ROOT`. Startup imports old `PANEL_ROOT/state/pids` records
once before any service operation. An explicit `PID_DIR` must remain stable
when changing worker configuration. Stop controls the entire originally owned
group through one pidfd and readiness requires that group's listening socket.
