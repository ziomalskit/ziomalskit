"""Current request fingerprints and exact, opt-in BASE-schema retry matching.

The legacy schema below is the BatchRequest/LoraSetting contract at
c6f033ab41a4456fd55558e652a563d8c31418d1, independently backed by a BASE snapshot.
"""
import hashlib
import json
from pathlib import Path
import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class BaseLoraSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(validation_alias=AliasChoices("id", "name"))
    enabled: bool
    strength: float = Field(ge=-2, le=2, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value):
        if not value or value != Path(value).name or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("LoRA name must be a canonical filename")
        return value


class BaseBatchRequest(BaseModel):
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
    loras: list[BaseLoraSetting] = []


def fingerprint(value):
    # BASE used these exact defaults: spaces, sorted keys, literal Unicode.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def request_fingerprints(request):
    excluded = {"request_id"} | ({"prompt_mode", "manual_final_prompt"} if request.prompt_mode == "autoprompt" else set())
    current = fingerprint(request.model_dump(mode="json", exclude=excluded))
    legacy = None
    fields = set(BaseBatchRequest.model_fields)
    if request.prompt_mode == "autoprompt" and request.model_fields_set <= fields:
        values = request.model_dump(mode="json")
        historical = BaseBatchRequest.model_validate({key: values[key] for key in fields})
        legacy = fingerprint(historical.model_dump(mode="json", exclude={"request_id"}))
    return current, legacy
