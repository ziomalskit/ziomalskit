"""Explicit analysis identities, durable text and ephemeral worker residency."""
from __future__ import annotations

import hashlib
import json
import uuid

ANALYSIS_FIELDS = ("visual_facts", "expanded_intent", "reference_map")


def _digest(identity) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def model_identity(manifest: dict, destinations: set[str], runtime: dict) -> dict:
    """Bind actual source/quantization identities, including pending provenance.

    A filename alone never identifies a model. Step 0 uses only its own six
    artifacts; changing a writer must not invalidate reusable visual facts.
    """
    fields = ("provider", "repository", "revision", "repository_path", "destination", "size_bytes", "sha256",
              "generation", "source_repository", "observed_source_revision", "observed_sha256", "observed_size_bytes")
    selected = [item for item in [*manifest["artifacts"], *manifest.get("pending_artifacts", [])]
                if item.get("destination") in destinations]
    if len(selected) != len(destinations) or {item["destination"] for item in selected} != destinations:
        raise ValueError("analysis model provenance missing or ambiguous")
    return {"models": [{key: item.get(key) for key in fields} for item in sorted(selected, key=lambda item: item["destination"])],
            "runtime": runtime}


def step0_key(job: dict) -> str:
    context = job["context"]
    if job.get("profile") and any(not context.get(key) for key in ("workflow_id", "workflow_baseline_sha256", "workflow_adapter_identity")):
        raise ValueError("persisted workflow analysis identity missing; explicit reconciliation required")
    profile = context.get("profile_identity", {})
    if job.get("profile") and not profile.get("step0_identity"):
        raise ValueError("persisted Step 0 model identity missing; explicit reconciliation required")
    identity = {"baseline": context.get("workflow_baseline_sha256"), "workflow_id": context.get("workflow_id"),
                "step0_identity": profile.get("step0_identity"), "cache_contract": "v20_split_analysis_native_eos_v2",
                **{key: context.get(key) for key in ("prompt", "pictures", "audio", "duration_seconds")}}
    return _digest(identity)


def analysis_key(job: dict) -> str:
    profile = job["context"].get("profile_identity", {})
    if job.get("profile") and not profile.get("analysis_identity"):
        raise ValueError("persisted Step 1/2 model identity missing; explicit reconciliation required")
    return _digest({"step0": step0_key(job), "analysis_identity": profile.get("analysis_identity"),
                    "analysis_seed": job["analysis_seed"],
                    "instructions": job["context"].get("workflow_baseline_sha256")})


class AnalysisCache:
    def __init__(self):
        self.epoch = uuid.uuid4().hex
        self.resident: set[str] = set()
        self.service_epoch: str | None = None

    def invalidate(self, service_epoch: str | None = None) -> None:
        self.epoch = uuid.uuid4().hex
        self.resident.clear()
        self.service_epoch = service_epoch

    def observe_service(self, service_epoch: str) -> None:
        if not isinstance(service_epoch, str) or len(service_epoch) != 32:
            raise ValueError("invalid prompt service epoch")
        int(service_epoch, 16)
        if service_epoch != self.service_epoch:
            self.invalidate(service_epoch)

    def texts(self, job: dict, batch: dict | None, *, records=()) -> dict:
        key, visual_key = analysis_key(job), step0_key(job)
        visual = {}
        for candidate in [batch, *records]:
            record = (candidate or {}).get("analysis", {})
            if not isinstance(record, dict) or record.get("step0_key") != visual_key:
                continue
            texts = record.get("texts", {})
            if not isinstance(texts, dict):
                continue
            if record.get("key") == key and all(isinstance(texts.get(field), str) and texts[field].strip() for field in ANALYSIS_FIELDS):
                return {field: texts[field] for field in ANALYSIS_FIELDS}
            if isinstance(texts.get("visual_facts"), str) and texts["visual_facts"].strip():
                visual = {"visual_facts": texts["visual_facts"]}
        return visual

    def classify(self, job: dict, batch: dict | None, *, records=()) -> str:
        key = analysis_key(job)
        texts = self.texts(job, batch, records=records)
        if set(texts) == set(ANALYSIS_FIELDS):
            return "cached_text" if key in self.resident else "durable_text"
        if texts:
            return "step0_text"
        return "cold_analysis"

    def record(self, job: dict, batch: dict, texts: dict, *, execution_epoch: str) -> bool:
        present = {field: texts[field] for field in ANALYSIS_FIELDS if isinstance(texts.get(field), str) and texts[field].strip()}
        if "visual_facts" not in present:
            return False
        key = analysis_key(job)
        previous = batch.get("analysis", {})
        if isinstance(previous, dict) and previous.get("key") == key and previous.get("step0_key") == step0_key(job):
            saved = previous.get("texts", {})
            if isinstance(saved, dict):
                present = {**{field: saved[field] for field in ANALYSIS_FIELDS if isinstance(saved.get(field), str) and saved[field].strip()}, **present}
        batch["analysis"] = {"key": key, "step0_key": step0_key(job), "texts": present,
                             "source_epoch": execution_epoch}
        if execution_epoch == self.epoch and set(present) == set(ANALYSIS_FIELDS):
            self.resident.add(key)
        return True


def prompt_admission(*, prompt_kind: str, prompt_profile: str, active_render: dict | None,
                     profiles: dict, free_vram_mb: int | None, prompt_policy: dict | None = None) -> tuple[bool, str]:
    if active_render is None:
        return True, "analysis window: renderer idle"
    render_profile = active_render.get("profile")
    if render_profile not in profiles["profiles"]:
        return False, "render profile unknown"
    if active_render.get("phase") != "sampling":
        return False, "renderer conditioning, decoding or recovery has VRAM priority"
    render_policy = (active_render.get("policy") or profiles["profiles"][render_profile])["overlap_policy"]
    if prompt_kind not in {"cold_analysis", "step0_text", "cached_text", "durable_text"}:
        return False, "prompt analysis state unknown"
    cold = prompt_kind in {"cold_analysis", "step0_text"}
    if cold and not render_policy["cold_analysis_during_sampling"]:
        return False, "H3 Full sampling: cold Step 0–2 waits for an analysis window"
    if prompt_profile not in profiles["profiles"]:
        return False, "prompt profile unknown"
    writer_policy = prompt_policy or profiles["profiles"][prompt_profile]
    needed = max(render_policy["analysis_headroom_mb"], writer_policy["overlap_policy"]["analysis_headroom_mb"]) if cold else writer_policy["overlap_policy"]["writer_headroom_mb"]
    if not cold and writer_policy.get("compiler") == "shared_gemma":
        needed = max(needed, writer_policy["overlap_policy"]["analysis_headroom_mb"])
    if type(free_vram_mb) is not int or free_vram_mb < needed:
        return False, "measured VRAM headroom unavailable or insufficient"
    return True, "analysis overlap with measured headroom" if cold else "writer with reused Step 0–2 text and measured headroom"
