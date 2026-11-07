"""Bounded, low-overhead VRAM release for the current AJ batch.

Runs independently of the panel. Never starts or cancels jobs, only evicts
idle ComfyUI renderer model residency after checking both queues and GPUGate.
"""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

WORKSPACE = Path("/workspace")
AJ_QUEUE = WORKSPACE / "H3_VAST_MOBILE/state/queue.json"
GATE_DIR = WORKSPACE / "ComfyUI/output/.aj-gpu"
RENDER = "http://127.0.0.1:8188"
MIN_FREE_MIB = 57344
INTERVAL = 10
MAX_CYCLES = 720
ACTIVE_STATUSES = {
    "prompt_queued", "prompt_running", "prompt_preparing",
    "prompt_submitting", "render_queued_auto", "render_queued_review",
    "render_running", "render_preparing", "render_submitting",
}


def json_get(route):
    with urllib.request.urlopen(RENDER + route, timeout=7) as response:
        return json.load(response)


def render_idle():
    queue = json_get("/queue")
    return not queue.get("queue_running") and not queue.get("queue_pending")


def free_mib():
    p = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=7,
    )
    if p.returncode:
        raise RuntimeError("gpu memory query failed")
    values = p.stdout.strip().splitlines()
    if len(values) != 1 or not values[0].strip().isdigit():
        raise RuntimeError("unexpected GPU topology")
    return int(values[0].strip())


def work_pending():
    data = json.loads(AJ_QUEUE.read_text())
    jobs = data if isinstance(data, list) else data.get("queue", [])
    return any(item.get("status") in ACTIVE_STATUSES for item in jobs)


def attempt_cleanup():
    state_fd = os.open(GATE_DIR / "state.lock", os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    infer_fd = None
    try:
        fcntl.flock(state_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((GATE_DIR / "state.json").read_text())
        if (state.get("phase") != "idle" or
                not 0 <= time.monotonic() - float(state.get("at", 0)) <= 3):
            return False
        infer_fd = os.open(
            GATE_DIR / "inference.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        fcntl.flock(infer_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not render_idle():
            return False
        before = free_mib()
        if before >= MIN_FREE_MIB:
            return False
        req = urllib.request.Request(
            RENDER + "/free",
            data=b'{"unload_models":true,"free_memory":true}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as reply:
            if reply.status != 200:
                raise RuntimeError("render free endpoint rejected request")
        print("RECLAIM", before, free_mib(), flush=True)
        return True
    except BlockingIOError:
        return False
    finally:
        if infer_fd is not None:
            os.close(infer_fd)
        os.close(state_fd)


def main():
    idle_cycles = 0
    last_attempt = 0.0
    for _ in range(MAX_CYCLES):
        try:
            if not work_pending():
                idle_cycles += 1
                if idle_cycles >= 12:
                    print("STOP no remaining work", flush=True)
                    return
            else:
                idle_cycles = 0
                if time.monotonic() - last_attempt > 45:
                    if free_mib() < MIN_FREE_MIB:
                        last_attempt = time.monotonic()
                        attempt_cleanup()
        except Exception as exc:
            print("CHECK_ERROR", type(exc).__name__, flush=True)
        time.sleep(INTERVAL)
    print("STOP bounded session lifetime", flush=True)


if __name__ == "__main__":
    main()
