# Optional AJ Docker trial on Vast

**GPU proof is PENDING. The existing [Git installer](PERSONAL_FIRST_RUN.md) remains
the default.** Obtain the image from the [complete CI build and publication
gate](VAST_DOCKER_BUILD.md#ci-build-and-publication), using its verified registry
digest and fresh-runner pull/smoke result. Copy the exact `image` reference from
`registry.json`, the CI summary or [PR #10](https://github.com/ziomalskit/ziomalskit/pull/10).
CI records compressed layer bytes and the fresh-pull uncompressed image size.
New GHCR packages can be private: verify anonymous pull access or configure the
required registry authentication before a Vast trial. This CI performs no GPU
rental. Do not rent an instance just to build the environment.

## Choose the template and instance

In a private Vast template, set:

| Setting | Value |
| --- | --- |
| Image | Verified `ghcr.io/ziomalskit/aj-vast@sha256:…` from successful CI; never `latest` or Automatic |
| Launch mode | **docker ENTRYPOINT**, no arguments |
| Ports | Map **22/tcp only**, for SSH |
| Environment | `PUBLIC_KEY` = the contents of your SSH **public** `.pub` key |
| On-start / provisioning | Empty; no Jupyter, external setup script or auto-render |
| GPU | Exactly one **RTX PRO 6000 Blackwell, 96 GB**, capability 12.0 |
| Host | x86_64, NVIDIA driver **580.65.06 or newer**, NVIDIA Container Toolkit |
| Resources | At least **96 GiB available RAM**, **8 effective CPU cores**, cgroup v2 |
| Disk | Start evaluating at **350 GB**; confirm space after image extraction |

Vast's [launch-mode documentation](https://docs.vast.ai/guides/templates/template-settings)
explains that SSH/Jupyter modes replace the image entrypoint. Use ENTRYPOINT;
this image supplies its own key-only SSH. If the UI strips the image digest,
stop and resolve that with the builder. This exact template still needs a live
trial; no claim of tested Vast launch compatibility is made.

Do not put HF tokens, Vast/account API keys, passwords or SSH private keys in the
template. The image needs **no Vast API key**. Review the real GPU + disk +
transfer cost and your time limit before approving a rental. A $15 / 4-hour plan
is conditional on a real offer, not a guarantee. Containers still depend on the
host driver, Linux kernel, readable `/proc`, pidfd permissions and provider
resource limits; they cannot repair those. Ask the host about cgroup limits
hidden outside the container namespace.

The 19 default weights occupy **278,009,016,136 bytes** in one store. Initialization
requires their remaining bytes plus **32 GiB free reserve**, measured on the
actual `/workspace` disk after image extraction. A nominal 350 GB offer can be
too small once image layers, writable data and outputs are counted. Disk is
**ephemeral**: DESTROY loses these files. No Local Volume is required for this
trial, and keep-data destroy is unavailable.

## SSH, health, then explicit download

Use the instance's actual SSH host and mapped port. On your computer or phone's
SSH client, connect with your key and add the private panel tunnel:

```bash
ssh -p SSH_PORT -L 127.0.0.1:7860:127.0.0.1:7860 root@SSH_HOST
```

In that SSH terminal, run these two lines in order:

```bash
aj-docker init
aj-docker models && aj-docker start
```

The **first line downloads no weights**. It prepares `/workspace`, checks free
disk, effective RAM/CPU, GPU/driver/toolkit, Python/torch/node/native pins, Linux
process ownership support and the real small KJ MiniMax SageAttention kernel.
It then asks you to choose a private panel password (at least 16 characters).
Do not proceed if health fails; resolve the stated host/allocation problem.

Run the **second line only when you explicitly approve the 278 GB transfer**.
Enter an HF read token at its hidden prompt only if required. It is passed only
to the downloader, never saved in the image, history, panel environment or
receipts. A byte counter reports transfer/hash-verification progress every 15 seconds.
The existing downloader verifies size/SHA256 and resumes source-bound partials. First transfer still takes time even with a
prebuilt environment. Watch the real provider bill and your deadline.

If interrupted, run the same second line again. **Do not delete `.downloads`,
partials or the models directory.** After a container restart, repeat the first
line before the second. There is no automatic download or service restart.
Completed models are reused and reverified; this can involve substantial disk
reading. All models live only in `/workspace/ComfyUI/models`.

`start` uses the original locked/pidfd service controller, starts two loopback
ComfyUI workers and the loopback panel, then runs preflight and conversion smoke.
It always leaves **submissions OFF**. A failed stage stops the sequence; it never
submits a render. If services started before a later preflight error, inspect the
private logs under `/workspace` and use the existing service controller to stop
them; do not guess PIDs. No command stops/destroys a provider resource.

## Approve two manual renders, save MP4, destroy

Open `http://127.0.0.1:7860` on the device holding the SSH tunnel. Login is `h3`
and the password you chose. Check GPU/service/controller Diagnostics and an empty
queue. Provider telemetry/lifecycle is unavailable in this image: supervise the
real bill and deadline in Vast; do not arm a telemetry-dependent panel cost guard.
After preflight/smoke PASS, deliberately enable acceptance in the SSH terminal:

```bash
set -a; source /workspace/H3_VAST_MOBILE/runtime.env; set +a
H3_ALLOW_SUBMISSIONS=1 "$COMFY_PYTHON" /workspace/H3_VAST_MOBILE/scripts/runtime_config.py write-env /workspace/H3_VAST_MOBILE/runtime.env
bash /workspace/H3_VAST_MOBILE/scripts/service_ctl.sh restart panel
```

Do **one H3 Full manual 5-second render**, then, after completion and checking its
MP4, **one 10Eros Full manual 5-second render**. In the panel use **Own final H3
prompt**, six real images, at most one audio, batches **1**, an explicit seed and
your own final prompt. Keep the Full model, LoRA and normal quality defaults.
**Autoprompt automatically creates five renders; it is not a cheap smoke test.**
The minimal real 27B probe in [Personal First Run](PERSONAL_FIRST_RUN.md) is still
required before later 10Eros Autoprompt use; these two manual renders bypass LLMs.

Check both videos for motion, black/static frames and audio. OPEN MP4 and download
both MP4s to your device; verify the local copies play. Record GPU/driver/image
digest, profile/seed/duration, wall time, peak VRAM and results. Kernel PASS alone
does not certify Full video quality, long sequences, Autoprompt or recovery.

Disable submissions, stop only your owned services, then use the **Vast UI to
manually DESTROY this exact instance** after saving the files:

```bash
H3_ALLOW_SUBMISSIONS=0 "$COMFY_PYTHON" /workspace/H3_VAST_MOBILE/scripts/runtime_config.py write-env /workspace/H3_VAST_MOBILE/runtime.env
bash /workspace/H3_VAST_MOBILE/scripts/service_ctl.sh stop all
```

Confirm the instance is gone and billing has ended, including storage. Stopping
a process or the instance is not proof that storage billing ended. There is no
automatic provider lifecycle in this Docker path.
