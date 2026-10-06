from __future__ import annotations
import asyncio, base64, hmac, json, math, os, shutil, subprocess, sys, time, uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlencode

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
WORKFLOWS = ROOT / "workflows"
CONFIG = ROOT / "config"
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)

MASTER = WORKFLOWS / "VAST_H3_MASTER_NATIVE_INT8_96GB.json"
PROMPT_ONLY = WORKFLOWS / "VAST_H3_PROMPT_ONLY_STAGE2.json"
MAP = json.loads((CONFIG / "VAST_H3_API_MAP.json").read_text(encoding="utf-8"))

# Stage 3 audited: prompt and render are truly separate ComfyUI services.
RENDER_COMFY_URL = os.getenv("RENDER_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
PROMPT_COMFY_URL = os.getenv("PROMPT_COMFY_URL", "http://127.0.0.1:8189").rstrip("/")
COMFY_INPUT_DIR = Path(os.getenv("COMFY_INPUT_DIR", "/workspace/ComfyUI/input"))
COMFY_ROOT = Path(os.getenv("COMFY_ROOT", "/workspace/ComfyUI"))
COMFY_PYTHON = os.getenv("COMFY_PYTHON", sys.executable)
COMFY_OUTPUT_DIR = Path(os.getenv("COMFY_OUTPUT_DIR", str(COMFY_ROOT / "output")))
COMFY_MODELS_DIR = Path(os.getenv("COMFY_MODELS_DIR", str(COMFY_ROOT / "models")))
RENDER_RESTART_CMD = os.getenv("RENDER_RESTART_CMD", "supervisorctl restart comfyui-render")
PROMPT_RESTART_CMD = os.getenv("PROMPT_RESTART_CMD", "supervisorctl restart comfyui-prompt")
QUEUE_FILE = STATE / "queue.json"

VAST_CONTROL_FILE = STATE / "vast_control.json"
VAST_CLI = os.getenv("VAST_CLI", "vastai")
SERVICE_CTL = os.getenv("SERVICE_CTL", str(ROOT / "scripts" / "service_ctl.sh"))
H3_PERSISTENT_ROOT = os.getenv("H3_PERSISTENT_ROOT", "").strip()
H3_PERSISTENCE_MODE = os.getenv("H3_PERSISTENCE_MODE", "").strip().lower()
SESSION_STARTED_AT = time.time()
PANEL_AUTH_USER = os.getenv("H3_PANEL_USER", "h3")
PANEL_AUTH_PASSWORD = os.getenv("H3_PANEL_PASSWORD", "")

PROMPTS_PER_BATCH = 10
AUTO_APPROVED_PER_BATCH = 5
REVIEW_PER_BATCH = 5

app = FastAPI(title="H3 Mobile Controller", version="1.0.0-pre-rental-final-rc4")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _authorized(auth_header: str | None) -> bool:
    if not PANEL_AUTH_PASSWORD:
        return False
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:], validate=True).decode("utf-8")
        user, password = decoded.split(":", 1)
    except Exception:
        return False
    return hmac.compare_digest(user.encode("utf-8"), PANEL_AUTH_USER.encode("utf-8")) and hmac.compare_digest(
        password.encode("utf-8"), PANEL_AUTH_PASSWORD.encode("utf-8")
    )


@app.middleware("http")
async def require_panel_auth(request: Request, call_next):
    # Fail closed: destructive controls must never be exposed by an unauthenticated panel.
    if not PANEL_AUTH_PASSWORD:
        return Response(
            "H3 panel authentication is not configured. Set H3_PANEL_PASSWORD.",
            status_code=503,
        )
    if not _authorized(request.headers.get("authorization")):
        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="H3 Vast Mobile"'},
        )
    return await call_next(request)


class LoraSetting(BaseModel):
    name: str
    enabled: bool
    strength: float


class BatchRequest(BaseModel):
    prompt: str
    model: str = "native_int8"
    batches: int = Field(1, ge=1, le=100)
    seed: int | None = None
    random_each: bool = True
    soft_timeout_minutes: int = Field(8, ge=1, le=120)
    hard_restart_after_seconds: int = Field(90, ge=30, le=600)
    pictures: list[str] = []
    audio: list[str] = []
    loras: list[LoraSetting] = []


class ApprovalRequest(BaseModel):
    final_prompt: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


class VastActionRequest(BaseModel):
    action: str
    confirm: str | None = None
    idle_minutes: int | None = Field(None, ge=1, le=1440)
    cost_guard_usd: float | None = Field(None, ge=0.01, le=10000)


queue: list[dict[str, Any]] = []
batches: list[dict[str, Any]] = []
queue_lock = asyncio.Lock()
prompt_worker_task: asyncio.Task | None = None
render_worker_task: asyncio.Task | None = None


vast_guard_task: asyncio.Task | None = None
lifecycle_action_task: asyncio.Task | None = None
recovery_task: asyncio.Task | None = None
recovering_services: set[str] = set()
state_load_error: str | None = None
vast_control_load_error: str | None = None
last_busy_at = time.time()


class SubmissionUncertain(RuntimeError):
    """The request may have reached ComfyUI; automatic resubmission is unsafe."""


class SubmissionRejected(RuntimeError):
    """ComfyUI explicitly rejected the request without accepting a job."""


class SubmissionCancelled(RuntimeError):
    """Cancellation happened before dispatch; no request was sent."""


class SubmissionDeferred(RuntimeError):
    """Shutdown was armed during preparation; keep the job queued."""


class SubmissionSkippedForShutdown(RuntimeError):
    """An unsubmitted review prompt is excluded from a draining queue."""


def submissions_allowed() -> bool:
    return os.getenv("H3_ALLOW_SUBMISSIONS", "1").strip().lower() not in {"0", "false", "no", "off"}


def lifecycle_dispatch_blocked() -> bool:
    plan = str(vast_control.get("plan") or "none")
    inflight = lifecycle_action_task is not None and not lifecycle_action_task.done()
    return bool(state_load_error or vast_control_load_error) or inflight or plan.startswith("executing_") or plan in {
        "action_failed", "action_interrupted", "blocked_unsafe_persistence",
    }


def shutdown_armed() -> bool:
    return lifecycle_dispatch_blocked() or vast_control.get("plan") in {
        "stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data",
    }


def service_recovering(service: str) -> bool:
    return service in recovering_services or any(
        j.get("status") in {f"recovery_{service}", f"{service}_submission_uncertain"} for j in queue
    )


def _known_prompt_id(job: dict, service: str) -> str | None:
    pid = job.get(f"{service}_prompt_id")
    if pid:
        return str(pid)
    # Legacy state can have only the generic id. Never reuse another phase's id.
    if not job.get(f"{service}_submission_state") and job.get("active_service") in (None, service):
        return str(job["prompt_id"]) if job.get("prompt_id") else None
    return None


def _preserve_recovery(job: dict, service: str, warning: str) -> None:
    job["status"] = f"recovery_{service}"
    job["active_service"] = service
    job["recovery_warning"] = warning
    save_state()


def _record_cancel_confirmation(job: dict, service: str, prompt_id: str, confirmed: bool) -> None:
    if confirmed is True:
        job[f"{service}_cancel_confirmed"] = True
        job[f"{service}_cancel_confirmed_id"] = prompt_id
        save_state()


def state_diagnostics() -> dict[str, Any]:
    return {
        "ready": not (state_load_error or vast_control_load_error),
        "queue_error": state_load_error,
        "lifecycle_error": vast_control_load_error,
    }


def load_vast_control() -> dict[str, Any]:
    global vast_control_load_error
    vast_control_load_error = None
    default = {
        "plan": "none",
        "idle_minutes": 0,
        "cost_guard_usd": 0.0,
        "armed_at": None,
        "reason": None,
        "last_action": None,
        "last_action_at": None,
    }
    if not VAST_CONTROL_FILE.exists():
        return default
    try:
        data = json.loads(VAST_CONTROL_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("lifecycle state must be a JSON object")
        if "plan" in data and not isinstance(data["plan"], str):
            raise ValueError("lifecycle plan must be a string")
        if "plan" in data and data["plan"] not in {
            "none", "stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data",
            "action_failed", "action_interrupted", "blocked_unsafe_persistence",
            "executing_stop", "executing_destroy", "executing_idle_stop",
        }:
            raise ValueError("unrecognized lifecycle plan")
        for key in ("idle_minutes", "cost_guard_usd"):
            if key in data and (not isinstance(data[key], (int, float)) or isinstance(data[key], bool) or not math.isfinite(data[key]) or data[key] < 0):
                raise ValueError(f"invalid lifecycle field: {key}")
        default.update(data)
    except Exception as error:
        vast_control_load_error = f"Lifecycle state could not be loaded; existing file preserved: {error}"
    return default


vast_control: dict[str, Any] = load_vast_control()
if str(vast_control.get("plan", "")).startswith("executing_"):
    vast_control["reason"] = "previous lifecycle action was interrupted or controller restarted"
    vast_control["plan"] = "action_interrupted"


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def save_vast_control() -> None:
    if vast_control_load_error:
        raise RuntimeError(vast_control_load_error)
    _atomic_json_write(VAST_CONTROL_FILE, vast_control)


def save_state() -> None:
    if state_load_error:
        raise RuntimeError(state_load_error)
    _atomic_json_write(QUEUE_FILE, {"queue": queue, "batches": batches})


def load_state() -> None:
    global queue, batches, state_load_error
    state_load_error = None
    if not QUEUE_FILE.exists():
        return
    try:
        raw = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            loaded_queue, loaded_batches = raw, []
        else:
            if not isinstance(raw, dict) or not isinstance(raw.get("queue"), list) or not isinstance(raw.get("batches"), list):
                raise ValueError("queue state must contain queue and batches arrays")
            loaded_queue, loaded_batches = raw["queue"], raw["batches"]
        if not all(isinstance(job, dict) and isinstance(job.get("id"), str) and job["id"] and isinstance(job.get("status"), str) for job in loaded_queue):
            raise ValueError("queue contains an invalid job record")
        if not all(isinstance(batch, dict) for batch in loaded_batches):
            raise ValueError("batches contains an invalid record")
        queue, batches = loaded_queue, loaded_batches
        for j in queue:
            old_status = j.get("status")
            if old_status in {"prompt_running", "prompt_preparing", "prompt_submitting", "prompt_submission_uncertain"}:
                j["status"] = "recovery_prompt"
                j["recovery_from_status"] = old_status
            elif old_status in {"render_running", "render_preparing", "render_submitting", "render_submission_uncertain"}:
                j["status"] = "recovery_render"
                j["recovery_from_status"] = old_status
            elif old_status in ("soft_timeout_cancelling", "hard_timeout_restarting_comfy", "cancelling"):
                j["status"] = "recovery_render" if j.get("active_service") == "render" else "recovery_prompt"
                j["recovery_from_status"] = old_status
    except Exception as error:
        state_load_error = f"Queue state could not be loaded; existing file preserved: {error}"


def find_job(job_id: str) -> dict:
    job = next((j for j in queue if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def node_by_id(wf: dict, node_id: int) -> dict:
    for n in wf["nodes"]:
        if n.get("id") == node_id:
            return n
    raise KeyError(node_id)


def iter_all_nodes(doc: dict):
    for n in doc.get("nodes", []):
        yield n
    for sg in doc.get("definitions", {}).get("subgraphs", []):
        yield from iter_all_nodes(sg)


def set_named_and_positional(node: dict, field: str, value: Any) -> None:
    node.setdefault("widgets_values_named", {})[field] = value
    if node.get("id") == 4595 and field == "unet_name":
        node["widgets_values"][0] = value
    elif node.get("id") in (2624, 2632) and field == "positive":
        node["widgets_values"][0] = value
    elif node.get("type") == "LoadImage" and field == "image":
        node["widgets_values"][0] = value
    elif node.get("type") == "LoadAudio" and field == "audio":
        node["widgets_values"][0] = value


def patch_rgthree_seed(wf: dict, node_id: int, seed: int) -> None:
    n = node_by_id(wf, node_id)
    if n.get("type") != "Seed (rgthree)":
        raise RuntimeError(f"Expected Seed (rgthree) at node {node_id}, got {n.get('type')}")
    n.setdefault("widgets_values_named", {})["seed"] = int(seed)
    if not n.get("widgets_values"):
        raise RuntimeError(f"Seed node {node_id} has no positional widgets")
    n["widgets_values"][0] = int(seed)


def validate_input_filename(name: str) -> str:
    clean = Path(name).name
    if not clean or clean != name:
        raise HTTPException(400, f"invalid input filename: {name!r}")
    p = COMFY_INPUT_DIR / clean
    if not p.is_file():
        raise HTTPException(400, f"uploaded input file is missing: {clean}")
    return clean


def model_path_for_preset(preset: str) -> Path:
    filename = MAP["nodes"]["model_loader"]["alternatives"].get(preset)
    if not filename:
        raise HTTPException(400, f"unknown model preset: {preset}")
    return COMFY_MODELS_DIR / "diffusion_models" / filename


def validate_requested_assets(req: BatchRequest) -> tuple[list[str], list[str]]:
    # First release deliberately mirrors the fixed-width source graph: 1 anchor + 5 supporting refs.
    if len(req.pictures) != 6:
        raise HTTPException(400, "This audited release requires exactly 6 images: Picture 1 + 5 supporting references.")
    if len(req.audio) > 1:
        raise HTTPException(400, "This audited first release supports at most 1 audio reference.")
    pictures = [validate_input_filename(x) for x in req.pictures]
    audio = [validate_input_filename(x) for x in req.audio]

    model_path = model_path_for_preset(req.model)
    if not model_path.is_file():
        raise HTTPException(409, f"selected render model is not installed: {model_path.name}")

    for lora in req.loras:
        if lora.enabled:
            lp = COMFY_MODELS_DIR / "loras" / Path(lora.name).name
            if not lp.is_file():
                raise HTTPException(409, f"enabled LoRA is not installed: {lora.name}")
    return pictures, audio


def normalized_audio_slots(audio: list[str]) -> tuple[list[str], str]:
    silence = "__h3_silence_1s.wav"
    if not audio:
        validate_input_filename(silence)
        return [silence, silence, silence], "LLM does not see audio"
    slots = list(audio[:3])
    while len(slots) < 3:
        slots.append(slots[0])
    return slots, "LLM sees Audio 0"


def set_collect_refs_audio_mode(wf: dict, choice: str) -> None:
    n = node_by_id(wf, 2249)
    n.setdefault("widgets_values_named", {})["choice_1"] = choice
    vals = n.get("widgets_values") or []
    if len(vals) < 3:
        raise RuntimeError("Collect Refs node 2249 has unexpected widget layout")
    vals[2] = choice


def set_primary_audio_reference_enabled(wf: dict, enabled: bool) -> None:
    # Node 3423 exposes Audio 0 `is_reference`; its BOOLEAN output feeds
    # Set_ref_audio_0_apply. This makes "no audio" truly mean no H3 audio ref.
    n = node_by_id(wf, 3423)
    vals = n.get("widgets_values") or []
    if not vals:
        raise RuntimeError("Audio 0 controller 3423 has unexpected widget layout")
    n.setdefault("widgets_values_named", {})["value_3"] = bool(enabled)
    vals[0] = bool(enabled)


def patch_llm_pipeline_seeds(wf: dict, analysis_seed: int, prompt_seed: int) -> None:
    """
    Patch the REAL seed sources used by the LLM dashboard.

    The four LLMTextProcessor nodes have their `seed` sockets connected, so their
    local widget values are only mirrors. Node 4022 is the upstream controller
    that actually feeds Step 1/2/3/4 seeds into the nested dashboard.

    Step 1–2: fixed per batch -> cacheable across 10 candidates.
    Step 3–4: unique per candidate -> actual prompt diversity.
    """
    controller = None
    downstream = {}
    for n in iter_all_nodes(wf):
        if n.get("id") == 4022:
            controller = n
        if n.get("id") in (2441, 2447, 2445, 4275):
            downstream[n["id"]] = n

    if controller is None:
        raise RuntimeError("Could not find LLM dashboard seed controller node 4022")

    vals = controller.get("widgets_values") or []
    if len(vals) <= 17:
        raise RuntimeError("LLM dashboard seed controller 4022 has unexpected widget layout")

    seeds = {
        "value_4": int(analysis_seed),       # Step 1
        "value_4_1": int(analysis_seed) + 1, # Step 2
        "value_4_2": int(prompt_seed),       # Step 3
        "value_4_3": int(prompt_seed) + 1,   # Step 4
    }
    controller.setdefault("widgets_values_named", {}).update(seeds)
    for idx, value in ((5, seeds["value_4"]), (9, seeds["value_4_1"]),
                       (13, seeds["value_4_2"]), (17, seeds["value_4_3"])):
        vals[idx] = value

    # Keep local widgets synchronized too; connected sockets remain authoritative.
    mirrors = {
        2441: seeds["value_4"],
        2447: seeds["value_4_1"],
        2445: seeds["value_4_2"],
        4275: seeds["value_4_3"],
    }
    missing = set(mirrors) - set(downstream)
    if missing:
        raise RuntimeError(f"Missing LLM processor nodes: {sorted(missing)}")
    for nid, seed in mirrors.items():
        n = downstream[nid]
        if n.get("type") != "LLMTextProcessor":
            raise RuntimeError(f"Expected LLMTextProcessor at {nid}, got {n.get('type')}")
        n.setdefault("widgets_values_named", {})["seed"] = int(seed)
        if len(n.get("widgets_values", [])) <= 13:
            raise RuntimeError(f"LLMTextProcessor {nid} has unexpected widget layout")
        n["widgets_values"][13] = int(seed)


def patch_workflow(job: dict, workflow_template: Path, *, approved_prompt: str | None = None) -> Path:
    wf = json.loads(workflow_template.read_text(encoding="utf-8"))
    ctx = job["context"]

    checkpoint = MAP["nodes"]["model_loader"]["alternatives"].get(ctx["model"])
    if not checkpoint:
        raise ValueError(f"Unknown model preset: {ctx['model']}")
    set_named_and_positional(node_by_id(wf, 4595), "unet_name", checkpoint)
    set_named_and_positional(node_by_id(wf, 2624), "positive", ctx["prompt"])
    set_named_and_positional(node_by_id(wf, 2632), "positive", approved_prompt or "")

    # Exact fixed-width reference context is bound to the batch and copied into every candidate.
    pictures = list(ctx.get("pictures", []))
    if len(pictures) != 6:
        raise RuntimeError("job context does not contain exactly 6 picture slots")
    for slot, filename in enumerate(pictures, start=1):
        nid = MAP["nodes"]["pictures"][slot - 1]["id"]
        set_named_and_positional(node_by_id(wf, nid), "image", filename)

    requested_audio = list(ctx.get("audio", []))
    audio_slots, audio_choice = normalized_audio_slots(requested_audio)
    for slot, filename in enumerate(audio_slots, start=1):
        nid = MAP["nodes"]["audio"][slot - 1]["id"]
        set_named_and_positional(node_by_id(wf, nid), "audio", filename)
    set_collect_refs_audio_mode(wf, audio_choice)
    set_primary_audio_reference_enabled(wf, bool(requested_audio))

    # Connected Seed (rgthree) nodes are authoritative; sampler widget values are only UI mirrors.
    render_seed = int(job["render_seed"])
    patch_rgthree_seed(wf, 1854, render_seed)
    patch_rgthree_seed(wf, 6067, render_seed + 1)
    for nid, seed in ((7334, render_seed), (7335, render_seed + 1)):
        n = node_by_id(wf, nid)
        n.setdefault("widgets_values_named", {})["noise_seed"] = seed
        if n.get("widgets_values"):
            n["widgets_values"][0] = seed

    # One analysis token per batch stabilizes Step 0-2 input text.
    patch_rgthree_seed(wf, 7360, int(job["analysis_seed"]))
    if workflow_template == PROMPT_ONLY:
        patch_llm_pipeline_seeds(
            wf,
            int(job["analysis_seed"]),
            int(job["prompt_seed"]),
        )

    if ctx.get("loras"):
        n = node_by_id(wf, 6164)
        byname = {x["name"]: x for x in ctx["loras"]}
        for key, item in n.get("widgets_values_named", {}).items():
            if key.startswith("lora_") and isinstance(item, dict) and item.get("lora") in byname:
                u = byname[item["lora"]]
                item["on"] = bool(u["enabled"])
                item["strength"] = float(u["strength"])
        for item in n.get("widgets_values", []):
            if isinstance(item, dict) and item.get("lora") in byname:
                u = byname[item["lora"]]
                item["on"] = bool(u["enabled"])
                item["strength"] = float(u["strength"])

    phase = "prompt" if workflow_template == PROMPT_ONLY else "render"
    out = STATE / f"job_{job['id']}_{phase}.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def service_url(service: str) -> str:
    if service == "prompt":
        return PROMPT_COMFY_URL
    if service == "render":
        return RENDER_COMFY_URL
    raise ValueError(service)


def restart_cmd(service: str) -> str:
    return PROMPT_RESTART_CMD if service == "prompt" else RENDER_RESTART_CMD


async def _terminate_subprocess(process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.wait(), timeout=5)


async def _drain_pipe(stream) -> None:
    while await stream.read(4096):
        pass


async def run_cli_envelope(service: str, *args: str) -> dict:
    env = os.environ.copy()
    env["COMFY_LOCAL_URL"] = service_url(service)
    env["COMFY_WHERE"] = "local"
    proc = await asyncio.create_subprocess_exec(
        COMFY_PYTHON, "-m", "comfy_cli", "--workspace", str(COMFY_ROOT), "--json", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await proc.communicate()
    finally:
        await _terminate_subprocess(proc)
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace") or f"comfy-cli exited {proc.returncode}")
    lines = [x for x in out.decode(errors="replace").splitlines() if x.strip()]
    if not lines:
        raise RuntimeError(err.decode(errors="replace") or f"comfy exited {proc.returncode}")
    envelope = json.loads(lines[-1])
    if envelope.get("type") != "envelope":
        raise RuntimeError(f"comfy-cli stream ended without envelope: {envelope}")
    if not envelope.get("ok", False):
        raise RuntimeError(json.dumps(envelope.get("error"), ensure_ascii=False))
    return envelope


async def cancel_prompt(service: str, prompt_id: str) -> bool:
    # ComfyUI 0.38 cancels this id atomically under the queue mutex. CLI 1.21
    # instead checks the queue then interrupts globally, risking the next job.
    from urllib.parse import quote
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{service_url(service)}/api/jobs/{quote(prompt_id, safe='')}/cancel"
        )
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or type(result.get("cancelled")) is not bool:
        raise RuntimeError("ComfyUI returned an invalid cancellation acknowledgement")
    return result["cancelled"]


async def restart_comfy(service: str) -> None:
    proc = await asyncio.create_subprocess_shell(restart_cmd(service))
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{service} restart failed with exit code {proc.returncode}")


async def fetch_history(service: str, prompt_id: str, *, retries: int = 8, delay: float = 0.4) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{service_url(service)}/history/{prompt_id}")
                r.raise_for_status()
                data = r.json()
            entry = data.get(prompt_id, data)
            if isinstance(entry, dict) and entry:
                return entry
        except Exception as e:
            last_error = e
        if attempt + 1 < retries:
            await asyncio.sleep(delay)
    if last_error is not None:
        raise last_error
    return {}


async def wait_service_ready(service: str, *, timeout_seconds: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(timeout=10) as c:
        while time.monotonic() < deadline:
            try:
                r = await c.get(f"{service_url(service)}/system_stats")
                if 200 <= r.status_code < 300:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False


async def fetch_service_queue(service: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{service_url(service)}/queue")
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ("queue_running", "queue_pending")):
        raise RuntimeError("ComfyUI returned an invalid queue response")
    return data


def _contains_prompt_id(value: Any, prompt_id: str) -> bool:
    if isinstance(value, str):
        return value == prompt_id
    if isinstance(value, dict):
        return any(_contains_prompt_id(v, prompt_id) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_prompt_id(v, prompt_id) for v in value)
    return False


def extract_output_urls_from_history(entry: dict, service: str, *, video_only: bool = False) -> list[str]:
    urls: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            filename = value.get("filename")
            if isinstance(filename, str) and filename:
                subfolder = str(value.get("subfolder") or "")
                kind = str(value.get("type") or "output")
                key = (filename, subfolder, kind)
                is_video = Path(filename).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
                if key not in seen and (not video_only or (is_video and kind == "output")):
                    seen.add(key)
                    q = urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
                    urls.append(f"/api/proxy/{service}/view?{q}")
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(entry.get("outputs") or {})
    return urls


def _history_outcome(entry: dict) -> str | None:
    status = entry.get("status") or {}
    if not isinstance(status, dict):
        return None
    messages = status.get("messages") or []
    names = {m[0] for m in messages if isinstance(m, (list, tuple)) and m and isinstance(m[0], str)}
    if status.get("status_str") == "error" or names & {"execution_error", "execution_interrupted"}:
        return "error"
    if status.get("status_str") == "success" and status.get("completed") is True:
        return "success"
    return None


def _apply_history_outcome(job: dict, service: str, entry: dict) -> bool:
    """Only settled ComfyUI history is proof of a terminal execution outcome."""
    outcome = _history_outcome(entry)
    if outcome is None:
        return False
    if outcome == "error":
        job["status"] = "cancelled" if job.get("cancel_requested") else (
            f"{service}_stuck_skipped" if job.get(f"{service}_cancel_reason") == "timeout" else f"{service}_failed"
        )
        job["error"] = "ComfyUI execution failed or was interrupted"
        job["execution_status"] = entry.get("status")
        job["finished_at"] = time.time()
    elif service == "prompt":
        job.update(extract_prompt_texts(entry))
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
        elif not job.get("final_h3_prompt"):
            job["status"] = "prompt_failed"
            job["error"] = "successful prompt execution did not capture a final H3 prompt"
            job["finished_at"] = time.time()
        else:
            job["status"] = "pending_review" if job.get("review_required") else "render_queued_auto"
            job["render_priority"] = None if job.get("review_required") else 10
            job["prompt_ready_at"] = time.time()
    else:
        videos = extract_output_urls_from_history(entry, "render", video_only=True)
        if not videos:
            job["status"] = "render_failed"
            job["error"] = "successful render execution did not produce a video output"
        else:
            job["status"] = "completed"
            job["video_outputs"] = videos
            job.setdefault("outputs", [])
            for url in videos:
                if url not in job["outputs"]:
                    job["outputs"].append(url)
        job["finished_at"] = time.time()
    job[f"{service}_submission_state"] = "settled"
    job.pop("recovery_warning", None)
    save_state()
    return True


def _prompt_graph_from_history(entry: dict) -> dict:
    p = entry.get("prompt")
    if isinstance(p, dict):
        inner = p.get("prompt")
        return inner if isinstance(inner, dict) else p
    if isinstance(p, list) and len(p) >= 3 and isinstance(p[2], dict):
        return p[2]
    return {}


def _title_for_api_node(node: dict) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict) and meta.get("title"):
        return str(meta["title"])
    return str(node.get("class_type", ""))


def extract_prompt_texts(entry: dict) -> dict[str, str | None]:
    outputs = entry.get("outputs") or {}
    graph = _prompt_graph_from_history(entry)
    capture = MAP["prompt_capture"]
    wanted = {
        capture["step0_visual_facts_title"]: "visual_facts",
        capture["step1_expanded_intent_title"]: "expanded_intent",
        capture["step2_reference_map_title"]: "reference_map",
        capture["step3_creative_plan_title"]: "creative_plan",
        capture["step4_final_prompt_title"]: "final_h3_prompt",
    }
    result = {v: None for v in wanted.values()}
    for node_id, out in outputs.items():
        if not isinstance(out, dict) or "text" not in out:
            continue
        raw = out["text"]
        value = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        api_node = graph.get(str(node_id), {}) if isinstance(graph, dict) else {}
        title = _title_for_api_node(api_node) if isinstance(api_node, dict) else ""
        if title in wanted:
            result[wanted[title]] = value

    # Conversion normally preserves the top-level PreviewAny id. Use it as a
    # final-prompt fallback if subgraph title metadata was not retained.
    fallback_id = str(capture.get("fallback_top_level_final_node_id", ""))
    if not result.get("final_h3_prompt") and fallback_id:
        out = outputs.get(fallback_id, {})
        if isinstance(out, dict) and "text" in out:
            raw = out["text"]
            result["final_h3_prompt"] = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
    return result


def normalize_output_url(url: str, service: str) -> str:
    """Turn localhost ComfyUI links into controller-proxied phone-accessible links."""
    try:
        u = urlsplit(url)
        path = u.path.lstrip("/")
        q = f"?{u.query}" if u.query else ""
        return f"/api/proxy/{service}/{path}{q}"
    except Exception:
        return url


async def watch_prompt(job: dict, prompt_id: str, service: str) -> dict:
    env = os.environ.copy()
    env["COMFY_LOCAL_URL"] = service_url(service)
    env["COMFY_WHERE"] = "local"
    watcher = await asyncio.create_subprocess_exec(
        COMFY_PYTHON, "-m", "comfy_cli", "--workspace", str(COMFY_ROOT), "--json-stream", "jobs", "watch", prompt_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stderr_task = asyncio.create_task(_drain_pipe(watcher.stderr))
    try:
        last_progress = time.monotonic()
        soft = job["context"]["soft_timeout_minutes"] * 60
        outputs: list[str] = []

        while True:
            try:
                line = await asyncio.wait_for(watcher.stdout.readline(), timeout=5)
            except asyncio.TimeoutError:
                line = b""

            if line:
                try:
                    ev = json.loads(line.decode())
                except Exception:
                    ev = None
                if isinstance(ev, dict):
                    typ = ev.get("type")
                    if typ in ("progress", "executing", "executed", "execution_cached", "output"):
                        last_progress = time.monotonic()
                        job["last_event"] = typ
                        if typ == "execution_cached":
                            cached = ev.get("nodes") or ev.get("node_ids") or ([ev.get("node")] if ev.get("node") is not None else [])
                            job.setdefault("cached_node_ids", [])
                            for nid in cached:
                                if nid is not None and str(nid) not in job["cached_node_ids"]:
                                    job["cached_node_ids"].append(str(nid))
                        job["last_progress_at"] = time.time()
                        if ev.get("node") is not None:
                            job["current_node"] = ev.get("title") or ev.get("node")
                        save_state()

                    if typ == "output" and ev.get("url"):
                        phone_url = normalize_output_url(ev["url"], service)
                        outputs.append(phone_url)
                        job.setdefault("outputs", [])
                        if phone_url not in job["outputs"]:
                            job["outputs"].append(phone_url)
                        save_state()

                    if typ == "envelope":
                        await watcher.wait()
                        return {"ok": bool(ev.get("ok")), "error": ev.get("error"), "outputs": outputs}

            if watcher.returncode is not None:
                break

            if time.monotonic() - last_progress > soft:
                job["status"] = "soft_timeout_cancelling"
                save_state()
                job[f"{service}_cancel_reason"] = "timeout"
                confirmed = await cancel_prompt(service, prompt_id)
                _record_cancel_confirmation(job, service, prompt_id, confirmed)
                try:
                    await asyncio.wait_for(watcher.wait(), timeout=job["context"]["hard_restart_after_seconds"])
                except asyncio.TimeoutError:
                    job["status"] = "hard_timeout_restarting_comfy"
                    save_state()
                    watcher.kill()
                    await watcher.wait()
                    await restart_comfy(service)
                return {"ok": False, "stuck": True, "error": "stuck_timeout", "outputs": outputs}

        return {"ok": False, "uncertain": True, "error": "watcher_exited_without_terminal_envelope", "outputs": outputs}
    finally:
        try:
            await _terminate_subprocess(watcher)
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)


async def _prepare_workflow_api(workflow: Path, service: str, *, preview_envelope: dict | None = None) -> Any:
    """Convert without submitting, then validate/prune against this worker."""
    from .workflow_conversion import prepare_api_prompt

    envelope = preview_envelope if preview_envelope is not None else await run_cli_envelope(
        service, "run", "--workflow", str(workflow), "--print-prompt"
    )
    data = envelope.get("data")
    if not isinstance(data, dict) or data.get("status") != "preview" or not isinstance(data.get("prompt"), dict):
        raise RuntimeError("comfy-cli did not return a non-submitting preview API graph")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{service_url(service)}/object_info")
        response.raise_for_status()
        catalog = response.json()
    approved = None
    if service == "render":
        ui_workflow = json.loads(workflow.read_text(encoding="utf-8"))
        override = node_by_id(ui_workflow, 2632)
        approved = override.get("widgets_values_named", {}).get("positive")
        if approved is None:
            approved = override["widgets_values"][0]
    prepared = prepare_api_prompt(data["prompt"], catalog, phase=service, approved_prompt=approved)
    api_path = workflow.with_suffix(".api.json")
    catalog_path = workflow.with_suffix(".catalog.json")
    _atomic_json_write(api_path, prepared)
    _atomic_json_write(catalog_path, catalog)
    validation = await run_cli_envelope(
        service, "workflow", "validate", "--workflow", str(api_path), "--input", str(catalog_path)
    )
    verdict = validation.get("data")
    if not isinstance(verdict, dict) or verdict.get("valid") is not True or verdict.get("error_count") != 0:
        raise RuntimeError("Prepared API workflow failed schema/input validation")
    if verdict.get("spends_credits") or verdict.get("partner_nodes"):
        raise RuntimeError("External paid API nodes are outside the local H3 execution contract")
    return prepared


async def _dispatch_prepared_workflow(prepared: Any, workflow: Path, service: str, prompt_id: str) -> str:
    """The only submission boundary. ComfyUI 0.38 supports this durable UUID."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{service_url(service)}/prompt", json={"prompt": prepared, "prompt_id": prompt_id, "client_id": prompt_id}
        )
    if 400 <= response.status_code < 500 and response.status_code != 408:
        raise SubmissionRejected(f"ComfyUI rejected the workflow (HTTP {response.status_code})")
    response.raise_for_status()
    data = response.json()
    return data["prompt_id"]


async def _submit_workflow(job: dict, workflow: Path, service: str) -> str:
    if state_load_error or vast_control_load_error:
        raise RuntimeError("Stored controller state is unavailable; submission is blocked")
    if not submissions_allowed():
        raise SubmissionRejected("Submissions are disabled in this CPU development environment")
    prepared = await _prepare_workflow_api(workflow, service)
    if job.get("cancel_requested"):
        raise SubmissionCancelled("job cancelled during preparation")
    if lifecycle_dispatch_blocked() or vast_control.get("plan") == "stop_after_current":
        raise SubmissionDeferred("shutdown armed during preparation")
    if service == "prompt" and job.get("review_required") and vast_control.get("plan") in {"stop_after_queue", "destroy_after_queue_keep_data"}:
        raise SubmissionSkippedForShutdown("unsubmitted review prompt skipped while draining queue")

    # Save the id and the possibility of acceptance BEFORE the network operation.
    # A timeout/crash must never create a second paid request with a fresh id.
    prompt_id = str(uuid.uuid4())
    job[f"{service}_prompt_id"] = prompt_id
    job["prompt_id"] = prompt_id
    job["active_service"] = service
    job[f"{service}_submission_attempted"] = True
    job[f"{service}_submission_state"] = "attempting"
    job["status"] = f"{service}_submitting"
    save_state()
    try:
        accepted_id = await _dispatch_prepared_workflow(prepared, workflow, service, prompt_id)
        if accepted_id != prompt_id:
            job[f"{service}_acknowledged_prompt_id"] = accepted_id
            raise SubmissionUncertain("ComfyUI acknowledgement did not match the durable prompt id")
    except SubmissionRejected:
        job[f"{service}_submission_state"] = "rejected"
        save_state()
        raise
    except Exception as error:
        job[f"{service}_submission_state"] = "uncertain"
        _preserve_recovery(job, service, f"Submission acknowledgement uncertain: {error}")
        raise SubmissionUncertain(str(error)) from error
    job[f"{service}_submission_state"] = "accepted"
    job["status"] = f"{service}_running"
    save_state()
    return prompt_id


async def submit_and_watch(job: dict, workflow: Path, service: str, approved_prompt: str | None = None) -> dict:
    job["active_service"] = service
    job["status"] = f"{service}_preparing"
    job[f"{service}_submission_state"] = "preparing"
    save_state()
    wf_path = patch_workflow(job, workflow, approved_prompt=approved_prompt)
    try:
        prompt_id = await _submit_workflow(job, wf_path, service)
    except SubmissionUncertain:
        return {"ok": False, "uncertain": True}
    except SubmissionCancelled:
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        save_state()
        return {"ok": False, "cancelled": True}
    except SubmissionDeferred:
        job["status"] = "prompt_queued" if service == "prompt" else (
            "render_queued_review" if job.get("approved_final_prompt") else "render_queued_auto"
        )
        save_state()
        return {"ok": False, "deferred": True}
    except SubmissionSkippedForShutdown:
        job["status"] = "review_skipped_shutdown"
        job["finished_at"] = time.time()
        save_state()
        return {"ok": False, "cancelled": True}
    if job.get("cancel_requested"):
        try:
            confirmed = await cancel_prompt(service, prompt_id)
            _record_cancel_confirmation(job, service, prompt_id, confirmed)
        except Exception as error:
            job["cancel_warning"] = str(error)
            save_state()
    return await watch_prompt(job, prompt_id, service)


async def _finish_watched_job(job: dict, service: str, result: dict) -> None:
    if result.get("deferred") or result.get("cancelled") or result.get("uncertain"):
        if result.get("uncertain"):
            _preserve_recovery(job, service, result.get("error") or job.get("recovery_warning") or "Execution outcome is unknown")
        return
    pid = _known_prompt_id(job, service)
    try:
        history = await fetch_history(service, str(pid))
    except Exception as error:
        _preserve_recovery(job, service, f"Execution history unavailable: {error}")
        return
    if not _apply_history_outcome(job, service, history):
        _preserve_recovery(job, service, "Execution has no settled history; waiting for reconciliation")


async def generate_prompt_candidate(job: dict) -> None:
    if job.get("cancel_requested"):
        job["status"] = "cancelled"
        save_state()
        return
    res = await submit_and_watch(job, PROMPT_ONLY, "prompt")
    await _finish_watched_job(job, "prompt", res)



def instance_id_from_env() -> str | None:
    raw = os.getenv("CONTAINER_ID") or os.getenv("VAST_CONTAINERLABEL")
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith("C."):
        raw = raw[2:]
    return raw or None


async def run_vast_cli(*args: str, expect_json: bool = False) -> Any:
    cmd = [VAST_CLI, *args]
    api_key = os.getenv("CONTAINER_API_KEY", "").strip()
    if api_key:
        cmd.extend(["--api-key", api_key])
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    sout = out.decode(errors="replace").strip()
    serr = err.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(serr or sout or f"vastai exited {proc.returncode}")
    if expect_json:
        if not sout:
            return {}
        try:
            return json.loads(sout)
        except Exception:
            # Some versions may prepend text; parse last JSON-looking line.
            for line in reversed(sout.splitlines()):
                try:
                    return json.loads(line)
                except Exception:
                    pass
            raise RuntimeError(f"Could not parse vastai JSON: {sout[:500]}")
    return sout


def queue_busy() -> bool:
    active = {
        "prompt_queued", "prompt_running",
        "render_queued_auto", "render_queued_review", "render_running",
        "soft_timeout_cancelling", "hard_timeout_restarting_comfy",
        "recovery_prompt", "recovery_render",
        "prompt_preparing", "render_preparing", "prompt_submitting", "render_submitting",
        "prompt_submission_uncertain", "render_submission_uncertain", "cancelling",
    }
    return any(j.get("status") in active for j in queue)


def current_work_running() -> bool:
    active = {
        "prompt_running", "render_running",
        "soft_timeout_cancelling", "hard_timeout_restarting_comfy",
        "recovery_prompt", "recovery_render",
        "prompt_preparing", "render_preparing", "prompt_submitting", "render_submitting",
        "prompt_submission_uncertain", "render_submission_uncertain", "cancelling",
    }
    return any(j.get("status") in active for j in queue)


def skip_unstarted_review_prompts_for_shutdown() -> int:
    count = 0
    for j in queue:
        if j.get("status") == "prompt_queued" and j.get("review_required"):
            j["status"] = "review_skipped_shutdown"
            j["finished_at"] = time.time()
            count += 1
    if count:
        save_state()
    return count


def queue_finished_for_shutdown() -> bool:
    # pending_review is intentionally ignored: it must never block shutdown.
    blocking = {
        "prompt_queued", "prompt_running",
        "render_queued_auto", "render_queued_review", "render_running",
        "soft_timeout_cancelling", "hard_timeout_restarting_comfy",
        "recovery_prompt", "recovery_render",
        "prompt_preparing", "render_preparing", "prompt_submitting", "render_submitting",
        "prompt_submission_uncertain", "render_submission_uncertain", "cancelling",
    }
    return not any(j.get("status") in blocking for j in queue)


def _mount_info(path: Path) -> tuple[str | None, str | None]:
    try:
        p = subprocess.run(
            ["findmnt", "-T", str(path), "-n", "-o", "SOURCE,TARGET"],
            capture_output=True, text=True, timeout=5
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None, None
        bits = p.stdout.strip().split(None, 1)
        return bits[0], bits[1] if len(bits) > 1 else None
    except Exception:
        return None, None


def persistent_storage_status() -> dict[str, Any]:
    root = Path(H3_PERSISTENT_ROOT).resolve() if H3_PERSISTENT_ROOT else None
    result = {
        "configured": bool(root),
        "mode": H3_PERSISTENCE_MODE or None,
        "root": str(root) if root else None,
        "exists": False,
        "verified_separate_mount": False,
        "safe_for_destroy_keep_data": False,
        "mount_source": None,
        "mount_target": None,
        "protected_paths": {},
        "reason": None,
    }
    if not root or not root.exists():
        result["reason"] = "persistent root is not configured or does not exist"
        return result

    result["exists"] = True
    source, target = _mount_info(root)
    root_source, _ = _mount_info(Path("/"))
    result["mount_source"] = source
    result["mount_target"] = target
    result["verified_separate_mount"] = bool(source and root_source and source != root_source)

    # "Keep data" means these specific assets must live on the verified volume.
    protected = {
        "controller_state": STATE.resolve(),
        "models": COMFY_MODELS_DIR.resolve(),
        "outputs": COMFY_OUTPUT_DIR.resolve(),
        "inputs": COMFY_INPUT_DIR.resolve(),
    }
    all_on_volume = True
    for name, path in protected.items():
        p_source, p_target = _mount_info(path if path.exists() else path.parent)
        same_mount = bool(source and p_source == source)
        try:
            under_root = path == root or root in path.parents
        except Exception:
            under_root = False
        ok = same_mount and under_root
        result["protected_paths"][name] = {
            "path": str(path),
            "exists": path.exists(),
            "mount_source": p_source,
            "mount_target": p_target,
            "under_persistent_root": under_root,
            "verified": ok,
        }
        all_on_volume = all_on_volume and ok

    result["safe_for_destroy_keep_data"] = (
        H3_PERSISTENCE_MODE == "volume"
        and result["verified_separate_mount"]
        and all_on_volume
    )
    if not result["safe_for_destroy_keep_data"]:
        if H3_PERSISTENCE_MODE != "volume":
            result["reason"] = "H3_PERSISTENCE_MODE is not 'volume'"
        elif not result["verified_separate_mount"]:
            result["reason"] = "persistent root is not a separately mounted filesystem"
        elif not all_on_volume:
            result["reason"] = "state/models/outputs/inputs are not all on the verified persistent volume"
    return result


def disk_status() -> dict[str, Any]:
    target = H3_PERSISTENT_ROOT or "/workspace"
    try:
        u = shutil.disk_usage(target)
        return {
            "path": target,
            "total_gb": round(u.total / 1024**3, 1),
            "used_gb": round((u.total-u.free) / 1024**3, 1),
            "free_gb": round(u.free / 1024**3, 1),
            "used_pct": round((u.total-u.free) * 100 / u.total, 1) if u.total else 0,
        }
    except Exception as e:
        return {"path": target, "error": repr(e)}


def gpu_status() -> dict[str, Any]:
    try:
        p = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8
        )
        if p.returncode != 0 or not p.stdout.strip():
            return {"error": p.stderr.strip() or "nvidia-smi failed"}
        vals = [x.strip() for x in p.stdout.splitlines()[0].split(",")]
        return {
            "name": vals[0],
            "util_pct": float(vals[1]),
            "vram_used_mb": float(vals[2]),
            "vram_total_mb": float(vals[3]),
            "temp_c": float(vals[4]),
            "power_w": float(vals[5]),
        }
    except Exception as e:
        return {"error": repr(e)}


async def vast_instance_status() -> dict[str, Any]:
    iid = instance_id_from_env()
    persistence, disk, gpu = await asyncio.gather(
        asyncio.to_thread(persistent_storage_status), asyncio.to_thread(disk_status), asyncio.to_thread(gpu_status),
    )
    base = {
        "instance_id": iid,
        "cli_available": shutil.which(VAST_CLI) is not None,
        "session_started_at": SESSION_STARTED_AT,
        "session_hours": round((time.time() - SESSION_STARTED_AT) / 3600, 3),
        "plan": dict(vast_control),
        "persistence": persistence,
        "disk": disk,
        "gpu": gpu,
        "state": state_diagnostics(),
    }
    if not iid:
        base["error"] = "No CONTAINER_ID/VAST_CONTAINERLABEL found"
        return base
    if not base["cli_available"]:
        base["error"] = "vastai CLI not installed"
        return base
    try:
        data = await run_vast_cli("show", "instance", iid, "--raw", expect_json=True)
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            data = {}
        base["instance"] = data
        base["vast_states"] = {
            "intended_status": data.get("intended_status"),
            "actual_status": data.get("actual_status"),
            "cur_state": data.get("cur_state"),
            "next_state": data.get("next_state"),
        }
        dph = data.get("dph_total") or data.get("dph_base") or data.get("price")
        try:
            dph = float(dph) if dph is not None else None
        except Exception:
            dph = None
        base["hourly_usd"] = dph
        base["estimated_session_compute_usd"] = (
            round(dph * base["session_hours"], 3) if dph is not None else None
        )
    except Exception as e:
        base["error"] = repr(e)
    return base


async def delayed_instance_action(action: str, delay: float = 2.0, *, require_persistence: bool = False) -> None:
    await asyncio.sleep(delay)
    if not submissions_allowed():
        vast_control["plan"] = "action_failed"
        vast_control["reason"] = "Lifecycle actions are disabled in this CPU development environment"
        save_vast_control()
        return
    if action == "destroy" and require_persistence:
        storage = await asyncio.to_thread(persistent_storage_status)
        if not storage["safe_for_destroy_keep_data"]:
            vast_control["plan"] = "blocked_unsafe_persistence"
            vast_control["reason"] = "Persistent storage is no longer verified; destroy was not dispatched"
            save_vast_control()
            return
    iid = instance_id_from_env()
    if not iid:
        vast_control["plan"] = "action_failed"
        vast_control["reason"] = "instance id unavailable"
        save_vast_control()
        return
    vast_control["last_action"] = action
    vast_control["last_action_at"] = time.time()
    save_vast_control()
    try:
        if action == "stop":
            await run_vast_cli("stop", "instance", iid)
        elif action == "destroy":
            await run_vast_cli("destroy", "instance", iid, "-y")
        else:
            raise ValueError(action)
    except Exception as e:
        # If the process survives, surface the failure instead of leaving an eternal "executing_*".
        vast_control["plan"] = "action_failed"
        vast_control["reason"] = repr(e)
        save_vast_control()


def _schedule_instance_action(action: str, delay: float = 1.5) -> None:
    global lifecycle_action_task
    if lifecycle_action_task and not lifecycle_action_task.done():
        raise HTTPException(409, "a lifecycle action is already executing")
    lifecycle_action_task = asyncio.create_task(delayed_instance_action(
        action, delay, require_persistence=bool(vast_control.get("require_persistence")),
    ))


async def restart_local_service(service: str) -> None:
    if service not in ("render", "prompt"):
        raise ValueError(service)
    proc = await asyncio.create_subprocess_exec(
        "bash", SERVICE_CTL, "restart", service,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace") or out.decode(errors="replace"))


async def vast_guard_worker() -> None:
    global last_busy_at
    while True:
        try:
            busy = queue_busy()
            if busy:
                last_busy_at = time.time()

            plan = vast_control.get("plan", "none")
            # The guard survives lifecycle failures. It never auto-retries a
            # failed/destructive action, or dispatches a second executing one.
            if lifecycle_dispatch_blocked() or not submissions_allowed():
                await asyncio.sleep(10)
                continue

            # Stop-after-current prevents new renders in pick_next_render_job().
            if plan == "stop_after_current" and not current_work_running():
                vast_control["plan"] = "executing_stop"
                save_vast_control()
                _schedule_instance_action("stop")

            if plan in ("stop_after_queue", "destroy_after_queue_keep_data") and queue_finished_for_shutdown():
                action = "stop"
                if plan == "destroy_after_queue_keep_data":
                    if not persistent_storage_status()["safe_for_destroy_keep_data"]:
                        vast_control["plan"] = "blocked_unsafe_persistence"
                        vast_control["reason"] = "Persistent volume is not verified as a separate mount"
                        save_vast_control()
                    else:
                        action = "destroy"
                        vast_control["plan"] = "executing_destroy"
                        save_vast_control()
                        _schedule_instance_action(action)
                else:
                    vast_control["plan"] = "executing_stop"
                    save_vast_control()
                    _schedule_instance_action(action)

            if lifecycle_dispatch_blocked():
                await asyncio.sleep(10)
                continue

            idle_minutes = int(vast_control.get("idle_minutes") or 0)
            if idle_minutes > 0 and not busy:
                if time.time() - last_busy_at >= idle_minutes * 60:
                    vast_control["plan"] = "executing_idle_stop"
                    vast_control["reason"] = f"idle for {idle_minutes} min"
                    save_vast_control()
                    _schedule_instance_action("stop")

            guard = float(vast_control.get("cost_guard_usd") or 0)
            if guard > 0 and vast_control.get("plan") in ("none", None):
                st = await vast_instance_status()
                est = st.get("estimated_session_compute_usd")
                if est is not None and est >= guard and vast_control.get("plan") in ("none", None) and not lifecycle_dispatch_blocked():
                    vast_control["plan"] = "stop_after_current"
                    vast_control["reason"] = f"estimated session compute cost reached ${est:.2f}"
                    vast_control["armed_at"] = time.time()
                    save_vast_control()
        except Exception as e:
            vast_control["guard_error"] = repr(e)
            save_vast_control()
        await asyncio.sleep(10)


def pick_next_prompt_job() -> dict | None:
    if lifecycle_dispatch_blocked() or service_recovering("prompt"):
        return None
    if vast_control.get("plan") in {"stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data"}:
        # auto prompts may already be queued for stop-after-queue, but review generation is intentionally skipped when armed.
        pending_auto = [j for j in queue if j.get("status") == "prompt_queued" and not j.get("review_required")]
        if vast_control.get("plan") == "stop_after_current":
            return None
        if pending_auto:
            pending_auto.sort(key=lambda j: (j["batch_seq"], j["candidate_index"]))
            return pending_auto[0]
        return None
    pending = [j for j in queue if j.get("status") == "prompt_queued"]
    if not pending:
        return None
    # Keep renderer fed: all auto candidates (#1-5) outrank review candidates (#6-10),
    # even when a newer batch arrives. Pending review never blocks the next auto five.
    pending.sort(key=lambda j: (
        1 if j.get("review_required") else 0,
        j["batch_seq"],
        j["candidate_index"],
    ))
    return pending[0]


def pick_next_render_job() -> dict | None:
    if lifecycle_dispatch_blocked() or service_recovering("render") or vast_control.get("plan") == "stop_after_current":
        return None
    candidates = [j for j in queue if j.get("status") in ("render_queued_review", "render_queued_auto")]
    if not candidates:
        return None
    candidates.sort(key=lambda j: (
        0 if j["status"] == "render_queued_review" else 1,
        j.get("approved_at", j.get("prompt_ready_at", j["created_at"])),
        j["batch_seq"],
        j["candidate_index"],
    ))
    return candidates[0]


async def render_job(job: dict) -> None:
    if job.get("cancel_requested"):
        job["status"] = "cancelled"
        save_state()
        return
    approved = job.get("approved_final_prompt") or job.get("final_h3_prompt")
    if not approved:
        job["status"] = "render_failed"
        job["error"] = "missing final prompt"
        save_state()
        return

    job["render_started_at"] = time.time()
    res = await submit_and_watch(job, MASTER, "render", approved_prompt=approved)
    await _finish_watched_job(job, "render", res)


def _handle_worker_error(job: dict, service: str, error: Exception) -> None:
    if job.get(f"{service}_submission_state") in {"attempting", "accepted", "uncertain"}:
        _preserve_recovery(job, service, f"Execution outcome could not be confirmed: {error}")
    else:
        job["status"] = "cancelled" if job.get("cancel_requested") else f"{service}_failed"
        job["error"] = str(error)
        job["finished_at"] = time.time()
        save_state()


async def prompt_worker() -> None:
    while True:
        job = pick_next_prompt_job()
        if job:
            try:
                await generate_prompt_candidate(job)
            except Exception as e:
                _handle_worker_error(job, "prompt", e)
        else:
            await asyncio.sleep(0.75)


async def render_worker() -> None:
    while True:
        job = pick_next_render_job()
        if job:
            try:
                await render_job(job)
            except Exception as e:
                _handle_worker_error(job, "render", e)
        else:
            await asyncio.sleep(0.75)


async def _recover_one_job(job: dict, service: str) -> None:
    recovering_services.add(service)
    try:
        await _recover_one_job_impl(job, service)
    finally:
        recovering_services.discard(service)


async def _recover_one_job_impl(job: dict, service: str) -> None:
    pid = _known_prompt_id(job, service)
    attempted = job.get(f"{service}_submission_attempted") or job.get(f"{service}_submission_state") in {
        "attempting", "accepted", "uncertain",
    }
    previous = job.get("recovery_from_status", "")
    if not pid:
        if attempted or previous in {
            f"{service}_running", f"{service}_submitting", f"{service}_submission_uncertain",
            "soft_timeout_cancelling", "hard_timeout_restarting_comfy", "cancelling",
        }:
            job[f"{service}_submission_state"] = "uncertain"
            _preserve_recovery(job, service, "A prior submission has no durable id; manual reconciliation is required")
            return
        # A pre-submit job has no remote execution to reconcile. Preserve the
        # original no-ID contract without making any external request.
        job["status"] = "cancelled" if job.get("cancel_requested") else (
            "prompt_queued" if service == "prompt" else (
                "render_queued_review" if job.get("approved_final_prompt") else "render_queued_auto"
            )
        )
        job["recovered_at"] = time.time()
        save_state()
        return

    if not await wait_service_ready(service):
        _preserve_recovery(job, service, f"{service} service is unavailable; recovery will retry")
        return
    try:
        entry = await fetch_history(service, pid, retries=2, delay=0.2)
    except Exception as error:
        _preserve_recovery(job, service, f"History unavailable; recovery will retry: {error}")
        return
    if _apply_history_outcome(job, service, entry):
        job["recovered_at"] = time.time()
        save_state()
        return
    try:
        remote_queue = await fetch_service_queue(service)
        if not isinstance(remote_queue, dict) or not all(
            isinstance(remote_queue.get(key), list) for key in ("queue_running", "queue_pending")
        ):
            raise RuntimeError("invalid queue response")
    except Exception as error:
        _preserve_recovery(job, service, f"Queue unavailable; recovery will retry: {error}")
        return
    if _contains_prompt_id(remote_queue, pid):
        if job.get("cancel_requested"):
            try:
                confirmed = await cancel_prompt(service, pid)
                _record_cancel_confirmation(job, service, pid, confirmed)
            except Exception as error:
                job["cancel_warning"] = str(error)
        try:
            result = await watch_prompt(job, pid, service)
            await _finish_watched_job(job, service, result)
        except Exception as error:
            _preserve_recovery(job, service, f"Watcher failed; recovery will retry: {error}")
        return

    if job.get(f"{service}_cancel_confirmed") is True and job.get(f"{service}_cancel_confirmed_id") == pid:
        job["status"] = "cancelled" if job.get("cancel_requested") else (
            f"{service}_stuck_skipped" if job.get(f"{service}_cancel_reason") == "timeout" else "cancelled"
        )
        job[f"{service}_submission_state"] = "settled"
        job["finished_at"] = time.time()
        job.pop("recovery_warning", None)
        save_state()
        return

    # History may have expired or been reset after a completed paid render.
    # Successful empty reads prove only absence now, not non-acceptance before.
    # Never automatically resubmit a request that may have been accepted.
    _preserve_recovery(job, service, "Submitted id is absent from history and queue; manual reconciliation is required")


async def recover_jobs_after_controller_restart() -> None:
    async def recover_service(service: str) -> None:
        while True:
            if not (state_load_error or vast_control_load_error):
                job = next((j for j in queue if j.get("status") in {
                    f"recovery_{service}", f"{service}_submission_uncertain",
                }), None)
                if job is not None:
                    try:
                        await _recover_one_job(job, service)
                    except Exception as error:
                        _preserve_recovery(job, service, f"Recovery will retry: {error}")
            await asyncio.sleep(5)

    # Separate retry loops: a long render watcher or startup delay must not
    # prevent prompt-service reconciliation, and vice versa.
    tasks = [asyncio.create_task(recover_service(service), name=f"h3-recovery-{service}")
             for service in ("prompt", "render")]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@app.on_event("startup")
async def startup() -> None:
    global prompt_worker_task, render_worker_task, vast_guard_task, recovery_task, last_busy_at
    load_state()
    last_busy_at = time.time()
    recovery_task = asyncio.create_task(recover_jobs_after_controller_restart())
    prompt_worker_task = asyncio.create_task(prompt_worker())
    render_worker_task = asyncio.create_task(render_worker())
    vast_guard_task = asyncio.create_task(vast_guard_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    tasks = [task for task in (prompt_worker_task, render_worker_task, vast_guard_task, recovery_task, lifecycle_action_task)
             if task is not None]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config():
    return {
        "map": MAP,
        "render_comfy_url": RENDER_COMFY_URL,
        "prompt_comfy_url": PROMPT_COMFY_URL,
        "loras": MAP["nodes"]["first_pass_loras"]["entries"],
        "version": "pre-rental-final-rc5",
        "state": state_diagnostics(),
        "batch": {
            "prompts_per_batch": PROMPTS_PER_BATCH,
            "auto_approved": AUTO_APPROVED_PER_BATCH,
            "review": REVIEW_PER_BATCH,
        }
    }


@app.get("/api/proxy/{service}/{path:path}")
async def proxy_comfy(service: str, path: str, request: Request):
    if service not in ("prompt", "render"):
        raise HTTPException(404, "unknown service")
    base = service_url(service)
    target = f"{base}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"

    forwarded = {}
    if request.headers.get("range"):
        forwarded["Range"] = request.headers["range"]

    client = httpx.AsyncClient(timeout=None)
    stream_ctx = client.stream("GET", target, headers=forwarded)
    resp = await stream_ctx.__aenter__()

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await stream_ctx.__aexit__(None, None, None)
            await client.aclose()

    headers = {}
    for h in ("content-type", "content-length", "content-range", "accept-ranges", "content-disposition"):
        if h in resp.headers:
            headers[h] = resp.headers[h]
    return StreamingResponse(body(), status_code=resp.status_code, headers=headers)



@app.get("/api/vast/status")
async def api_vast_status():
    return await vast_instance_status()


@app.post("/api/vast/action")
async def api_vast_action(req: VastActionRequest):
    action = req.action

    if action == "stop_now" and req.confirm != "STOP":
        raise HTTPException(400, "Type STOP to confirm")
    if action == "destroy_now" and req.confirm != "DESTROY":
        raise HTTPException(400, "Type DESTROY to confirm")
    if state_load_error or vast_control_load_error:
        raise HTTPException(503, "Stored controller state is unavailable; repair it before changing lifecycle controls")

    action_inflight = lifecycle_action_task is not None and not lifecycle_action_task.done()
    if (action_inflight or str(vast_control.get("plan") or "").startswith("executing_")) and action not in {"set_idle_timer", "set_cost_guard"}:
        raise HTTPException(409, "a lifecycle action is already executing; it cannot be cancelled after dispatch")
    if action in {"stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data", "stop_now", "destroy_now"} and not submissions_allowed():
        raise HTTPException(409, "Lifecycle actions are disabled in this CPU development environment")

    if action == "cancel_plan":
        vast_control.update({"plan":"none","reason":None,"armed_at":None,"require_persistence":False})
        save_vast_control()
        return {"ok": True, "plan": vast_control}

    if action == "set_idle_timer":
        vast_control["idle_minutes"] = int(req.idle_minutes or 0)
        save_vast_control()
        return {"ok": True, "plan": vast_control}

    if action == "set_cost_guard":
        vast_control["cost_guard_usd"] = float(req.cost_guard_usd or 0)
        save_vast_control()
        return {"ok": True, "plan": vast_control}

    if action == "stop_after_current":
        vast_control.update({"plan":"stop_after_current","armed_at":time.time(),"reason":"user"})
        save_vast_control()
        return {"ok": True, "plan": vast_control}

    if action == "stop_after_queue":
        skipped = skip_unstarted_review_prompts_for_shutdown()
        vast_control.update({"plan":"stop_after_queue","armed_at":time.time(),"reason":f"user; skipped {skipped} unstarted review prompts"})
        save_vast_control()
        return {"ok": True, "plan": vast_control}

    if action == "destroy_after_queue_keep_data":
        ps = persistent_storage_status()
        if not ps["safe_for_destroy_keep_data"]:
            raise HTTPException(
                409,
                "Persistent storage is not verified. Configure H3_PERSISTENT_ROOT on a separate Vast volume before using keep-data destroy."
            )
        skipped = skip_unstarted_review_prompts_for_shutdown()
        vast_control.update({
            "plan":"destroy_after_queue_keep_data",
            "require_persistence": True,
            "armed_at":time.time(),
            "reason":f"user; skipped {skipped} unstarted review prompts"
        })
        save_vast_control()
        return {"ok": True, "plan": vast_control, "persistence": ps}

    if action == "stop_now":
        if req.confirm != "STOP":
            raise HTTPException(400, "Type STOP to confirm")
        vast_control.update({"plan":"executing_stop","armed_at":time.time(),"reason":"user immediate","require_persistence":False})
        save_vast_control()
        _schedule_instance_action("stop", 2.0)
        return {"ok": True, "message":"Instance stop scheduled. This panel will disconnect."}

    if action == "destroy_now":
        if req.confirm != "DESTROY":
            raise HTTPException(400, "Type DESTROY to confirm")
        vast_control.update({"plan":"executing_destroy","armed_at":time.time(),"reason":"user immediate","require_persistence":False})
        save_vast_control()
        _schedule_instance_action("destroy", 2.0)
        return {
            "ok": True,
            "message":"Instance destroy scheduled. Instance-local data will be lost; separately mounted persistent volumes are not deleted by this action."
        }

    raise HTTPException(400, f"Unknown Vast action: {action}")


@app.post("/api/services/{service}/restart")
async def api_restart_service(service: str):
    if service not in ("render", "prompt"):
        raise HTTPException(400, "service must be render or prompt")
    await restart_local_service(service)
    return {"ok": True, "service": service}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename or "upload.bin").name
    target = COMFY_INPUT_DIR / safe
    if target.exists():
        target = COMFY_INPUT_DIR / f"{target.stem}_{uuid.uuid4().hex[:8]}{target.suffix}"
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"filename": target.name}


@app.post("/api/batches")
async def create_batches(req: BatchRequest):
    if shutdown_armed():
        raise HTTPException(409, "shutdown plan is armed; cancel it before creating a new batch")
    if not req.prompt.strip():
        raise HTTPException(400, "scene prompt is empty")
    pictures, audio = validate_requested_assets(req)
    created_batches = []
    base_render_seed = req.seed if req.seed is not None else int(time.time_ns() & 0x7FFFFFFF)
    base_prompt_seed = int((time.time_ns() >> 8) & 0x7FFFFFFF)
    base_analysis_seed = int((time.time_ns() >> 16) & 0x7FFFFFFF)

    async with queue_lock:
        batch_seq_start = max([b.get("seq", 0) for b in batches] or [0]) + 1

        for batch_offset in range(req.batches):
            batch_id = uuid.uuid4().hex
            batch_seq = batch_seq_start + batch_offset
            context = {
                "prompt": req.prompt,
                "model": req.model,
                "soft_timeout_minutes": req.soft_timeout_minutes,
                "hard_restart_after_seconds": req.hard_restart_after_seconds,
                "pictures": pictures,
                "audio": audio,
                "loras": [x.model_dump() for x in req.loras],
            }

            analysis_seed = base_analysis_seed + batch_offset
            batch = {
                "id": batch_id,
                "seq": batch_seq,
                "created_at": time.time(),
                "context": context,
                "prompts_per_batch": PROMPTS_PER_BATCH,
                "auto_approved": AUTO_APPROVED_PER_BATCH,
                "review_required": REVIEW_PER_BATCH,
                "analysis_seed": analysis_seed,
            }
            batches.append(batch)
            created_batches.append(batch_id)

            for i in range(PROMPTS_PER_BATCH):
                render_seed = (
                    base_render_seed + (batch_offset * PROMPTS_PER_BATCH) + i
                    if req.random_each else base_render_seed
                )
                prompt_seed = base_prompt_seed + (batch_offset * PROMPTS_PER_BATCH * 2) + i * 2
                queue.append({
                    "id": uuid.uuid4().hex,
                    "batch_id": batch_id,
                    "batch_seq": batch_seq,
                    "candidate_index": i + 1,
                    "render_seed": render_seed,
                    "prompt_seed": prompt_seed,
                    "analysis_seed": analysis_seed,
                    "seed": render_seed,  # backwards-compatible UI field
                    "created_at": time.time(),
                    "status": "prompt_queued",
                    "review_required": i >= AUTO_APPROVED_PER_BATCH,
                    "render_priority": None,
                    "context": context,
                })
        save_state()

    return {"created_batches": created_batches}


@app.get("/api/jobs")
async def jobs():
    return {"jobs": queue, "batches": batches, "state": state_diagnostics()}


@app.get("/api/jobs/{controller_job_id}")
async def get_job(controller_job_id: str):
    return find_job(controller_job_id)


@app.get("/api/jobs/{controller_job_id}/prompts")
async def prompts(controller_job_id: str):
    job = find_job(controller_job_id)
    return {
        "user_prompt": job["context"]["prompt"],
        "visual_facts": job.get("visual_facts"),
        "expanded_intent": job.get("expanded_intent"),
        "reference_map": job.get("reference_map"),
        "creative_plan": job.get("creative_plan"),
        "final_h3_prompt": job.get("final_h3_prompt"),
        "approved_final_prompt": job.get("approved_final_prompt"),
        "status": job.get("status"),
        "review_required": job.get("review_required", False),
        "batch_seq": job.get("batch_seq"),
        "candidate_index": job.get("candidate_index"),
        "prompt_seed": job.get("prompt_seed"),
        "analysis_seed": job.get("analysis_seed"),
        "render_seed": job.get("render_seed"),
    }


@app.post("/api/jobs/{controller_job_id}/approve")
async def approve(controller_job_id: str, req: ApprovalRequest):
    if shutdown_armed():
        raise HTTPException(409, "shutdown plan is armed; cancel it before approving review prompts")
    job = find_job(controller_job_id)
    if job.get("status") != "pending_review":
        raise HTTPException(409, f"job is {job.get('status')}, not pending_review")
    final_prompt = req.final_prompt if req.final_prompt is not None else job.get("final_h3_prompt")
    if not final_prompt or not str(final_prompt).strip():
        raise HTTPException(400, "final prompt is empty")
    job["approved_final_prompt"] = str(final_prompt)
    job["approved_at"] = time.time()
    job["render_priority"] = 0
    job["status"] = "render_queued_review"
    save_state()
    return {"ok": True, "priority": "top"}


@app.post("/api/jobs/{controller_job_id}/reject")
async def reject(controller_job_id: str, req: RejectRequest):
    job = find_job(controller_job_id)
    if job.get("status") != "pending_review":
        raise HTTPException(409, f"job is {job.get('status')}, not pending_review")
    job["status"] = "rejected"
    job["rejected_at"] = time.time()
    job["reject_reason"] = req.reason
    save_state()
    return {"ok": True}


@app.post("/api/jobs/{controller_job_id}/cancel")
async def cancel(controller_job_id: str):
    job = find_job(controller_job_id)
    if job.get("status") in {"completed", "cancelled", "rejected", "prompt_failed", "render_failed", "prompt_stuck_skipped", "render_stuck_skipped", "review_skipped_shutdown"}:
        raise HTTPException(409, "job has already reached a terminal state")
    job["cancel_requested"] = True
    service = job.get("active_service")
    if job.get("status") in {"prompt_queued", "render_queued_auto", "render_queued_review", "pending_review", "prompt_preparing", "render_preparing"}:
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        save_state()
        return {"ok": True, "pending": False}
    # In-flight submit may be accepted after this request. Keep it active and
    # let the submitting coroutine cancel once the durable id is acknowledged.
    if job.get("status") in {"prompt_submitting", "render_submitting"}:
        save_state()
        return {"ok": True, "pending": True}
    if service in ("prompt", "render") and _known_prompt_id(job, service):
        cancelling_id = _known_prompt_id(job, service)
        try:
            confirmed = await cancel_prompt(service, cancelling_id)
            _record_cancel_confirmation(job, service, cancelling_id, confirmed)
        except Exception as e:
            job["cancel_warning"] = repr(e)
        # The watcher may have settled the job or entered the next phase during
        # the await. A late cancel acknowledgement must not erase that outcome.
        if job.get("active_service") == service and _known_prompt_id(job, service) == cancelling_id and job.get("status") in {
            f"{service}_running", "soft_timeout_cancelling", "hard_timeout_restarting_comfy", "cancelling",
        }:
            job["status"] = "cancelling"
    # A cancel acknowledgement is not proof of terminal GPU execution. The
    # watcher/recovery history settles it before shutdown or subsequent work.
    save_state()
    return {"ok": True, "pending": True}
