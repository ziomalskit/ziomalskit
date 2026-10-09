# H3 Vast Mobile — PRE-RENTAL FINAL RC5

Current AJ/H3 runtime for CPU development and the eventual consolidated Vast
acceptance session. Live GPU/Vast acceptance is still pending. Follow
[`docs/ROADMAP.md`](../docs/ROADMAP.md): finish the feature set, run the full CPU
regression/audit, choose and freeze final models as a separate decision, then
perform one consolidated GPU/Vast session.

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
- native MiniMax H3 INT8 is the intended default; the exact production stack and manifest remain provisional
- 10Eros Hybrid remains optional A/B

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

After all required models are present:
```bash
bash <PANEL_ROOT>/scripts/preflight.sh
bash <PANEL_ROOT>/scripts/smoke_test.sh
```

Do not start a real H3 render until both pass.

See `FULL_PRE_RENTAL_AUDIT.md` for the complete audit and remaining live-only risks.

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

Model diagnostics use the **provisional** `config/models_manifest.json`: required
primary files, default LoRAs and accepted bridge locations. Missing/empty files
produce FAIL; even complete presence stays WARN because presence cannot prove
integrity, loader readiness or final model selection. Existing preflight integrity
checks are unchanged. Production model sources/checksums must be decided separately.

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
