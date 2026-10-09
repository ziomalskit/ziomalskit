from __future__ import annotations
import asyncio, base64, copy, errno, hashlib, hmac, json, math, os, shutil, signal, subprocess, sys, time, uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlencode, parse_qs

if sys.version_info < (3, 11):
    raise RuntimeError("H3 runtime requires Python >=3.11")

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, Request, Query, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
WORKFLOWS = ROOT / "workflows"
CONFIG = ROOT / "config"
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)

MASTER = WORKFLOWS / "VAST_H3_MASTER_NATIVE_INT8_96GB.json"
PROMPT_ONLY = WORKFLOWS / "VAST_H3_PROMPT_ONLY_STAGE2.json"
MAP = json.loads((CONFIG / "VAST_H3_API_MAP.json").read_text(encoding="utf-8"))
from .production import read_profiles, read_loras, read_bridge, resolve_loras, patch_lora_slots, PROFILE_LABELS
from .analysis_cache import AnalysisCache, prompt_admission
from .v20 import compose as compose_v20, set_widget as set_v20_widget
from .writer_policy import MODEL_FIELDS, validate_stages, stage_routes, validate_writer_graph, final_answer
from .temporal import duration_identity, validate_duration, final_timeline

PRODUCTION = read_profiles(CONFIG / "production_profiles.json")
LORA_REGISTRY = read_loras(CONFIG / "lora_registry.json")
CONDITIONING_BRIDGE = read_bridge(CONFIG / "lora_registry.json")
analysis_cache = AnalysisCache()
overlap_telemetry = {"free_vram_mb": None, "sampled_at": 0.0}


def job_profile(job: dict) -> str:
    profile = job.get("profile")
    if profile not in PROFILE_LABELS or job.get("context", {}).get("profile") != profile:
        raise ValueError("job has no consistent persisted production profile; explicit migration is required")
    identity = job["context"].get("profile_identity")
    if not isinstance(identity, dict):
        raise ValueError("persisted writer identity missing; explicit reconciliation is required")
    if any(identity.get(key) != PRODUCTION["profiles"][profile][key] for key in MODEL_FIELDS):
        raise ValueError("persisted profile model routing changed; explicit reconciliation is required")
    validate_stages(identity.get("writer_stages"))
    if identity.get("writer_runtime") != PRODUCTION["writer_runtime"]:
        raise ValueError("persisted writer runtime changed; explicit reconciliation is required")
    if identity.get("manifest_identity") != manifest_identity(profile):
        raise ValueError("persisted model provenance changed; explicit reconciliation is required")
    validate_duration(job["context"])
    if job["context"]["duration_seconds"] != job["context"]["requested_duration_seconds"]:
        raise ValueError("persisted requested duration differs from job context")
    lora_identity = job["context"].get("lora_identity")
    if lora_identity is not None and any(key not in LORA_REGISTRY or LORA_REGISTRY[key]["filename"] != value for key, value in lora_identity.items()):
        raise ValueError("persisted LoRA routing changed; explicit reconciliation is required")
    bridge = job["context"].get("conditioning_bridge")
    if bridge is not None and (bridge.get("id") != CONDITIONING_BRIDGE["id"] or bridge.get("filename") != CONDITIONING_BRIDGE["filename"]):
        raise ValueError("persisted conditioning bridge routing changed")
    return profile


def profile_identity(profile: str) -> dict:
    return copy.deepcopy({**{key: PRODUCTION["profiles"][profile][key] for key in MODEL_FIELDS},
                          "writer_stages": PRODUCTION["profiles"][profile]["writer_stages"],
                          "writer_runtime": PRODUCTION["writer_runtime"], "manifest_identity": manifest_identity(profile)})


def manifest_identity(profile: str) -> str:
    manifest = json.loads((CONFIG / "models_manifest.json").read_text())
    facts = ("provider", "repository", "revision", "repository_path", "destination", "size_bytes", "sha256", "generation",
             "source_repository", "source_revision", "observed_source_revision", "llama_cpp_revision", "converter_revision", "converter_sha256")
    entries = [item for item in [*manifest["artifacts"], *manifest.get("pending_artifacts", [])]
               if item["required"] and (profile in item["profiles"] or "shared" in item["profiles"])]
    # GPU-validation flags and human diagnostics do not change artifact identity.
    identity = [{key: item.get(key) for key in facts} for item in sorted(entries, key=lambda item: item["destination"])]
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def production_workflow(job: dict) -> Path:
    return WORKFLOWS / PRODUCTION["profiles"][job_profile(job)]["render_template"]


def execution_workflow(job: dict, service: str) -> Path:
    if job.get("profile") in PROFILE_LABELS:
        return production_workflow(job)
    if os.getenv("H3_ALLOW_DIAGNOSTIC_SUBMISSIONS", "0") == "1":
        return PROMPT_ONLY if service == "prompt" else MASTER
    raise ValueError("legacy job requires explicit diagnostic mode or recreation with a production profile")


def batch_for_job(job: dict) -> dict | None:
    return next((batch for batch in batches if batch.get("id") == job.get("batch_id")), None)

# Stage 3 audited: prompt and render are truly separate ComfyUI services.
RENDER_COMFY_URL = os.getenv("RENDER_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
PROMPT_COMFY_URL = os.getenv("PROMPT_COMFY_URL", "http://127.0.0.1:8189").rstrip("/")
COMFY_INPUT_DIR = Path(os.getenv("COMFY_INPUT_DIR", "/workspace/ComfyUI/input"))
COMFY_ROOT = Path(os.getenv("COMFY_ROOT", "/workspace/ComfyUI"))
WORKSPACE = Path(os.getenv("WORKSPACE") or "/workspace")
COMFY_PYTHON = os.getenv("COMFY_PYTHON", sys.executable)
COMFY_OUTPUT_DIR = Path(os.getenv("COMFY_OUTPUT_DIR", str(COMFY_ROOT / "output")))
COMFY_MODELS_DIR = Path(os.getenv("COMFY_MODELS_DIR", str(COMFY_ROOT / "models")))
RENDER_RESTART_CMD = os.getenv("RENDER_RESTART_CMD", "supervisorctl restart comfyui-render")
PROMPT_RESTART_CMD = os.getenv("PROMPT_RESTART_CMD", "supervisorctl restart comfyui-prompt")
QUEUE_FILE = STATE / "queue.json"
QUEUE_PUBLICATION_FILE = STATE / "queue.publication.json"

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


def prompt_prefetch_target(value: str | None) -> int:
    """A small future-render buffer, never an unbounded preparation queue."""
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


H3_PROMPT_PREFETCH = prompt_prefetch_target(os.getenv("H3_PROMPT_PREFETCH", "3"))
H3_ENABLE_TERMINAL = os.getenv("H3_ENABLE_TERMINAL", "0").strip() == "1"

from .terminal import PTYSession, SessionTokens, TOKEN_TTL_SECONDS, TERMINAL_PROTOCOL, bridge_terminal, websocket_ticket

terminal_tokens = SessionTokens()
terminal_sessions: set[PTYSession] = set()
terminal_shutting_down = False

app = FastAPI(title="H3 Mobile Controller", version="1.0.0-pre-rental-final-rc4")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, error):
    # The default validation response echoes the offending input. Bare JSON
    # NaN/Infinity or overflowing numbers must yield 422, not a JSON encoder 500.
    return JSONResponse(status_code=422, content={"detail": finite_json(jsonable_encoder(error.errors()))})


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
    model_config = ConfigDict(extra="forbid")
    name: str = Field(validation_alias=AliasChoices("id", "name"))
    enabled: bool
    # Project-approved inclusive range, within ComfyUI v0.38's loader bounds.
    strength: float = Field(ge=-2, le=2, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        # Canonical identifiers, never local paths. Reject prefixes rather than
        # validating a basename and later patching a different identifier.
        if not value or value != Path(value).name or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("LoRA name must be a canonical filename")
        return value


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID | None = None
    prompt: str
    profile: str | None = None
    model: str | None = None
    duration_seconds: float = Field(20, ge=1, le=120, allow_inf_nan=False)
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
    control_token: str | None = None
    confirm: str | None = None
    idle_minutes: int | None = Field(None, ge=1, le=1440)
    cost_guard_usd: float | None = Field(None, ge=0.01, le=10000, allow_inf_nan=False)


queue: list[dict[str, Any]] = []
batches: list[dict[str, Any]] = []
request_results: dict[str, dict[str, Any]] = {}
queue_lock = asyncio.Lock()
prompt_worker_task: asyncio.Task | None = None
render_worker_task: asyncio.Task | None = None


vast_guard_task: asyncio.Task | None = None
lifecycle_action_task: asyncio.Task | None = None
recovery_task: asyncio.Task | None = None
recovering_services: set[str] = set()
recovery_counts: dict[str, int] = {}
service_restart_locks = {service: asyncio.Lock() for service in ("prompt", "render")}
service_maintenance: dict[str, str] = {}
service_maintenance_epochs = {service: 0 for service in ("prompt", "render")}
LIFECYCLE_CONTROL_EPOCH = uuid.uuid4().hex
state_load_error: str | None = None
queue_persistence_error: str | None = None
queue_persistence_epoch = 0
controller_started = False
ARMED_LIFECYCLE_PLANS = {"stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data"}
vast_control_load_error: str | None = None
last_busy_at = time.time()


class SubmissionUncertain(RuntimeError):
    """The request may have reached ComfyUI; automatic resubmission is unsafe."""


class SubmissionRejected(RuntimeError):
    """ComfyUI explicitly rejected the request without accepting a job."""


class SubmissionCancelled(RuntimeError):
    """Cancellation happened before dispatch; no request was sent."""


class SubmissionDeferred(RuntimeError):
    """Shutdown or service maintenance interrupted preparation; keep it queued."""


class SubmissionSkippedForShutdown(RuntimeError):
    """An unsubmitted review prompt is excluded from a draining queue."""


def submissions_allowed() -> bool:
    return os.getenv("H3_ALLOW_SUBMISSIONS", "1").strip().lower() not in {"0", "false", "no", "off"}


def lifecycle_dispatch_blocked() -> bool:
    plan = str(vast_control.get("plan") or "none")
    inflight = lifecycle_action_task is not None and not lifecycle_action_task.done()
    return bool(state_load_error or queue_persistence_error or vast_control_load_error or
                (controller_started and _worker_health()[1])) or inflight or plan.startswith("executing_") or plan in {
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


def _worker_health() -> tuple[dict[str, str], str | None]:
    tasks = {"prompt": prompt_worker_task, "render": render_worker_task,
             "recovery": recovery_task, "lifecycle_guard": vast_guard_task}
    workers = {name: "not_started" if task is None else "stopped" if task.done() else "running"
               for name, task in tasks.items()}
    unavailable = [name for name, status in workers.items() if status != "running"]
    return workers, "Required controller tasks unavailable: " + ", ".join(unavailable) if unavailable else None


def state_diagnostics() -> dict[str, Any]:
    lifecycle_error = vast_control_load_error or vast_control.get("persistence_error")
    if vast_control.get("plan") in {"action_failed", "action_interrupted", "blocked_unsafe_persistence"}:
        lifecycle_error = lifecycle_error or vast_control.get("reason") or "Lifecycle requires manual reconciliation"
    workers, worker_error = _worker_health()
    return {
        "ready": not (state_load_error or queue_persistence_error or lifecycle_error or worker_error),
        "queue_error": state_load_error,
        "persistence_error": queue_persistence_error,
        "worker_error": worker_error,
        "workers": workers,
        "lifecycle_error": lifecycle_error,
        "service_maintenance": dict(service_maintenance),
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
        "control_generation": 0,
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
        if type(default["control_generation"]) is not int or default["control_generation"] < 0:
            raise ValueError("invalid lifecycle control generation")
        if "armed_generation" in data and data["armed_generation"] is not None and (
            type(data["armed_generation"]) is not int or data["armed_generation"] < 0
        ):
            raise ValueError("invalid lifecycle safety generation")
        if default["plan"] in ARMED_LIFECYCLE_PLANS or default["plan"].startswith("executing_"):
            default.update(plan="action_interrupted", reason="Controller restarted; explicitly re-arm the lifecycle plan")
    except Exception as error:
        vast_control_load_error = f"Lifecycle state could not be loaded; existing file preserved: {error}"
    return default


vast_control: dict[str, Any] = load_vast_control()


def lifecycle_control_token() -> str:
    # The epoch rejects tokens from an earlier controller process; the durable
    # monotonic generation orders accepted client decisions within this process.
    return f"{LIFECYCLE_CONTROL_EPOCH}:{vast_control['control_generation']}"


def save_lifecycle_decision() -> None:
    vast_control["control_generation"] += 1
    save_vast_control()


def lifecycle_decision_response(**fields) -> dict:
    return {"ok": True, "control_token": lifecycle_control_token(), **fields}


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
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
    vast_control.pop("persistence_error", None)
    try:
        _atomic_json_write(VAST_CONTROL_FILE, vast_control)
    except Exception as error:
        # Even if disk writes fail, never leave an unowned executing action in
        # memory. An executing record on disk becomes action_interrupted on boot.
        vast_control.update({"plan": "action_failed", "reason": "Lifecycle state write failed; manual reconciliation required",
                             "persistence_error": repr(error)})
        raise


def _record_queue_persistence_failure(error: Exception) -> None:
    global queue_persistence_error, queue_persistence_epoch
    queue_persistence_error = f"Queue state write failed; dispatch is blocked: {error!r}"
    queue_persistence_epoch += 1
    if vast_control.get("plan") in ARMED_LIFECYCLE_PLANS or str(vast_control.get("plan", "")).startswith("executing_"):
        _lifecycle_failure("Queue persistence failed after lifecycle arm; explicit re-arm is required")


def _persist_queue_snapshot(next_queue: list[dict], next_batches: list[dict], *, publication: bool = False,
                            next_requests: dict | None = None) -> None:
    global queue_persistence_error
    if state_load_error:
        raise RuntimeError(state_load_error)
    try:
        if publication and next_requests is None:
            raise ValueError("A published mutation requires its durable idempotency state")
        # One atomic document is the commit boundary, including the response.
        # A crash can expose either snapshot; a retry of a committed key can
        # never create another batch. Legacy publication markers fail closed.
        _atomic_json_write(QUEUE_FILE, {"queue": next_queue, "batches": next_batches,
                                      "requests": request_results if next_requests is None else next_requests,
                                      "safety_generation": queue_persistence_epoch})
    except Exception as error:
        _record_queue_persistence_failure(error)
        raise
    queue_persistence_error = None


def save_state() -> None:
    _persist_queue_snapshot(queue, batches)


def _resume_queue_persistence() -> None:
    if queue_persistence_error and not state_load_error:
        # Never repair from the failed request's private staged snapshot.
        save_state()


def load_state() -> None:
    global queue, batches, request_results, state_load_error, queue_persistence_epoch
    state_load_error = None
    if QUEUE_PUBLICATION_FILE.exists():
        state_load_error = "Queue publication was interrupted; manual reconciliation is required; existing files preserved"
        return
    if not QUEUE_FILE.exists():
        return
    try:
        raw = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        # Reject both explicit NaN/Infinity and numeric overflow (e.g. 1e400)
        # before publishing any loaded records to HTTP or worker state.
        json.dumps(raw, allow_nan=False)
        if isinstance(raw, list):
            loaded_queue, loaded_batches = raw, []
            loaded_requests, generation = {}, 0
        else:
            if not isinstance(raw, dict) or not isinstance(raw.get("queue"), list) or not isinstance(raw.get("batches"), list):
                raise ValueError("queue state must contain queue and batches arrays")
            loaded_queue, loaded_batches = raw["queue"], raw["batches"]
            loaded_requests, generation = raw.get("requests", {}), raw.get("safety_generation", 0)
        if not all(isinstance(job, dict) and isinstance(job.get("id"), str) and job["id"] and isinstance(job.get("status"), str) for job in loaded_queue):
            raise ValueError("queue contains an invalid job record")
        if not all(isinstance(batch, dict) for batch in loaded_batches):
            raise ValueError("batches contains an invalid record")
        if type(generation) is not int or generation < 0 or not isinstance(loaded_requests, dict):
            raise ValueError("invalid queue idempotency/safety state")
        batch_ids = {batch.get("id") for batch in loaded_batches if isinstance(batch.get("id"), str)}
        profiles_by_batch = {}
        for batch in loaded_batches:
            if batch.get("profile") is not None:
                if batch["profile"] not in PROFILE_LABELS or batch.get("context", {}).get("profile") != batch["profile"]:
                    raise ValueError("batch persisted profile is inconsistent")
                profiles_by_batch[batch["id"]] = batch["profile"]
        for key, record in loaded_requests.items():
            if str(uuid.UUID(key)) != key or not isinstance(record, dict) or not isinstance(record.get("request_hash"), str):
                raise ValueError("invalid idempotency record")
            if len(record["request_hash"]) != 64 or any(c not in "0123456789abcdef" for c in record["request_hash"]):
                raise ValueError("invalid idempotency fingerprint")
            result = record.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("created_batches"), list) or not result["created_batches"] or any(
                not isinstance(batch_id, str) or batch_id not in batch_ids for batch_id in result["created_batches"]
            ):
                raise ValueError("idempotency result refers to an unavailable batch")
        for j in loaded_queue:
            # Legacy state remains available for UUID reconciliation. It cannot
            # acquire a new production identity from a mutable runtime default.
            if j.get("profile") is not None:
                job_profile(j)
                if j.get("batch_id") in profiles_by_batch and profiles_by_batch[j["batch_id"]] != j["profile"]:
                    raise ValueError("job persisted profile differs from batch")
            else:
                j["profile_migration_required"] = True
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
        queue, batches = loaded_queue, loaded_batches
        request_results, queue_persistence_epoch = loaded_requests, generation
    except Exception as error:
        state_load_error = f"Queue state could not be loaded; existing file preserved: {error}"


def find_job(job_id: str) -> dict:
    job = next((j for j in queue if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def node_by_id(wf: dict, node_id: int) -> dict:
    matches = [node for node in wf["nodes"] if node.get("id") == node_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one UI node at {node_id}, found {len(matches)}")
    return matches[0]


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
    if preset in PRODUCTION["profiles"]:
        return COMFY_MODELS_DIR / "diffusion_models" / PRODUCTION["profiles"][preset]["checkpoint"]
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

    model_path = model_path_for_preset(req.profile or req.model or PRODUCTION["default_profile"])
    if not model_path.is_file():
        raise HTTPException(409, f"selected render model is not installed: {model_path.name}")

    if len({lora.name for lora in req.loras}) != len(req.loras):
        raise HTTPException(400, "Duplicate LoRA settings are ambiguous")
    try:
        resolved = resolve_loras(LORA_REGISTRY, req.profile or req.model or PRODUCTION["default_profile"], [item.model_dump() for item in req.loras])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for lora in req.loras:
        if lora.enabled:
            lp = COMFY_MODELS_DIR / "loras" / next(item["filename"] for key, item in LORA_REGISTRY.items()
                if key == lora.name or item["filename"] == lora.name)
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
        expected_type = "AJCompilerTextProcessor" if nid == 4275 and wf.get("extra", {}).get("aj_production") else "LLMTextProcessor"
        if n.get("type") != expected_type:
            raise RuntimeError(f"Expected LLMTextProcessor at {nid}, got {n.get('type')}")
        n.setdefault("widgets_values_named", {})["seed"] = int(seed)
        if len(n.get("widgets_values", [])) <= 13:
            raise RuntimeError(f"LLMTextProcessor {nid} has unexpected widget layout")
        n["widgets_values"][13] = int(seed)


def patch_workflow(job: dict, workflow_template: Path, *, approved_prompt: str | None = None) -> Path:
    requested_phase = "prompt" if workflow_template == PROMPT_ONLY else "render"
    if job.get("profile") in PROFILE_LABELS and workflow_template in (MASTER, PROMPT_ONLY):
        workflow_template = production_workflow(job)
    is_production = workflow_template.name in {profile["render_template"] for profile in PRODUCTION["profiles"].values()}
    if is_production:
        if workflow_template != production_workflow(job):
            raise ValueError("workflow does not match job's persisted profile")
        wf = compose_v20(workflow_template, PRODUCTION, LORA_REGISTRY,
                         writer_stages=job["context"]["profile_identity"]["writer_stages"])
    else:
        # These explicitly addressed legacy templates are diagnostic artifacts.
        # Normal workers always select production_workflow(job).
        wf = json.loads(workflow_template.read_text(encoding="utf-8"))
    from .workflow_validation import validate_ui_workflow, lora_slots
    validate_ui_workflow(wf, patch_contract=True)
    ctx = job["context"]

    checkpoint = (PRODUCTION["profiles"][job_profile(job)]["checkpoint"] if is_production else
                  MAP["nodes"]["model_loader"]["alternatives"].get(ctx["model"]))
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
    if workflow_template == PROMPT_ONLY or is_production:
        patch_llm_pipeline_seeds(
            wf,
            int(job["analysis_seed"]),
            int(job["prompt_seed"]),
        )

    if is_production:
        patch_lora_slots(node_by_id(wf, 6164), LORA_REGISTRY,
                         resolve_loras(LORA_REGISTRY, job_profile(job), ctx.get("loras"), frozen=True))
        set_v20_widget(node_by_id(wf, 1731), "value", ctx["duration_seconds"])
        defaults = ctx.get("conditioning_bridge", CONDITIONING_BRIDGE["defaults"][job_profile(job)])
        set_v20_widget(node_by_id(wf, 7362), "enabled", defaults["enabled"])
        set_v20_widget(node_by_id(wf, 7362), "alpha", defaults["strength"])
        wf["extra"]["aj_production"].update(job_id=job["id"], phase=job.get("active_service", requested_phase))
    elif ctx.get("loras"):
        n = node_by_id(wf, 6164)
        settings = [LoraSetting.model_validate(value) for value in ctx["loras"]]
        if len({setting.name for setting in settings}) != len(settings):
            raise ValueError("Duplicate LoRA settings")
        supported = [item["lora"] for item in MAP["nodes"]["first_pass_loras"]["entries"]]
        slots = lora_slots(wf, supported)
        for setting in settings:
            if setting.name not in slots:
                raise ValueError(f"Unsupported LoRA: {setting.name}")
            _, named = slots[setting.name]
            rows = [item for item in n["widgets_values"] if isinstance(item, dict) and item.get("lora") == setting.name]
            if len(rows) != 1:
                raise ValueError(f"Ambiguous positional LoRA slot: {setting.name}")
            for item in (named, rows[0]):
                item.update(on=setting.enabled, strength=setting.strength)
        lora_slots(wf, supported)

    validate_ui_workflow(wf, patch_contract=True)

    phase = job.get("active_service", requested_phase) if is_production else requested_phase
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


async def _terminate_subprocess(process, *, drain_streams=(), grace_seconds: float = 5,
                                reap_seconds: float = 5) -> None:
    if process.returncode is not None:
        return

    async def cleanup() -> list[BaseException]:
        errors: list[BaseException] = []
        # Callers supply only streams whose earlier reader has finished. A
        # watcher already owns a stderr drainer, so it supplies stdout alone.
        drainers = [asyncio.create_task(_drain_pipe(stream)) for stream in drain_streams if stream is not None]

        def send(method) -> None:
            try:
                method()
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)

        async def wait(timeout: float, *, escalate: bool = False) -> None:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError as error:
                if not escalate:
                    errors.append(error)
            except BaseException as error:
                errors.append(error)

        send(process.terminate)
        await wait(grace_seconds, escalate=True)
        # TERM or wait errors must not bypass KILL. Retry an unsuccessful KILL
        # once while the original asyncio child remains owned and unreaped.
        for _attempt in range(2):
            if process.returncode is not None:
                break
            send(process.kill)
            await wait(reap_seconds)
        try:
            results = await asyncio.wait_for(asyncio.gather(*drainers, return_exceptions=True), timeout=reap_seconds)
            errors.extend(error for error in results if isinstance(error, BaseException))
        except BaseException as error:
            errors.append(error)
        finally:
            for drainer in drainers:
                drainer.cancel()
            await asyncio.gather(*drainers, return_exceptions=True)
        return errors

    owned_cleanup = asyncio.create_task(cleanup())
    cancellation = None
    while True:
        try:
            errors = await asyncio.shield(owned_cleanup)
            break
        except asyncio.CancelledError as error:
            # Repeated shutdown cancellation cannot abandon an owned child
            # between TERM and KILL or before its pipes are drained and reaped.
            cancellation = cancellation or error
            if owned_cleanup.done():
                try:
                    errors = owned_cleanup.result()
                except BaseException as cleanup_error:
                    errors = [cleanup_error]
                break
    if cancellation is not None:
        for error in errors:
            cancellation.add_note(f"Subprocess cleanup failed: {error!r}")
        raise cancellation
    if errors:
        for error in errors[1:]:
            errors[0].add_note(f"Subprocess cleanup failed: {error!r}")
        raise errors[0]


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
        primary_error = sys.exception()
        try:
            await _terminate_subprocess(proc, drain_streams=(proc.stdout, proc.stderr))
        except BaseException as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"CLI cleanup failed: {cleanup_error!r}")
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


_RESTART_GATE = """import os, sys
if sys.stdin.buffer.read(1) != b'G':
    sys.exit(0)
os.close(0)
os.open(os.devnull, os.O_RDONLY)
os.execvp(sys.argv[1], sys.argv[1:])
"""
_PIDFD_SIGNAL_PROCESS_GROUP = 4


def _require_restart_group_control() -> None:
    """Check the atomic group primitive before launching a restart command."""
    descriptor = os.pidfd_open(os.getpid())
    primary = None
    try:
        try:
            signal.pidfd_send_signal(descriptor, 0, None, _PIDFD_SIGNAL_PROCESS_GROUP)
        except OSError as error:
            # The flag is validated before group lookup. A controller which
            # is not its group's leader has no PID-as-PGID to signal, so ESRCH
            # also proves the primitive is supported without touching a peer.
            if error.errno != errno.ESRCH:
                raise RuntimeError("Safe service restart requires Linux pidfd process-group signalling (kernel >= 6.9)") from error
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f"Restart capability descriptor close failed: {error!r}")


async def _cleanup_owned_restart(proc, descriptor: int | None, communication: asyncio.Task | None,
                                 cooperative_stop: bool = False) -> list[BaseException]:
    errors: list[BaseException] = []

    def send(sig: int) -> bool:
        try:
            if descriptor is not None:
                # The fd identifies the original group even after its leader
                # has exited. Never signal a possibly reused numeric PGID.
                signal.pidfd_send_signal(descriptor, sig, None, _PIDFD_SIGNAL_PROCESS_GROUP)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
            return False
        return True

    if descriptor is None:
        # Capture failed before the gate was released; no restart command or
        # descendants can exist. Closing stdin also makes the gate exit.
        try:
            proc.stdin.close()
        except BaseException as error:
            errors.append(error)
    send(signal.SIGTERM)
    try:
        if cooperative_stop and communication is not None:
            # Shell exit is not helper completion. The stop owner holds these
            # pipes through TERM->KILL confirmation (two bounded 5s attempts).
            await asyncio.wait_for(asyncio.shield(communication), timeout=12)
        else:
            await asyncio.to_thread(proc.wait, timeout=2)
    except (subprocess.TimeoutExpired, asyncio.TimeoutError):
        pass
    except BaseException as error:
        errors.append(error)
    # A shell can exit on TERM while a descendant ignores it. Always address
    # the original group again, rather than testing only the shell returncode.
    if not send(signal.SIGKILL):
        # A transient signalling error cannot consume the only KILL attempt.
        send(signal.SIGKILL)
    try:
        await asyncio.to_thread(proc.wait, timeout=5)
    except BaseException as error:
        errors.append(error)
    if communication is not None:
        try:
            await asyncio.wait_for(asyncio.shield(communication), timeout=5)
        except BaseException as error:
            errors.append(error)
    return errors


async def _run_owned_restart(command: list[str], *, preserve_success: bool = False,
                             cooperative_stop: bool = False) -> tuple[int, bytes, bytes]:
    _require_restart_group_control()
    proc = None
    descriptor = None
    communication = None
    primary = None
    try:
        # Popen is kept unreaped until synchronous pidfd capture. The gate
        # cannot execute the command before capture, and exits on parent EOF.
        proc = subprocess.Popen([sys.executable, "-c", _RESTART_GATE, *command],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, close_fds=True)
        descriptor = os.pidfd_open(proc.pid)
        communication = asyncio.create_task(asyncio.to_thread(proc.communicate, b"G"))
        out, err = await asyncio.shield(communication)
        return proc.returncode, out, err
    except BaseException as error:
        primary = error
        raise
    finally:
        if proc is not None:
            cleanup = asyncio.create_task(_cleanup_owned_restart(proc, descriptor, communication, cooperative_stop))
            cleanup_cancellation = None
            while True:
                try:
                    errors = await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError as error:
                    # shutdown() must not finish while a queued restart can
                    # still acquire flock, even if cancellation is repeated.
                    cleanup_cancellation = cleanup_cancellation or error
                    if cleanup.done():
                        try:
                            errors = cleanup.result()
                        except BaseException as cleanup_error:
                            errors = [cleanup_error]
                        break
                except BaseException as error:
                    errors = [error]
                    break
            group_finished = False
            if preserve_success and primary is None and proc.returncode == 0 and descriptor is not None:
                try:
                    signal.pidfd_send_signal(descriptor, 0, None, _PIDFD_SIGNAL_PROCESS_GROUP)
                except ProcessLookupError:
                    group_finished = True
                except BaseException as error:
                    errors.append(error)
            for resource in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    resource.close()
                except BaseException as error:
                    errors.append(error)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    errors.append(error)
            authoritative = primary or cleanup_cancellation
            if authoritative is not None:
                for error in errors:
                    if error is not authoritative:
                        authoritative.add_note(f"Restart cleanup failed: {error!r}")
                if primary is None:
                    raise authoritative
            elif errors:
                if preserve_success and group_finished:
                    # A confirmed successful CLI response cannot become an
                    # uncertain remote operation because closing a local fd
                    # failed. The entire owned group is already gone.
                    vast_control['last_cli_cleanup_warning'] = (
                        'Confirmed CLI success; local cleanup diagnostics: '
                        + ', '.join(type(error).__name__ for error in errors))
                else:
                    for error in errors[1:]:
                        errors[0].add_note(f"Restart cleanup failed: {error!r}")
                    raise errors[0]


async def restart_comfy(service: str) -> None:
    returncode, _out, _err = await _run_owned_restart(["/bin/sh", "-c", restart_cmd(service)], cooperative_stop=True)
    if returncode != 0:
        raise RuntimeError(f"{service} restart failed with exit code {returncode}")


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


def render_duration_seconds(job: dict) -> float | None:
    start, finish = job.get("render_started_at"), job.get("finished_at")
    if (type(start) in (int, float) and type(finish) in (int, float)
            and math.isfinite(start) and math.isfinite(finish) and finish >= start):
        duration = finish - start
        return round(duration, 3) if math.isfinite(duration) else None
    return None


def _record_render_finish(job: dict, entry: dict) -> None:
    job["finished_at"] = time.time()
    if job.get("render_started_at") is None:
        # Older/recovered jobs may only have the ComfyUI execution timestamp
        # (milliseconds since epoch). Never invent a start from recovery time.
        for message in (entry.get("status") or {}).get("messages") or []:
            if (isinstance(message, (list, tuple)) and len(message) >= 2
                    and message[0] == "execution_start" and isinstance(message[1], dict)):
                timestamp = message[1].get("timestamp")
                if type(timestamp) in (int, float) and math.isfinite(timestamp) and timestamp > 0:
                    job["render_started_at"] = timestamp / 1000
                    break
    job["render_duration_seconds"] = render_duration_seconds(job)


def public_job(job: dict) -> dict:
    """Read-only result normalization, including pre-contract queue snapshots."""
    result = dict(job)
    if job.get("status") == "completed":
        videos = []
        for url in job.get("video_outputs", job.get("outputs", [])) or []:
            if not isinstance(url, str):
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            query = parse_qs(parsed.query)
            filename = query.get("filename", [""])[0]
            if (not parsed.scheme and not parsed.netloc and parsed.path == "/api/proxy/render/view"
                    and query.get("type", ["output"])[0] == "output"
                    and Path(filename).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
                    and url not in videos):
                videos.append(url)
        result["video_outputs"] = videos
        result["render_started_at"] = job.get("render_started_at")
        result["finished_at"] = job.get("finished_at")
        result["render_duration_seconds"] = render_duration_seconds(job)
    return result


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
        if service == "render":
            _record_render_finish(job, entry)
        else:
            job["finished_at"] = time.time()
    elif service == "prompt":
        capture_error = None
        try:
            captured = extract_prompt_texts(entry)
            if job.get("profile") in PROFILE_LABELS:
                job_profile(job)
                validate_writer_graph(_prompt_graph_from_history(entry), stage_routes(job["context"]["profile_identity"]))
                from .production_api import validate_temporal_graph
                validate_temporal_graph(_prompt_graph_from_history(entry), job["context"])
                final_timeline(captured.get("final_h3_prompt"), captured.get("creative_plan"), job["context"]["effective_duration_seconds"])
                job["temporal_guard_status"] = "verified"
        except ValueError as error:
            captured = {key: None for key in ("visual_facts", "expanded_intent", "reference_map", "creative_plan", "final_h3_prompt")}
            capture_error = f"Prompt capture failed: {error}"
            if job.get("profile") in PROFILE_LABELS:
                job["temporal_guard_status"] = "failed"
        job.update(captured)
        if job.get("profile") in PROFILE_LABELS:
            batch = batch_for_job(job)
            if batch is not None:
                analysis_cache.record(job, batch, captured, execution_epoch=job.get("prompt_cache_epoch", ""))
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
        elif not (job.get("final_h3_prompt") or "").strip():
            job["status"] = "prompt_failed"
            job["error"] = capture_error or "successful prompt execution did not capture a final H3 prompt"
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
        _record_render_finish(job, entry)
    job[f"{service}_submission_state"] = "settled"
    job.pop("recovery_warning", None)
    try:
        save_state()
    except Exception as error:
        # Remote execution is already settled. Keep this exact result for the
        # local persistence retry, instead of treating EIO as execution failure.
        job["persistence_warning"] = str(error)
        if not queue_persistence_error:
            _record_queue_persistence_failure(error)
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
    sources = {title: [] for title in wanted}
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        title = _title_for_api_node(node)
        if title in sources:
            sources[title].append(str(node_id))
    for title, ids in sources.items():
        if len(ids) > 1:
            raise ValueError(f"Ambiguous prompt capture title: {title}")
    for title, ids in sources.items():
        if not ids:
            continue
        node_id = ids[0]
        if graph[node_id].get("class_type") != "PreviewAny":
            raise ValueError(f"Unexpected prompt capture type: {title}")
        out = outputs.get(node_id)
        if not isinstance(out, dict) or "text" not in out or out["text"] is None:
            raise ValueError(f"Missing prompt capture output: {title}")
        raw = out["text"]
        value = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        result[wanted[title]] = value

    # Conversion normally preserves the top-level PreviewAny id. Use it as a
    # final-prompt fallback if subgraph title metadata was not retained.
    fallback_id = str(capture.get("fallback_top_level_final_node_id", ""))
    if not sources[capture["step4_final_prompt_title"]] and fallback_id:
        node = graph.get(fallback_id)
        if not isinstance(node, dict) or node.get("class_type") != "PreviewAny":
            raise ValueError("Final prompt fallback identity/type is unavailable")
        out = outputs.get(fallback_id, {})
        if isinstance(out, dict) and "text" in out and out["text"] is not None:
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
    watchdog_id = f"{service}_watchdog_prompt_id"
    progress_at = f"{service}_watchdog_last_progress_at"
    timeout_id = f"{service}_timeout_prompt_id"
    hard_deadline_key = f"{service}_hard_timeout_deadline"
    cancel_state_key = f"{service}_timeout_cancel_state"
    restart_state_key = f"{service}_hard_restart_state"
    if job.get(watchdog_id) != prompt_id:
        job[watchdog_id] = prompt_id
        job[progress_at] = time.time()
        save_state()
    stored_progress = job.get(progress_at)
    if type(stored_progress) not in (int, float) or not math.isfinite(stored_progress):
        raise RuntimeError("Stored watchdog progress time is invalid; manual reconciliation required")
    hard_deadline = None
    if job.get(timeout_id) == prompt_id:
        stored_deadline = job.get(hard_deadline_key)
        if type(stored_deadline) not in (int, float) or not math.isfinite(stored_deadline):
            raise RuntimeError("Stored watchdog deadline is invalid; manual reconciliation required")
        # Retain the durable deadline across watchers and controller restarts.
        # Wall-clock adjustments during this watcher cannot extend its budget.
        hard_deadline = time.monotonic() + max(0.0, stored_deadline - time.time())
        if job.get(restart_state_key) in {"attempting", "completed", "uncertain"}:
            return {"ok": False, "uncertain": True, "error": "hard_restart_already_attempted"}
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
        last_progress = time.monotonic() - max(0.0, time.time() - stored_progress)
        soft = job["context"]["soft_timeout_minutes"] * 60
        outputs: list[str] = []

        while True:
            if hard_deadline is not None and time.monotonic() >= hard_deadline:
                # A lost cancel reply can race a successful render. Reconcile
                # before restarting a service and possibly discarding its history.
                try:
                    entry = await asyncio.wait_for(fetch_history(service, prompt_id, retries=1), timeout=5)
                    if _apply_history_outcome(job, service, entry):
                        return {"ok": _history_outcome(entry) == "success", "settled": True, "outputs": outputs}
                    remote_queue = await asyncio.wait_for(fetch_service_queue(service), timeout=5)
                    if not isinstance(remote_queue, dict) or not all(
                        isinstance(remote_queue.get(key), list) for key in ("queue_running", "queue_pending")
                    ):
                        raise RuntimeError("invalid queue response")
                except Exception as error:
                    _preserve_recovery(job, service, f"Hard timeout ownership reconciliation failed: {error}")
                    return {"ok": False, "uncertain": True}
                if not _contains_prompt_id(remote_queue, prompt_id):
                    _preserve_recovery(job, service, "Hard timeout id is absent from queue; waiting for execution history")
                    return {"ok": False, "uncertain": True}
                job["status"] = "hard_timeout_restarting_comfy"
                # Commit before the restart side effect. An interrupted or
                # unacknowledged restart is reconciled, never automatically retried.
                job[restart_state_key] = "attempting"
                try:
                    save_state()
                except BaseException:
                    # This live process knows restart dispatch never began.
                    # Storage repair may retry escalation against the same deadline.
                    job.pop(restart_state_key, None)
                    raise
                try:
                    await restart_comfy(service)
                except BaseException as error:
                    job[restart_state_key] = "uncertain"
                    job["restart_warning"] = repr(error)
                    try:
                        save_state()
                    except Exception as write_error:
                        error.add_note(f"Restart outcome persistence failed: {write_error!r}")
                    raise
                job[restart_state_key] = "completed"
                save_state()
                return {"ok": False, "stuck": True, "error": "stuck_timeout", "outputs": outputs}
            try:
                read_timeout = 5 if hard_deadline is None else min(5, max(0.001, hard_deadline - time.monotonic()))
                line = await asyncio.wait_for(watcher.stdout.readline(), timeout=read_timeout)
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
                        job[progress_at] = job["last_progress_at"]
                        if ev.get("node") is not None:
                            job["current_node"] = ev.get("title") or ev.get("node")
                            if service == "render" and job.get("profile") in PROFILE_LABELS:
                                from .production_api import event_phase
                                job["render_phase"] = event_phase(str(ev["node"]), job.get("render_phase_nodes", {}))
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

            if hard_deadline is None and time.monotonic() - last_progress > soft:
                job["status"] = "soft_timeout_cancelling"
                job[f"{service}_cancel_reason"] = "timeout"
                job[timeout_id] = prompt_id
                job[f"{service}_timeout_started_at"] = time.time()
                budget = job["context"]["hard_restart_after_seconds"]
                job[hard_deadline_key] = time.time() + budget
                hard_deadline = time.monotonic() + budget
                job[cancel_state_key] = "attempting"
                job.pop(restart_state_key, None)
                save_state()
                try:
                    confirmed = await asyncio.wait_for(
                        cancel_prompt(service, prompt_id), timeout=max(0.001, hard_deadline - time.monotonic())
                    )
                except Exception as error:
                    job[cancel_state_key] = "failed"
                    job["cancel_warning"] = repr(error)
                    try:
                        save_state()
                    except Exception as write_error:
                        error.add_note(f"Cancellation warning persistence failed: {write_error!r}")
                        raise error from write_error
                else:
                    job[cancel_state_key] = "acknowledged"
                    _record_cancel_confirmation(job, service, prompt_id, confirmed)
                    save_state()
                # Cancellation is best-effort cleanup. Keep observing against
                # the original deadline even if it failed or its reply was lost.

        return {"ok": False, "uncertain": True, "error": "watcher_exited_without_terminal_envelope", "outputs": outputs}
    finally:
        primary_error = sys.exc_info()[1]
        try:
            await _terminate_subprocess(watcher, drain_streams=(watcher.stdout,))
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(f"Watcher cleanup failed: {cleanup_error!r}")
            elif isinstance(cleanup_error, asyncio.CancelledError):
                # Shutdown must still propagate after the owned watcher has
                # been reaped, including when history already proved success.
                raise
            elif job.get(f"{service}_submission_state") == "settled":
                job["watcher_cleanup_warning"] = repr(cleanup_error)
                try:
                    save_state()
                except Exception:
                    # The authoritative remote result is already retained in
                    # RAM and queue persistence remains blocked until repaired.
                    pass
            else:
                raise
        finally:
            stderr_task.cancel()
            authoritative_error = primary_error or sys.exception()
            stderr_join = asyncio.gather(stderr_task, return_exceptions=True)
            join_cancellation = None
            while True:
                try:
                    await asyncio.shield(stderr_join)
                    break
                except asyncio.CancelledError as error:
                    join_cancellation = join_cancellation or error
                    if stderr_join.done():
                        break
            if join_cancellation is not None:
                if authoritative_error is not None:
                    authoritative_error.add_note(f"Watcher stderr cleanup cancelled: {join_cancellation!r}")
                else:
                    raise join_cancellation


async def _prepare_workflow_api(workflow: Path, service: str, *, preview_envelope: dict | None = None) -> Any:
    """Convert without submitting, then validate/prune against this worker."""
    from .workflow_conversion import prepare_api_prompt
    from .workflow_validation import validate_ui_workflow, validate_converted_widgets
    ui_workflow = json.loads(workflow.read_text(encoding="utf-8"))
    validate_ui_workflow(ui_workflow, patch_contract=True)

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
    validate_ui_workflow(ui_workflow, catalog, patch_contract=True)
    validate_converted_widgets(ui_workflow, data["prompt"], catalog)
    approved = None
    if service == "render":
        override = node_by_id(ui_workflow, 2632)
        approved = override.get("widgets_values_named", {}).get("positive")
        if approved is None:
            approved = override["widgets_values"][0]
    prepared = prepare_api_prompt(data["prompt"], catalog, phase=service, approved_prompt=approved)
    production = ui_workflow.get("extra", {}).get("aj_production")
    if production:
        job = find_job(production["job_id"])
        profile = job_profile(job)
        if production.get("profile") != profile or production.get("phase") != service:
            raise ValueError("prepared workflow/profile identity mismatch")
        from .production_api import phase_boundaries, reuse_analysis, frozen_duration, temporal_guards, validate_temporal_graph
        prepared = frozen_duration(prepared, job["context"], catalog)
        if service == "render":
            final_timeline(approved, job.get("creative_plan"), job["context"]["effective_duration_seconds"])
            values = PRODUCTION["profiles"][profile]
            for node, field, expected in (("4595:4529", "unet_name", values["checkpoint"]),
                ("4595:130", "clip_name", values["encoder"]), ("4595:121", "vae_name", values["video_vae"]),
                ("4595:122", "vae_name", values["audio_vae"])):
                if prepared.get(node, {}).get("inputs", {}).get(field) != expected:
                    raise ValueError("converted render loader differs from persisted production profile")
            prepared, mapping = phase_boundaries(prepared, catalog, PRODUCTION["profiles"][profile]["memory_policy"])
            job["render_phase_nodes"] = mapping
        else:
            batch = batch_for_job(job)
            job["prompt_kind"] = analysis_cache.classify(job, batch)
            if job["prompt_kind"] != "cold_analysis":
                prepared = reuse_analysis(prepared, batch["analysis"]["texts"], catalog)
            prepared = temporal_guards(prepared, catalog)
            validate_writer_graph(prepared, stage_routes(job["context"]["profile_identity"]))
            validate_temporal_graph(prepared, job["context"])
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
    if state_load_error or queue_persistence_error or vast_control_load_error or (controller_started and _worker_health()[1]):
        raise RuntimeError("Stored controller state is unavailable; submission is blocked")
    if not submissions_allowed():
        raise SubmissionRejected("Submissions are disabled in this CPU development environment")
    maintenance_epoch = service_maintenance_epochs[service]
    if service in service_maintenance:
        raise SubmissionDeferred("service maintenance is in progress")
    production = job.get("profile") in PROFILE_LABELS
    if not production and os.getenv("H3_ALLOW_DIAGNOSTIC_SUBMISSIONS", "0") != "1":
        raise SubmissionRejected("legacy submission requires explicit diagnostic mode")
    if production:
        assert_phase_admission(job, service)
        await verify_production_files(job, service)
    cache_epoch = None
    if production and service == "prompt":
        await refresh_prompt_epoch()
        cache_epoch = analysis_cache.epoch
        job["prompt_cache_epoch"] = cache_epoch
        job["prompt_kind"] = analysis_cache.classify(job, batch_for_job(job))
    if production and service == "render":
        # Only the Render Worker owns this idle render process. Clear cached CPU
        # model objects at each new render/profile boundary, before preparation.
        # /free does not submit inference and never touches Prompt ComfyUI.
        await prepare_render_model_boundary()
    if production:
        assert_phase_admission(job, service)
    try:
        prepared = await _prepare_workflow_api(workflow, service)
    except Exception as error:
        if service in service_maintenance or maintenance_epoch != service_maintenance_epochs[service]:
            raise SubmissionDeferred("service restarted during preparation") from error
        raise
    if service in service_maintenance or maintenance_epoch != service_maintenance_epochs[service]:
        raise SubmissionDeferred("service restarted during preparation")
    if production and service == "prompt":
        await refresh_prompt_epoch()
        if cache_epoch != analysis_cache.epoch:
            raise SubmissionDeferred("Prompt Worker epoch changed during preparation")
    if service in service_maintenance or maintenance_epoch != service_maintenance_epochs[service]:
        raise SubmissionDeferred("service restarted during final epoch check")
    if production:
        # The last synchronous admission check precedes the durable UUID arm.
        # A renderer/prompt reservation made while conversion awaited cannot
        # create an unsafe cold-analysis/conditioning race.
        assert_phase_admission(job, service)
    if job.get("cancel_requested"):
        raise SubmissionCancelled("job cancelled during preparation")
    if lifecycle_dispatch_blocked() or vast_control.get("plan") == "stop_after_current":
        raise SubmissionDeferred("shutdown armed during preparation")
    if service == "prompt" and job.get("review_required") and vast_control.get("plan") in {"stop_after_queue", "destroy_after_queue_keep_data"}:
        raise SubmissionSkippedForShutdown("unsubmitted review prompt skipped while draining queue")
    if service == "prompt" and prompt_prefetch_status()["ready"] >= H3_PROMPT_PREFETCH:
        # An approval may have filled the future-render buffer while workflow
        # conversion awaited. This is still known no-POST deferral, never an
        # uncertain submission or a reason to cancel already accepted work.
        raise SubmissionDeferred("prompt prefetch buffer filled during preparation")

    # Keep a local-only preparation snapshot until the dispatch arm is durable.
    # A failed arm write cannot have sent a request in this live process, even
    # when replace succeeded and the directory fsync failed. On restart that
    # armed disk record remains conservatively uncertain.
    job[f"{service}_submission_state"] = "prepared"
    prepared_job = dict(job)
    prompt_id = str(uuid.uuid4())
    job[f"{service}_prompt_id"] = prompt_id
    job["prompt_id"] = prompt_id
    job["active_service"] = service
    job[f"{service}_submission_attempted"] = True
    job[f"{service}_submission_state"] = "armed"
    job["status"] = f"{service}_submitting"
    try:
        save_state()
    except Exception:
        job.clear()
        job.update(prepared_job)
        raise
    # No await separates the durable arm and entering the submission boundary.
    # The legacy attempted flag in the armed record also protects older state
    # readers; prepared/armed/attempting distinguishes the live-process phases.
    job[f"{service}_submission_state"] = "attempting"
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
    if job.get("profile") in PROFILE_LABELS:
        if service == "prompt":
            job["prompt_kind"] = analysis_cache.classify(job, batch_for_job(job))
        else:
            job["render_phase"] = "conditioning"
    try:
        save_state()
    except Exception:
        # This queue write precedes even workflow preparation. A later generic
        # converter/validation error retains 'preparing' and stays terminal.
        job[f"{service}_submission_state"] = "deferred"
        raise
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
        # This outcome is authoritative no-dispatch knowledge, unlike a generic
        # preparation error. Publish it in RAM before the queued snapshot can
        # fail to persist; the worker must retain it through storage recovery.
        job[f"{service}_submission_state"] = "deferred"
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
    if result.get("settled"):
        return
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
    res = await submit_and_watch(job, execution_workflow(job, "prompt"), "prompt")
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
    returncode, out, err = await _run_owned_restart(cmd, preserve_success=True)
    sout = out.decode(errors="replace").strip()
    serr = err.decode(errors="replace").strip()
    if returncode != 0:
        raise RuntimeError(serr or sout or f"vastai exited {returncode}")
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


def _mount_info(path: Path, *, timeout_seconds: float = 5) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["findmnt", "-T", str(path), "-J", "-o", "SOURCE,TARGET,FSTYPE,FSROOT,UUID,MAJ:MIN"],
            capture_output=True, text=True, timeout=timeout_seconds, check=True)
        mounts = json.loads(result.stdout)["filesystems"]
        if len(mounts) != 1 or not isinstance(mounts[0], dict):
            return {}
        return mounts[0]
    except Exception:
        return {}


def persistent_storage_status(*, timeout_seconds: float | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None

    def mount_info(path):
        if deadline is None:
            return _mount_info(path)
        remaining = deadline - time.monotonic()
        return _mount_info(path, timeout_seconds=min(0.3, remaining)) if remaining > 0 else {}

    root = Path(H3_PERSISTENT_ROOT).resolve() if H3_PERSISTENT_ROOT else None
    result = {"configured": bool(root), "mode": H3_PERSISTENCE_MODE or None,
              "root": str(root) if root else None, "exists": bool(root and root.exists()),
              "verified_separate_mount": False, "safe_for_destroy_keep_data": False,
              "mount_source": None, "mount_target": None, "protected_paths": {}, "reason": None}
    if not root or not root.exists():
        result["reason"] = "persistent root is not configured or does not exist"
        return result
    mount = mount_info(root)
    system_mount = mount_info(Path("/"))
    keys = ("source", "target", "fstype", "fsroot", "uuid", "maj:min")
    result.update(mount_source=mount.get("source"), mount_target=mount.get("target"),
                  mount_fstype=mount.get("fstype"))
    # A provider-attested attachment binds the retention contract to the actual
    # mount. Filesystem type or a different device alone cannot prove retention.
    proof_ok = False
    try:
        proof_path = Path(os.environ["H3_PERSISTENT_VOLUME_PROOF"])
        stat = proof_path.stat()
        if stat.st_uid not in {0, os.geteuid()} or stat.st_mode & 0o022:
            raise ValueError("untrusted persistence proof permissions")
        proof = json.loads(proof_path.read_text())
        if not isinstance(proof, dict):
            raise ValueError('Invalid persistence attestation')
        root_stat = root.stat()
        proof_ok = (proof.get("provider") == "vast-local-volume"
                    and type(proof.get("volume_id")) is int and proof["volume_id"] > 0
                    and proof.get("retained_on_instance_destroy") is True
                    and bool(instance_id_from_env()) and proof.get('instance_id') == instance_id_from_env()
                    and proof.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                    and proof.get('root_identity') == [root_stat.st_dev, root_stat.st_ino]
                    and proof.get("mount") == {key: mount.get(key) for key in keys})
    except (OSError, KeyError, ValueError, TypeError):
        pass
    durable_fs = mount.get("fstype") in {"ext4", "xfs", "btrfs", "zfs"}
    separate = (bool(mount.get("source") and mount.get("target") and mount.get("maj:min") and mount.get('fsroot'))
                and mount.get("target") != "/"
                and (mount.get("maj:min"), mount.get("fsroot")) !=
                    (system_mount.get("maj:min"), system_mount.get("fsroot")))
    result["verified_separate_mount"] = bool(durable_fs and separate and proof_ok)
    protected = {"controller_state": STATE.resolve(), "models": COMFY_MODELS_DIR.resolve(),
                 "outputs": COMFY_OUTPUT_DIR.resolve(), "inputs": COMFY_INPUT_DIR.resolve()}
    all_on_volume = True
    for name, path in protected.items():
        info = mount_info(path if path.exists() else path.parent)
        ok = (path.exists() and (path == root or root in path.parents)
              and bool(mount) and all(info.get(key) == mount.get(key) for key in keys))
        result["protected_paths"][name] = {"path": str(path), "exists": path.exists(),
            "mount_source": info.get("source"), "mount_target": info.get("target"),
            "under_persistent_root": path == root or root in path.parents, "verified": ok}
        all_on_volume = all_on_volume and ok
    result["safe_for_destroy_keep_data"] = bool(H3_PERSISTENCE_MODE == "volume"
        and result["verified_separate_mount"] and all_on_volume)
    if not result["safe_for_destroy_keep_data"]:
        result["reason"] = ("Verified retained Vast Local Volume attachment and durable mount required; "
                            "state/models/outputs/inputs must all exist on that mount")
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


def gpu_status(*, timeout_seconds: float = 8) -> dict[str, Any]:
    try:
        p = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_seconds
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
        "control_token": lifecycle_control_token(),
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
        # Untrusted telemetry can contain nonfinite nested numbers as well as
        # malformed prices. Preserve the rest of the diagnostic response.
        data = finite_json(data)
        base["instance"] = data
        base["vast_states"] = {
            "intended_status": data.get("intended_status"),
            "actual_status": data.get("actual_status"),
            "cur_state": data.get("cur_state"),
            "next_state": data.get("next_state"),
        }
        dph = next((data[key] for key in ("dph_total", "dph_base", "price") if data.get(key) is not None), None)
        try:
            dph = float(dph) if dph is not None else None
            if dph is not None and not math.isfinite(dph):
                dph = None
        except Exception:
            dph = None
        base["hourly_usd"] = dph
        estimate = dph * base["session_hours"] if dph is not None else None
        base["estimated_session_compute_usd"] = round(estimate, 3) if estimate is not None and math.isfinite(estimate) else None
    except Exception as e:
        base["error"] = repr(e)
    return base


def _lifecycle_failure(reason: str, *, plan: str = "action_failed") -> None:
    """Block dispatch in memory even when the failure cannot be persisted."""
    vast_control.update({"plan": plan, "reason": reason})
    try:
        save_vast_control()
    except Exception as error:
        # Diagnostics must not kill the guard or trigger a destructive retry.
        vast_control["persistence_error"] = repr(error)


async def delayed_instance_action(action: str, delay: float = 2.0, *, require_persistence: bool = False,
                                  persistence_epoch: int | None = None) -> None:
    epoch = queue_persistence_epoch if persistence_epoch is None else persistence_epoch
    try:
        await asyncio.sleep(delay)
        if not submissions_allowed():
            _lifecycle_failure("Lifecycle actions are disabled in this CPU development environment")
            return
        if action == "destroy" and require_persistence:
            storage = await asyncio.to_thread(persistent_storage_status)
            if not storage["safe_for_destroy_keep_data"]:
                _lifecycle_failure("Persistent storage is no longer verified; destroy was not dispatched",
                                   plan="blocked_unsafe_persistence")
                return
        iid = instance_id_from_env()
        if not iid:
            _lifecycle_failure("instance id unavailable")
            return
        # A settings write or queue failure can happen while the delay or
        # mount inspection awaits. Fail closed at the final dispatch boundary.
        if epoch != queue_persistence_epoch or vast_control.get("armed_generation", epoch) != epoch or not state_diagnostics()["ready"]:
            _lifecycle_failure("Controller state became unavailable; lifecycle was not dispatched")
            return
        vast_control["last_action"] = action
        vast_control["last_action_at"] = time.time()
        save_vast_control()
        if action == "stop":
            await run_vast_cli("stop", "instance", iid)
        elif action == "destroy":
            await run_vast_cli("destroy", "instance", iid, "-y")
        else:
            raise ValueError(action)
    except asyncio.CancelledError:
        _lifecycle_failure("Lifecycle action interrupted; manual reconciliation required", plan="action_interrupted")
        raise
    except Exception as error:
        _lifecycle_failure(f"Lifecycle action failed or its outcome is unknown; manual reconciliation required: {error}")


def _schedule_instance_action(action: str, delay: float = 1.5) -> None:
    global lifecycle_action_task
    if lifecycle_action_task and not lifecycle_action_task.done():
        raise HTTPException(409, "a lifecycle action is already executing")
    lifecycle_action_task = asyncio.create_task(delayed_instance_action(
        action, delay, require_persistence=bool(vast_control.get("require_persistence")),
        persistence_epoch=vast_control.get("armed_generation", queue_persistence_epoch),
    ))


async def restart_local_service(service: str) -> None:
    if service not in ("render", "prompt"):
        raise ValueError(service)
    lock = service_restart_locks[service]
    if lock.locked():
        raise HTTPException(409, "service restart is already in progress")
    async with lock:
        service_maintenance[service] = "restarting"
        service_maintenance_epochs[service] += 1
        if service == "prompt":
            analysis_cache.invalidate()
        try:
            returncode, out, err = await _run_owned_restart(["bash", SERVICE_CTL, "restart", service], cooperative_stop=True)
            if returncode != 0:
                raise RuntimeError(err.decode(errors="replace") or out.decode(errors="replace"))
        except BaseException:
            # A failed/cancelled restart cannot establish worker availability.
            # A successful explicit restart releases this per-service barrier.
            service_maintenance[service] = "unavailable"
            raise
        else:
            service_maintenance.pop(service, None)


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
            if plan in ARMED_LIFECYCLE_PLANS and vast_control.get("armed_generation") != queue_persistence_epoch:
                _lifecycle_failure("Lifecycle safety generation changed; explicit re-arm is required")
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
                    vast_control["armed_generation"] = queue_persistence_epoch
                    vast_control["reason"] = f"idle for {idle_minutes} min"
                    save_vast_control()
                    _schedule_instance_action("stop")

            guard = float(vast_control.get("cost_guard_usd") or 0)
            if guard > 0 and vast_control.get("plan") in ("none", None):
                st = await vast_instance_status()
                est = st.get("estimated_session_compute_usd")
                if est is not None and est >= guard and vast_control.get("plan") in ("none", None) and not lifecycle_dispatch_blocked():
                    vast_control["plan"] = "stop_after_current"
                    vast_control["armed_generation"] = queue_persistence_epoch
                    vast_control["reason"] = f"estimated session compute cost reached ${est:.2f}"
                    vast_control["armed_at"] = time.time()
                    save_vast_control()
        except Exception as e:
            vast_control["guard_error"] = repr(e)
            _lifecycle_failure(f"Lifecycle guard failed; manual reconciliation required: {e}")
        await asyncio.sleep(10)


def service_work_active(service: str) -> bool:
    return any(j.get("status") in {f"{service}_preparing", f"{service}_submitting", f"{service}_running"}
               or (j.get("active_service") == service and j.get("status") in {
                   "soft_timeout_cancelling", "hard_timeout_restarting_comfy", "cancelling",
               }) for j in queue)


def prompt_prefetch_status() -> dict:
    ready = sum(j.get("status") in {"render_queued_auto", "render_queued_review"} for j in queue)
    # The one Prompt Worker reserves one slot before its first await. Review
    # Pool entries are decisions, not future render work; counting them would
    # deadlock automatic batches and prevent all five reviews being prepared.
    preparing = int(service_work_active("prompt") or service_recovering("prompt"))
    return {"target": H3_PROMPT_PREFETCH, "ready": ready, "preparing": preparing,
            "buffer": ready + preparing}


async def refresh_prompt_epoch() -> None:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{PROMPT_COMFY_URL}/aj/service_epoch")
            response.raise_for_status()
            analysis_cache.observe_service(response.json()["service_epoch"])
    except Exception as error:
        analysis_cache.invalidate()
        raise SubmissionDeferred("Prompt Worker service epoch is unavailable") from error


async def prepare_render_model_boundary() -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{RENDER_COMFY_URL}/free", json={"unload_models": True, "free_memory": True})
        response.raise_for_status()


async def verify_production_files(job: dict, service: str) -> None:
    from .artifacts import validate_manifest, read_json
    from .model_health import artifact_health
    profile = job_profile(job)
    def verify():
        manifest = json.loads((CONFIG / "models_manifest.json").read_text())
        entries = validate_manifest(manifest)
        for item in entries.values():
            if item["required"] and ("shared" in item["profiles"] or profile in item["profiles"]):
                if artifact_health(COMFY_MODELS_DIR, item)["status"] != "verified_file":
                    raise ValueError("required production artifact is not integrity-verified")
        if service == "render":
            from .artifacts import verify_file
            settings = resolve_loras(LORA_REGISTRY, profile, job["context"].get("loras"), frozen=True)
            expected = [(setting["id"], "loras/" + LORA_REGISTRY[setting["id"]]["filename"], LORA_REGISTRY[setting["id"]])
                        for setting in settings if setting["enabled"]]
            bridge = CONDITIONING_BRIDGE
            if job["context"].get("conditioning_bridge", bridge["defaults"][profile])["enabled"]:
                expected.append((bridge["id"], "semantic_bridge/" + bridge["filename"], bridge))
            enrolled = None
            for identifier, destination, metadata in expected:
                if type(metadata.get("size_bytes")) is int and metadata.get("sha256"):
                    if identifier == bridge["id"]:
                        from .model_health import verify_bridge_files
                        verify_bridge_files(COMFY_MODELS_DIR, COMFY_ROOT, bridge, metadata)
                    else:
                        verify_file(COMFY_MODELS_DIR, destination, metadata)
                    continue
                if enrolled is None:
                    enrolled = read_json(COMFY_MODELS_DIR, ".external_loras.json")
                receipt = enrolled.get("artifacts", {}).get(identifier)
                if not isinstance(receipt, dict) or receipt.get("destination") != destination:
                    raise ValueError("external LoRA needs explicit local checksum enrollment")
                verify_file(COMFY_MODELS_DIR, destination, receipt)
    try:
        await asyncio.to_thread(verify)
    except Exception:
        raise SubmissionRejected("Production model/LoRA provenance or file integrity is incomplete; run provisioning and preflight") from None


def active_render_policy() -> dict | None:
    active = [job for job in queue if job.get("status") in {
        "render_preparing", "render_submitting", "render_running", "recovery_render", "render_submission_uncertain"}
        or job.get("active_service") == "render" and job.get("status") in {
            "cancelling", "soft_timeout_cancelling", "hard_timeout_restarting_comfy"}]
    if not active:
        return None
    if len(active) != 1:
        return {"profile": None, "phase": "recovery"}
    job = active[0]
    return {"profile": job.get("profile"), "phase": job.get("render_phase", "unknown")
            if job.get("status") == "render_running" else "recovery"}


def phase_admission(job: dict, service: str) -> tuple[bool, str]:
    if job.get("profile") not in PROFILE_LABELS:
        return True, "explicit diagnostic fixture; normal workers require persisted profiles"
    if service == "render":
        if any(other is not job and (other.get("status") in {
            "render_preparing", "render_submitting", "render_running", "recovery_render", "render_submission_uncertain"}
            or other.get("active_service") == "render" and other.get("status") in {
                "cancelling", "soft_timeout_cancelling", "hard_timeout_restarting_comfy"}) for other in queue):
            return False, "another heavy render owns the renderer"
        if service_work_active("prompt") or service_recovering("prompt"):
            return False, "conditioning window waits for the active Prompt Worker to settle"
        return True, "exclusive render conditioning window"
    free = overlap_telemetry["free_vram_mb"] if time.monotonic() - overlap_telemetry["sampled_at"] <= 3 else None
    return prompt_admission(prompt_kind=analysis_cache.classify(job, batch_for_job(job)),
        prompt_profile=job_profile(job), active_render=active_render_policy(), profiles=PRODUCTION, free_vram_mb=free)


def assert_phase_admission(job: dict, service: str) -> None:
    allowed, reason = phase_admission(job, service)
    job["overlap_admission"] = reason
    if not allowed:
        raise SubmissionDeferred(reason)


def pick_next_prompt_job() -> dict | None:
    if "prompt" in service_maintenance or lifecycle_dispatch_blocked() or service_recovering("prompt"):
        return None
    if service_work_active("prompt") or prompt_prefetch_status()["buffer"] >= H3_PROMPT_PREFETCH:
        return None
    if vast_control.get("plan") in {"stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data"}:
        # auto prompts may already be queued for stop-after-queue, but review generation is intentionally skipped when armed.
        pending_auto = [j for j in queue if j.get("status") == "prompt_queued" and not j.get("review_required")]
        if vast_control.get("plan") == "stop_after_current":
            return None
        if pending_auto:
            pending_auto.sort(key=lambda j: (j["batch_seq"], j["candidate_index"]))
            return next((job for job in pending_auto if phase_admission(job, "prompt")[0]), None)
        return None
    pending = [j for j in queue if j.get("status") == "prompt_queued"]
    # A phase-deferred automatic candidate retains its accepted priority over
    # all unstarted reviews. Admission cannot turn a warm review into a bypass
    # of cold automatic work in another batch.
    pending_auto = [job for job in pending if not job.get("review_required")]
    if pending_auto:
        pending = pending_auto
    if not pending:
        return None
    # Keep renderer fed: all auto candidates (#1-5) outrank review candidates (#6-10),
    # even when a newer batch arrives. Pending review never blocks the next auto five.
    pending.sort(key=lambda j: (
        1 if j.get("review_required") else 0,
        j["batch_seq"],
        j["candidate_index"],
    ))
    return next((job for job in pending if phase_admission(job, "prompt")[0]), None)


def pick_next_render_job() -> dict | None:
    if "render" in service_maintenance or lifecycle_dispatch_blocked() or service_recovering("render") or vast_control.get("plan") == "stop_after_current":
        return None
    if service_work_active("render"):
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
    return next((job for job in candidates if phase_admission(job, "render")[0]), None)


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
    res = await submit_and_watch(job, execution_workflow(job, "render"), "render", approved_prompt=approved)
    await _finish_watched_job(job, "render", res)


def _handle_worker_error(job: dict, service: str, error: Exception) -> None:
    try:
        submission_state = job.get(f"{service}_submission_state")
        if submission_state in {"prepared", "deferred"}:
            # Both outcomes prove the absence of remote side effects. Keep the
            # published RAM snapshot queued until the persistence fence heals.
            job["status"] = "cancelled" if job.get("cancel_requested") else (
                "prompt_queued" if service == "prompt" else (
                    "render_queued_review" if job.get("approved_final_prompt") else "render_queued_auto"
                )
            )
            job["persistence_warning"] = str(error)
            if submission_state == "deferred":
                # Do not consume a one-shot fault in the error handler: expose
                # degraded storage until the worker's normal repair boundary.
                if not queue_persistence_error:
                    _record_queue_persistence_failure(error)
            else:
                save_state()
        elif submission_state in {"armed", "attempting", "accepted", "uncertain"}:
            _preserve_recovery(job, service, f"Execution outcome could not be confirmed: {error}")
        else:
            job["status"] = "cancelled" if job.get("cancel_requested") else f"{service}_failed"
            job["error"] = str(error)
            job["finished_at"] = time.time()
            save_state()
    except Exception as write_error:
        _record_queue_persistence_failure(write_error)
        job["persistence_warning"] = str(write_error)


async def _wait_for_queue_persistence() -> bool:
    if not queue_persistence_error:
        return False
    await asyncio.sleep(0.75)
    try:
        _resume_queue_persistence()
    except Exception:
        # Error state remains visible; only retry the local published snapshot,
        # never a ComfyUI submission or an uncertain Vast operation.
        return True
    return False


async def prompt_worker() -> None:
    while True:
        if await _wait_for_queue_persistence():
            continue
        if active_render_policy() is not None and time.monotonic() - overlap_telemetry["sampled_at"] >= 1:
            sample = await asyncio.to_thread(gpu_status, timeout_seconds=1)
            used, total = sample.get("vram_used_mb"), sample.get("vram_total_mb")
            overlap_telemetry.update(free_vram_mb=int(total - used) if type(used) in (int, float)
                                    and type(total) in (int, float) and math.isfinite(total - used) else None,
                                    sampled_at=time.monotonic())
        job = pick_next_prompt_job()
        if job:
            try:
                await generate_prompt_candidate(job)
            except Exception as e:
                _handle_worker_error(job, "prompt", e)
            # Cached/immediate service responses need not suspend. Give the
            # renderer, guard and HTTP handlers a turn before another prompt.
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.75)


async def render_worker() -> None:
    while True:
        if await _wait_for_queue_persistence():
            continue
        job = pick_next_render_job()
        if job:
            try:
                await render_job(job)
            except Exception as e:
                _handle_worker_error(job, "render", e)
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.75)


async def _recover_one_job(job: dict, service: str) -> None:
    recovery_counts[service] = recovery_counts.get(service, 0) + 1
    recovering_services.add(service)
    try:
        await _recover_one_job_impl(job, service)
    finally:
        recovery_counts[service] -= 1
        if recovery_counts[service] == 0:
            del recovery_counts[service]
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
    if _history_outcome(entry) is not None:
        job["recovered_at"] = time.time()
    if _apply_history_outcome(job, service, entry):
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
        # Cancellation may only have signalled a running job. It can finish
        # successfully between the history and queue reads, so absence plus an
        # acknowledgement is not terminal proof. Reconcile a fresh history.
        try:
            entry = await fetch_history(service, pid, retries=2, delay=0.2)
        except Exception as error:
            _preserve_recovery(job, service, f"Post-cancellation history unavailable; recovery will retry: {error}")
            return
        if _history_outcome(entry) is not None:
            job["recovered_at"] = time.time()
        if not _apply_history_outcome(job, service, entry):
            _preserve_recovery(job, service, "Cancellation acknowledged, but execution has no settled history; recovery will retry")
        return

    # History may have expired or been reset after a completed paid render.
    # Successful empty reads prove only absence now, not non-acceptance before.
    # Never automatically resubmit a request that may have been accepted.
    _preserve_recovery(job, service, "Submitted id is absent from history and queue; manual reconciliation is required")


async def recover_jobs_after_controller_restart() -> None:
    async def recover_service(service: str) -> None:
        active: dict[str, asyncio.Task] = {}
        statuses = {f"recovery_{service}", f"{service}_submission_uncertain"}

        async def recover_job(job: dict) -> None:
            while job.get("status") in statuses:
                if not (state_load_error or vast_control_load_error):
                    try:
                        await _recover_one_job(job, service)
                    except Exception as error:
                        try:
                            _preserve_recovery(job, service, f"Recovery will retry: {error}")
                        except Exception as write_error:
                            job["recovery_warning"] = f"Recovery state write failed: {write_error}"
                await asyncio.sleep(5)

        try:
            while True:
                for job_id, task in list(active.items()):
                    if task.done():
                        await task
                        del active[job_id]
                if not (state_load_error or vast_control_load_error):
                    for job in queue:
                        if job.get("status") in statuses and job["id"] not in active:
                            # One reconciler per job. A manual job or long watcher
                            # cannot starve another job on this same service.
                            active[job["id"]] = asyncio.create_task(recover_job(job), name=f"h3-recovery-{service}")
                await asyncio.sleep(5)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)

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
    global prompt_worker_task, render_worker_task, vast_guard_task, recovery_task, last_busy_at, controller_started
    load_state()
    controller_started = True
    last_busy_at = time.time()
    recovery_task = asyncio.create_task(recover_jobs_after_controller_restart())
    prompt_worker_task = asyncio.create_task(prompt_worker())
    render_worker_task = asyncio.create_task(render_worker())
    vast_guard_task = asyncio.create_task(vast_guard_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    global terminal_shutting_down
    terminal_shutting_down = True
    terminal_tokens.pending.clear()
    tasks = [task for task in (prompt_worker_task, render_worker_task, vast_guard_task, recovery_task, lifecycle_action_task)
             if task is not None]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    sessions = list(terminal_sessions)
    errors = await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
    for session in sessions:
        if session.closed:
            terminal_sessions.discard(session)
    if any(isinstance(error, BaseException) for error in errors):
        raise BaseExceptionGroup("Terminal cleanup failed", [error for error in errors if isinstance(error, BaseException)])


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config():
    from .model_health import manifest_health, lora_health
    return {
        "profiles": [{"id": key, **value} for key, value in PRODUCTION["profiles"].items()],
        "default_profile": PRODUCTION["default_profile"],
        "render_comfy_url": RENDER_COMFY_URL,
        "prompt_comfy_url": PROMPT_COMFY_URL,
        "loras": [{"id": key, "display_name": value["display_name"], "defaults": value["defaults"],
                   "strength_range": value["strength_range"], "compatibility": value["compatibility"],
                   "gpu_validation": value["gpu_validation"], "help": value.get("help", ""),
                   **lora_health(COMFY_MODELS_DIR, key, value)}
                  for key, value in LORA_REGISTRY.items()],
        "prompt_cache": {"epoch": analysis_cache.epoch, "service_epoch": analysis_cache.service_epoch},
        "conditioning_bridge": {"id": CONDITIONING_BRIDGE["id"], "defaults": CONDITIONING_BRIDGE["defaults"],
                                **lora_health(COMFY_MODELS_DIR, CONDITIONING_BRIDGE["id"], CONDITIONING_BRIDGE, bridge=True)},
        "gpu_validation": "pending",
        "models": await asyncio.to_thread(manifest_health, CONFIG / "models_manifest.json", COMFY_MODELS_DIR),
        "version": "pre-rental-final-rc5",
        "state": state_diagnostics(),
        "prompt_prefetch": H3_PROMPT_PREFETCH,
        "terminal_enabled": H3_ENABLE_TERMINAL,
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

    from .proxy import worker_proxy
    return await worker_proxy(target, request, forwarded)



@app.get("/api/vast/status")
async def api_vast_status():
    return await vast_instance_status()


@app.get("/api/diagnostics")
async def api_diagnostics():
    from .diagnostics import checks_for, local_probe, model_status, queue_summary, service_health

    # Independent probes run concurrently. No save_state, CLI control, workflow
    # submission, restart or recovery operation belongs on this read-only path.
    render, prompt, storage, disk, gpu, models = await asyncio.gather(
        service_health(RENDER_COMFY_URL), service_health(PROMPT_COMFY_URL),
        local_probe(lambda: persistent_storage_status(timeout_seconds=1.5)),
        local_probe(disk_status), local_probe(lambda: gpu_status(timeout_seconds=1.0)),
        local_probe(lambda: model_status(CONFIG / "models_manifest.json", COMFY_MODELS_DIR, COMFY_ROOT)),
    )
    if "status" not in models:
        models.update(status="WARN", provisional=True)
    gpu = finite_json(gpu)
    vram_available = (type(gpu.get("vram_total_mb")) in (int, float) and gpu["vram_total_mb"] > 0
                      and type(gpu.get("vram_used_mb")) in (int, float) and gpu["vram_used_mb"] >= 0)
    cli_available = shutil.which(VAST_CLI) is not None
    data = finite_json({"controller": state_diagnostics(), "services": {"render": render, "prompt": prompt},
        "gpu": gpu, "vram": {"available": vram_available,
                               "used_mb": gpu.get("vram_used_mb"), "total_mb": gpu.get("vram_total_mb")},
        "storage": storage, "disk": disk, "queue": queue_summary(queue), "prefetch": prompt_prefetch_status(),
        "vast": {"cli_available": cli_available, "instance_id_available": bool(instance_id_from_env()),
                 "control_available": cli_available and bool(instance_id_from_env()) and submissions_allowed(),
                 "remote_authorization": "not_probed"}, "models": models,
        "production": {"default_profile": PRODUCTION["default_profile"], "active_render": active_render_policy(),
                       "profiles": PRODUCTION["profiles"], "prompt_cache_epoch": analysis_cache.epoch,
                       "duration_jobs": [{"id": job["id"], "requested_duration_seconds": job.get("context", {}).get("requested_duration_seconds"),
                           "effective_duration_seconds": job.get("context", {}).get("effective_duration_seconds"),
                           "legal_frame_count": job.get("context", {}).get("legal_frame_count"),
                           "temporal_guard_status": job.get("temporal_guard_status", "pending")}
                           for job in queue if job.get("profile") in PROFILE_LABELS][:20],
                       "prompt_service_epoch": analysis_cache.service_epoch, "gpu_validation": "pending"}})
    data["checks"] = checks_for(data)
    data["status"] = next((status for status in ("FAIL", "WARN") if any(
        check["status"] == status for check in data["checks"])), "PASS")
    return data


@app.get("/api/logs/{service}")
async def api_logs(service: str, lines: int = Query(200, ge=1, le=1000)):
    from .logs import LOG_FILES, tail_log

    if service not in LOG_FILES:
        raise HTTPException(400, "service must be render, prompt or panel")
    return await asyncio.to_thread(tail_log, WORKSPACE, service, lines)


@app.post("/api/terminal/session")
async def api_terminal_session():
    # This HTTP handshake is protected by require_panel_auth. It does not
    # launch a shell. The WebSocket independently validates/consumes the ticket.
    if not H3_ENABLE_TERMINAL or terminal_shutting_down:
        raise HTTPException(403, "Terminal is disabled")
    if terminal_sessions:
        raise HTTPException(409, "A terminal session is already active")
    try:
        token = terminal_tokens.issue()
    except RuntimeError as error:
        raise HTTPException(429, "Too many pending terminal sessions") from error
    return JSONResponse({"token": token, "expires_in_seconds": TOKEN_TTL_SECONDS},
                        headers={"Cache-Control": "no-store"})


@app.websocket("/api/terminal/ws")
async def api_terminal_websocket(websocket: WebSocket):
    # HTTP middleware NEVER authorizes WebSockets. Basic Auth, a URL token,
    # and an old/reused HTTP ticket cannot bypass this independent check.
    if not H3_ENABLE_TERMINAL or terminal_shutting_down or not PANEL_AUTH_PASSWORD:
        await websocket.close(code=1008)
        return
    ticket = websocket_ticket(websocket)
    if ticket is None or not terminal_tokens.consume(ticket):
        await websocket.close(code=1008)
        return
    if terminal_sessions:
        await websocket.close(code=1013)
        return
    session = PTYSession(WORKSPACE, _require_restart_group_control)
    terminal_sessions.add(session)  # No await before reserving the sole session.
    try:
        await websocket.accept(subprotocol=TERMINAL_PROTOCOL)
        session.start()
        await session.ready()
        await bridge_terminal(websocket, session)
    except Exception:
        # Do not echo commands, tickets, environment or process exception text.
        pass
    finally:
        try:
            await session.close()
        finally:
            if session.closed:
                terminal_sessions.discard(session)
            try:
                await websocket.close()
            except Exception:
                pass


@app.post("/api/vast/action")
async def api_vast_action(req: VastActionRequest, request: Request = None):
    action = req.action
    if action not in {"cancel_plan", "set_idle_timer", "set_cost_guard", "stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data", "stop_now", "destroy_now"}:
        raise HTTPException(400, f"Unknown Vast action: {action}")

    if action == "stop_now" and req.confirm != "STOP":
        raise HTTPException(400, "Type STOP to confirm")
    if action == "destroy_now" and req.confirm != "DESTROY":
        raise HTTPException(400, "Type DESTROY to confirm")
    if state_load_error or queue_persistence_error or vast_control_load_error or (controller_started and _worker_health()[1]):
        raise HTTPException(503, "Stored controller state is unavailable; repair it before changing lifecycle controls")

    action_inflight = lifecycle_action_task is not None and not lifecycle_action_task.done()
    if (action_inflight or str(vast_control.get("plan") or "").startswith("executing_")) and action not in {"set_idle_timer", "set_cost_guard"}:
        raise HTTPException(409, "a lifecycle action is already executing; it cannot be cancelled after dispatch")
    if action in {"stop_after_current", "stop_after_queue", "destroy_after_queue_keep_data", "stop_now", "destroy_now"} and not submissions_allowed():
        raise HTTPException(409, "Lifecycle actions are disabled in this CPU development environment")

    # HTTP clients must carry the snapshot token captured for their logical
    # decision. Direct controller calls are trusted internal actions. No await
    # separates this compare from the durable generation increment below.
    if request is not None and req.control_token is None:
        raise HTTPException(428, "A lifecycle control_token from /api/vast/status is required")
    if req.control_token is not None and req.control_token != lifecycle_control_token():
        raise HTTPException(409, "Lifecycle controls changed; refresh and make a new decision")

    if action == "cancel_plan":
        vast_control.update({"plan":"none","reason":None,"armed_at":None,"require_persistence":False,"armed_generation":None})
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control))

    if action == "set_idle_timer":
        vast_control["idle_minutes"] = int(req.idle_minutes or 0)
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control))

    if action == "set_cost_guard":
        vast_control["cost_guard_usd"] = float(req.cost_guard_usd or 0)
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control))

    if action == "stop_after_current":
        vast_control.update({"plan":"stop_after_current","armed_at":time.time(),"reason":"user","armed_generation":queue_persistence_epoch})
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control))

    if action == "stop_after_queue":
        skipped = skip_unstarted_review_prompts_for_shutdown()
        vast_control.update({"plan":"stop_after_queue","armed_at":time.time(),"reason":f"user; skipped {skipped} unstarted review prompts","armed_generation":queue_persistence_epoch})
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control))

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
            "armed_generation": queue_persistence_epoch,
            "require_persistence": True,
            "armed_at":time.time(),
            "reason":f"user; skipped {skipped} unstarted review prompts"
        })
        save_lifecycle_decision()
        return lifecycle_decision_response(plan=dict(vast_control), persistence=ps)

    if action == "stop_now":
        if req.confirm != "STOP":
            raise HTTPException(400, "Type STOP to confirm")
        vast_control.update({"plan":"executing_stop","armed_at":time.time(),"reason":"user immediate","require_persistence":False,"armed_generation":queue_persistence_epoch})
        save_lifecycle_decision()
        _schedule_instance_action("stop", 2.0)
        return lifecycle_decision_response(message="Instance stop scheduled. This panel will disconnect.")

    if action == "destroy_now":
        if req.confirm != "DESTROY":
            raise HTTPException(400, "Type DESTROY to confirm")
        vast_control.update({"plan":"executing_destroy","armed_at":time.time(),"reason":"user immediate","require_persistence":False,"armed_generation":queue_persistence_epoch})
        save_lifecycle_decision()
        _schedule_instance_action("destroy", 2.0)
        return lifecycle_decision_response(message="Instance destroy scheduled. Instance-local data will be lost; separately mounted persistent volumes are not deleted by this action.")

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
    async with queue_lock:
        _resume_queue_persistence()
        if state_load_error:
            raise HTTPException(503, state_load_error)
        request_key = str(req.request_id) if req.request_id is not None else None
        request_hash = hashlib.sha256(json.dumps(req.model_dump(mode="json", exclude={"request_id"}),
                                                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = request_results.get(request_key)
        if previous:
            if previous["request_hash"] != request_hash:
                raise HTTPException(409, "request_id was already used for a different request")
            return {"created_batches": list(previous["result"]["created_batches"])}
        if shutdown_armed():
            raise HTTPException(409, "shutdown plan is armed; cancel it before creating a new batch")
        if request_key is None:
            raise HTTPException(400, "A client-generated UUID request_id is required; reuse it when retrying")
        if not req.prompt.strip():
            raise HTTPException(400, "scene prompt is empty")
        selected_profile = req.profile or req.model or PRODUCTION["default_profile"]
        if selected_profile not in PROFILE_LABELS:
            raise HTTPException(400, "unknown production profile")
        if req.profile and req.model is not None and req.model != req.profile:
            raise HTTPException(400, "conflicting profile identifiers")
        pictures, audio = validate_requested_assets(req)
        created_batches = []
        base_render_seed = req.seed if req.seed is not None else int(time.time_ns() & 0x7FFFFFFF)
        base_prompt_seed = int((time.time_ns() >> 8) & 0x7FFFFFFF)
        base_analysis_seed = int((time.time_ns() >> 16) & 0x7FFFFFFF)
        next_queue, next_batches = list(queue), list(batches)
        batch_seq_start = max([b.get("seq", 0) for b in batches] or [0]) + 1

        for batch_offset in range(req.batches):
            batch_id = uuid.uuid4().hex
            batch_seq = batch_seq_start + batch_offset
            context = {
                "prompt": req.prompt,
                "model": selected_profile,
                "profile": selected_profile,
                "profile_identity": profile_identity(selected_profile),
                "lora_identity": {key: entry["filename"] for key, entry in LORA_REGISTRY.items()},
                "conditioning_bridge": {"id": CONDITIONING_BRIDGE["id"], "filename": CONDITIONING_BRIDGE["filename"],
                                        **CONDITIONING_BRIDGE["defaults"][selected_profile]},
                "duration_seconds": req.duration_seconds,
                **duration_identity(req.duration_seconds),
                "soft_timeout_minutes": req.soft_timeout_minutes,
                "hard_restart_after_seconds": req.hard_restart_after_seconds,
                "pictures": pictures,
                "audio": audio,
                "loras": resolve_loras(LORA_REGISTRY, selected_profile, [x.model_dump() for x in req.loras]),
            }

            analysis_seed = base_analysis_seed + batch_offset
            batch = {
                "id": batch_id,
                "profile": selected_profile,
                "seq": batch_seq,
                "created_at": time.time(),
                "context": context,
                "prompts_per_batch": PROMPTS_PER_BATCH,
                "auto_approved": AUTO_APPROVED_PER_BATCH,
                "review_required": REVIEW_PER_BATCH,
                "analysis_seed": analysis_seed,
            }
            next_batches.append(batch)
            created_batches.append(batch_id)

            for i in range(PROMPTS_PER_BATCH):
                render_seed = (
                    base_render_seed + (batch_offset * PROMPTS_PER_BATCH) + i
                    if req.random_each else base_render_seed
                )
                prompt_seed = base_prompt_seed + (batch_offset * PROMPTS_PER_BATCH * 2) + i * 2
                next_queue.append({
                    "id": uuid.uuid4().hex,
                    "batch_id": batch_id,
                    "profile": selected_profile,
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
        result = {"created_batches": created_batches}
        next_requests = {**request_results, request_key: {"request_hash": request_hash, "result": result}}
        _persist_queue_snapshot(next_queue, next_batches, publication=True, next_requests=next_requests)
        # No await separates the durable commit and publication. Existing job
        # identities are preserved for active watchers and recovery tasks.
        queue[:] = next_queue
        batches[:] = next_batches
        request_results.clear()
        request_results.update(next_requests)

    return {"created_batches": created_batches}


@app.get("/api/jobs")
async def jobs():
    return {"jobs": [public_job(job) for job in queue], "batches": batches}


@app.get("/api/jobs/{controller_job_id}")
async def get_job(controller_job_id: str):
    return public_job(find_job(controller_job_id))


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
    async with queue_lock:
        _resume_queue_persistence()
        job = find_job(controller_job_id)
        final_prompt = req.final_prompt if req.final_prompt is not None else job.get("final_h3_prompt")
        if job.get("approval_result"):
            if str(final_prompt) != job.get("approved_final_prompt"):
                raise HTTPException(409, "job was already approved with a different final prompt")
            return dict(job["approval_result"])
        if shutdown_armed():
            raise HTTPException(409, "shutdown plan is armed; cancel it before approving review prompts")
        if job.get("status") != "pending_review":
            raise HTTPException(409, f"job is {job.get('status')}, not pending_review")
        if not final_prompt or not str(final_prompt).strip():
            raise HTTPException(400, "final prompt is empty")
        if job.get("profile") in PROFILE_LABELS:
            try:
                job_profile(job)
                final_timeline(str(final_prompt), job.get("creative_plan"), job["context"]["effective_duration_seconds"])
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
        staged_job = dict(job, approved_final_prompt=str(final_prompt), approved_at=time.time(),
                          render_priority=0, status="render_queued_review", approval_result={"ok": True, "priority": "top"})
        staged_queue = [staged_job if existing is job else existing for existing in queue]
        _persist_queue_snapshot(staged_queue, batches, publication=True, next_requests=request_results)
        job.clear()
        job.update(staged_job)
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
