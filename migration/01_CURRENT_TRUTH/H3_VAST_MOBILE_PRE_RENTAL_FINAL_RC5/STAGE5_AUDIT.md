# Stage 5 offline audit

Verified offline:
- Python controller compiles.
- Bash provisioning/service scripts pass `bash -n`.
- Vast CLI installation is included in provisioning.
- Vast API key is not exposed to browser code.
- Stop/destroy actions execute server-side through `vastai`.
- Immediate destructive actions require typed confirmation.
- `destroy_after_queue_keep_data` fails closed unless a separate volume mount is verified.
- pending review does not block stop/destroy-after-queue.
- stop-after-current blocks new render dispatch.
- reviewed jobs still outrank auto render jobs.
- prompt worker now prioritizes auto #1–5 across all waiting batches before review #6–10.
- Stage 4 prompt/render split and watchdog remain intact.

Requires live Vast verification:
- exact fields returned by `vastai show instance --raw` on chosen host/image
- instance-scoped Vast credentials available to CLI
- attached Local Volume mount layout
- stop/destroy command behavior from inside the chosen Vast launch mode
- live billing/status refresh
