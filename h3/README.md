# H3 Vast Mobile — PRE-RENTAL FINAL RC5

This is the release candidate to use for the first Vast deployment.

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
- native MiniMax H3 INT8 is the production default
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
