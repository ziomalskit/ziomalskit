"""Explicit analysis identities, durable text and ephemeral worker residency."""
from __future__ import annotations

import hashlib
import json
import uuid

from .v20 import BASELINE_SHA256

ANALYSIS_FIELDS = ("visual_facts", "expanded_intent", "reference_map")


def analysis_key(job: dict) -> str:
    context = job["context"]
    # Render profile/writer and LoRAs are intentionally excluded: Step 0–2 is
    # shared. Requested runtime is part of the v20 analysis contract.
    identity = {"baseline": BASELINE_SHA256, "analysis_seed": job["analysis_seed"],
                **{key: context.get(key) for key in ("prompt", "pictures", "audio", "duration_seconds")}}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


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

    def classify(self, job: dict, batch: dict | None) -> str:
        key = analysis_key(job)
        record = (batch or {}).get("analysis", {})
        texts = record.get("texts", {})
        if record.get("key") == key and all(isinstance(texts.get(field), str) and texts[field].strip() for field in ANALYSIS_FIELDS):
            return "cached_text" if key in self.resident else "durable_text"
        return "cold_analysis"

    def record(self, job: dict, batch: dict, texts: dict, *, execution_epoch: str) -> bool:
        if not all(isinstance(texts.get(field), str) and texts[field].strip() for field in ANALYSIS_FIELDS):
            return False
        key = analysis_key(job)
        batch["analysis"] = {"key": key, "texts": {field: texts[field] for field in ANALYSIS_FIELDS},
                             "source_epoch": execution_epoch}
        if execution_epoch == self.epoch:
            self.resident.add(key)
        return True


def prompt_admission(*, prompt_kind: str, prompt_profile: str, active_render: dict | None,
                     profiles: dict, free_vram_mb: int | None) -> tuple[bool, str]:
    if active_render is None:
        return True, "analysis window: renderer idle"
    render_profile = active_render.get("profile")
    if render_profile not in profiles["profiles"]:
        return False, "render profile unknown"
    if active_render.get("phase") != "sampling":
        return False, "renderer conditioning, decoding or recovery has VRAM priority"
    render_policy = profiles["profiles"][render_profile]["overlap_policy"]
    cold = prompt_kind == "cold_analysis"
    if cold and not render_policy["cold_analysis_during_sampling"]:
        return False, "H3 Full sampling: cold Step 0–2 waits for an analysis window"
    if prompt_profile not in profiles["profiles"]:
        return False, "prompt profile unknown"
    needed = (render_policy["analysis_headroom_mb"] if cold else
              profiles["profiles"][prompt_profile]["overlap_policy"]["writer_headroom_mb"])
    if not cold and profiles["profiles"][prompt_profile].get("compiler") == "shared_gemma":
        needed = max(needed, profiles["profiles"][prompt_profile]["overlap_policy"]["analysis_headroom_mb"])
    if type(free_vram_mb) is not int or free_vram_mb < needed:
        return False, "measured VRAM headroom unavailable or insufficient"
    return True, "analysis overlap with measured headroom" if cold else "small writer with reused Step 0–2 text"
