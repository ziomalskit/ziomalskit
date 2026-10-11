"""Typed browser vocabulary; adapter capabilities own supported ranges/defaults."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_mpx: float | None = Field(None, allow_inf_nan=False)
    first_steps: int | None = Field(None, strict=True)
    second_steps: int | None = Field(None, strict=True)

    @field_validator("target_mpx", "first_steps", "second_steps", mode="before")
    @classmethod
    def numeric_json_only(cls, value):
        if type(value) not in (int, float):
            raise ValueError("render settings require JSON numbers, never strings, booleans or null")
        return value
