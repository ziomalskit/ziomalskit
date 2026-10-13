"""Typed browser vocabulary; adapter capabilities own supported ranges/defaults."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer
from .production import IDENTIFIER


def json_number(value):
    if type(value) not in (int, float):
        raise ValueError("render settings require JSON numbers, never strings, booleans or null")
    return value


class SecondPassLora(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(strict=True)
    enabled: bool = Field(True, strict=True)
    strength: float = Field(1.0, ge=-2, le=2, allow_inf_nan=False)

    @field_validator("id")
    @classmethod
    def canonical_id(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("second-pass LoRA requires a registered ID, never a filename or path")
        return value

    @field_validator("strength", mode="before")
    @classmethod
    def numeric_strength(cls, value):
        return json_number(value)


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_mpx: float | None = Field(None, allow_inf_nan=False)
    first_steps: int | None = Field(None, strict=True)
    latent_upscaler_enabled: bool | None = Field(None, strict=True)
    latent_upscaler_mpx: float | None = Field(None, allow_inf_nan=False)
    second_pass_enabled: bool | None = Field(None, strict=True)
    second_steps: int | None = Field(None, strict=True)
    second_pass_loras: list[SecondPassLora] | None = None

    @field_validator("target_mpx", "first_steps", "second_steps", "latent_upscaler_mpx", mode="before")
    @classmethod
    def numeric_json_only(cls, value):
        return json_number(value)

    @field_validator("latent_upscaler_enabled", "second_pass_enabled", mode="before")
    @classmethod
    def exact_bool(cls, value):
        if type(value) is not bool:
            raise ValueError("render toggles require JSON booleans")
        return value

    @field_validator("second_pass_loras", mode="before")
    @classmethod
    def exact_list(cls, value):
        if type(value) is not list:
            raise ValueError("second-pass LoRAs require an array")
        return value

    @model_serializer(mode="wrap")
    def omit_unset_controls(self, handler):
        # Absence means resolve the selected adapter's canonical default.
        # Serializing a valid omitted field must not emit an invalid null.
        return {key: value for key, value in handler(self).items() if value is not None}
