"""Trusted production profiles and LoRA identities; no client paths or URLs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from collections.abc import Mapping
from types import MappingProxyType

PROFILE_LABELS = {"h3_full": "H3 Full", "10eros_full": "10Eros Full"}
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


def immutable_registry(value):
    """One deeply immutable, validated controller-generation snapshot."""
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({key: immutable_registry(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(immutable_registry(item) for item in value)
    return value


def registry_json(value):
    """Return public/identity JSON without exposing mutable snapshot objects."""
    if isinstance(value, Mapping):
        return {key: registry_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [registry_json(item) for item in value]
    return value


def registry_identity(registry, bridge=None):
    # Defaults are used only at creation: every selection/strength is persisted.
    # Labels/help and GPU acceptance annotations do not route or verify a file.
    excluded = {"display_name", "help", "notes", "defaults", "gpu_validation", "installation"}
    def functional(entry):
        return {key: registry_json(value) for key, value in entry.items() if key not in excluded}
    identity = {"loras": {key: functional(entry) for key, entry in registry.items()}}
    if bridge is not None:
        identity["conditioning_bridge"] = functional(bridge)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def lora_execution_identity(registry, bridge, profile, settings, render_settings, bridge_setting):
    """Bind ordered frozen selections and only the resources they execute.

    Inactive rows remain frozen UI values, not dependencies on today's catalog.
    All execution and verification still use the same trusted startup snapshot.
    """
    if not isinstance(settings, list) or not isinstance(bridge_setting, dict):
        raise ValueError("persisted LoRA selection/bridge missing; reconciliation required")
    first = resolve_loras(registry, profile, settings, frozen=True, ids_only=True)
    second_active = render_settings["second_pass_enabled"]
    second = resolve_loras(registry, profile, render_settings["second_pass_loras"],
                           frozen=True, ids_only=True, active=second_active)
    strength = bridge_setting.get("strength")
    if (type(bridge_setting.get("enabled")) is not bool or type(strength) not in (int, float)
            or not math.isfinite(strength) or not -2 <= strength <= 2):
        raise ValueError("invalid frozen conditioning bridge settings")
    if bridge_setting["enabled"] and any(bridge_setting.get(key) != bridge[key] for key in ("id", "filename")):
        raise ValueError("persisted conditioning bridge routing changed")
    selected = {row["id"] for row in first if row["enabled"]}
    selected.update(row["id"] for row in second if second_active and row["enabled"])
    # Other profiles and presentation/evidence annotations do not route this
    # job. Actual used source, filename, hash, bounds and compatibility do.
    resources = {}
    for identifier in selected:
        entry = registry_json(registry[identifier])
        entry.pop("metadata_evidence", None)
        entry["compatibility"] = {profile: entry["compatibility"][profile]}
        resources[identifier] = entry
    used_bridge = registry_json(bridge) if bridge_setting["enabled"] else None
    if used_bridge is not None:
        used_bridge.pop("metadata_evidence", None)
    identity = {"schema_version": 1, "profile": profile, "loras": first,
                "second_pass_loras": second, "second_pass_enabled": second_active,
                "conditioning_bridge": bridge_setting,
                "resources": registry_identity(resources, used_bridge)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def functional_policy(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("persisted functional profile policy is malformed; reconciliation required")
    memory, overlap = profile.get("memory_policy", {}), profile.get("overlap_policy", {})
    if not isinstance(memory, dict) or not isinstance(overlap, dict):
        raise ValueError("persisted functional memory/overlap policy is malformed; reconciliation required")
    if (memory.get("encoder_release") != "after_conditioning" or memory.get("render_release") != "after_video"
            or memory.get("sampling_priority") != "render"):
        raise ValueError("persisted functional memory policy missing or incompatible; explicit reconciliation required")
    if type(overlap.get("cold_analysis_during_sampling")) is not bool or any(
        type(overlap.get(key)) is not int or overlap[key] <= 0 for key in ("analysis_headroom_mb", "writer_headroom_mb")):
        raise ValueError("persisted functional overlap policy missing; explicit reconciliation required")
    if profile.get("compiler") not in {"profile_writer", "shared_gemma"}:
        raise ValueError("persisted compiler admission route missing")
    return {"memory_policy": {key: memory[key] for key in ("encoder_release", "render_release", "sampling_priority")},
            "overlap_policy": {key: overlap[key] for key in ("cold_analysis_during_sampling", "analysis_headroom_mb", "writer_headroom_mb")},
            "compiler": profile["compiler"]}


def validate_lora_provenance(entry: dict) -> None:
    if entry.get("provenance_status") not in {"verified_source", "external/user-provided"}:
        raise ValueError("invalid LoRA provenance status")
    if entry.get("gpu_validation") not in {"pending", "validated"}:
        raise ValueError("invalid LoRA GPU validation state")
    if entry["provenance_status"] == "verified_source":
        source = entry.get("source") or {}
        if source.get("provider") != "huggingface" or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")):
            raise ValueError("verified LoRA source missing")
        relative_path(entry["repository_path"])
        if not re.fullmatch(r"[a-f0-9]{40}", entry.get("revision", "")) or not re.fullmatch(r"[a-f0-9]{64}", entry.get("sha256", "")) or type(entry.get("size_bytes")) is not int or entry["size_bytes"] <= 0:
            raise ValueError("verified LoRA exact provenance missing")


def read_bridge(path: Path, *, document=None) -> dict:
    entry = (json.loads(path.read_text()) if document is None else document)["conditioning_bridge"]
    if not isinstance(entry.get("id"), str) or not IDENTIFIER.fullmatch(entry["id"]):
        raise ValueError("invalid conditioning bridge ID")
    filename = relative_path(entry["filename"])
    if "/" in filename or not filename.endswith(".safetensors"):
        raise ValueError("invalid conditioning bridge filename")
    if set(entry.get("defaults", {})) != set(PROFILE_LABELS):
        raise ValueError("conditioning bridge defaults must be per profile")
    for value in entry["defaults"].values():
        strength = value.get("strength")
        if type(value.get("enabled")) is not bool or type(strength) not in (int, float) or not math.isfinite(strength) or not -2 <= strength <= 2:
            raise ValueError("invalid conditioning bridge default")
    validate_lora_provenance(entry)
    return entry


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid trusted relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", "..", ""} for part in value.split("/")):
        raise ValueError("invalid trusted relative path")
    return value


def read_profiles(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("default_profile") not in PROFILE_LABELS:
        raise ValueError("invalid production profile configuration")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILE_LABELS):
        raise ValueError("exactly two production profiles are required")
    runtime = data.get("writer_runtime", {})
    if set(runtime) != {"llama_cpp_revision", "llm_node_revision"} or any(not re.fullmatch(r"[a-f0-9]{40}", value) for value in runtime.values()):
        raise ValueError("immutable writer runtime identity is required")
    from .writer_policy import validate_stages
    for identifier, profile in profiles.items():
        if profile.get("label") != PROFILE_LABELS[identifier] or profile.get("user_facing") is not True:
            raise ValueError("invalid production profile identity")
        from .workflow_adapters import REGISTRY
        if profile.get("workflow_id") not in REGISTRY:
            raise ValueError("production profile requires a trusted workflow adapter")
        for field in ("render_template", "checkpoint", "writer", "encoder", "video_vae", "audio_vae", "analysis_model", "analysis_mmproj"):
            relative_path(profile[field])
        if any("/" in profile[field] or not profile[field].endswith(".gguf") for field in ("writer", "analysis_model", "analysis_mmproj")):
            raise ValueError("profile LLM routes require registered GGUF basenames")
        if any(word in profile["checkpoint"].lower() for word in ("int8", "w4a8", "turbo", "quant")):
            raise ValueError("legacy checkpoint cannot be a production profile")
        policy = profile.get("memory_policy", {})
        if policy.get("encoder_release") != "after_conditioning" or policy.get("render_release") != "after_video":
            raise ValueError("phase boundary memory policy is required")
        overlap = profile.get("overlap_policy", {})
        if profile.get("compiler") not in {"profile_writer", "shared_gemma"}:
            raise ValueError("unknown controlled compiler benchmark route")
        validate_stages(profile.get("writer_stages"))
        if type(overlap.get("cold_analysis_during_sampling")) is not bool:
            raise ValueError("explicit overlap policy is required")
        for field in ("analysis_headroom_mb", "writer_headroom_mb"):
            if type(overlap.get(field)) is not int or overlap[field] <= 0:
                raise ValueError("positive overlap headroom is required")
    return data


def read_loras(path: Path, *, document=None) -> dict[str, dict]:
    data = json.loads(path.read_text()) if document is None else document
    if data.get("schema_version") != 1 or type(data.get("slots")) is not int or data["slots"] < 8:
        raise ValueError("at least eight dynamic LoRA slots are required")
    entries = data.get("loras")
    if not isinstance(entries, list):
        raise ValueError("invalid LoRA registry")
    registry, filenames = {}, set()
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in registry:
            raise ValueError("invalid or duplicate LoRA ID")
        filename = relative_path(entry["filename"])
        if "/" in filename or not filename.endswith(".safetensors") or filename in filenames:
            raise ValueError("invalid or duplicate LoRA filename")
        minimum, maximum = entry["strength_range"]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (minimum, maximum)) or not -2 <= minimum <= maximum <= 2:
            raise ValueError("invalid LoRA strength range")
        if set(entry.get("defaults", {})) != set(PROFILE_LABELS):
            raise ValueError("LoRA defaults must be per profile")
        if set(entry.get("compatibility", {})) != set(PROFILE_LABELS):
            raise ValueError("explicit profile compatibility is required")
        for profile, default in entry["defaults"].items():
            strength = default.get("strength")
            if type(default.get("enabled")) is not bool or type(strength) not in (int, float) or not math.isfinite(strength) or not minimum <= strength <= maximum:
                raise ValueError("invalid LoRA default")
            if default["enabled"] and entry["compatibility"][profile] == "incompatible":
                raise ValueError("incompatible LoRA cannot be enabled by default")
        validate_lora_provenance(entry)
        registry[identifier] = entry
        filenames.add(filename)
    bridge = read_bridge(path, document=data)
    if bridge["id"] in registry or bridge["filename"] in filenames:
        raise ValueError("duplicate conditioning bridge identity")
    return registry


def resolve_loras(registry: dict, profile: str, settings: list[dict] | None, *, frozen=False,
                  ids_only=False, supplied_only=False, active=True) -> list[dict]:
    if profile not in PROFILE_LABELS:
        raise ValueError("unknown production profile")
    result = {identifier: {"id": identifier, **entry["defaults"][profile]} for identifier, entry in registry.items()}
    if supplied_only or frozen:
        result = {}
    # Frozen rows are the entire ordered stack. Registry order, new entries and
    # defaults must never reorder or add disabled rows to a persisted selection.
    seen = set()
    for setting in settings or []:
        if not isinstance(setting, dict):
            raise ValueError("invalid LoRA settings")
        # Only explicit registered identifiers/legacy canonical names are accepted.
        # No basename normalization can transform an untrusted path into an ID.
        value = setting.get("id", setting.get("name"))
        inactive = frozen and (setting.get("enabled") is False or not active)
        identifier = value if isinstance(value, str) and value in registry else None if ids_only else next((key for key, item in registry.items() if item["filename"] == value), None)
        if inactive and isinstance(value, str) and IDENTIFIER.fullmatch(value):
            identifier = value
        if identifier is None:
            raise ValueError("unknown LoRA identifier")
        if identifier in seen:
            raise ValueError("duplicate LoRA settings")
        seen.add(identifier)
        entry = registry.get(identifier)
        strength = setting.get("strength")
        if (type(setting.get("enabled")) is not bool or type(strength) not in (int, float) or
                not math.isfinite(strength) or
                (not inactive and not entry["strength_range"][0] <= strength <= entry["strength_range"][1])):
            raise ValueError("LoRA strength outside registry bounds")
        if not inactive and setting["enabled"] and entry["compatibility"][profile] == "incompatible":
            raise ValueError("LoRA incompatible with this profile")
        result[identifier] = {"id": identifier, "enabled": setting["enabled"], "strength": float(strength)}
    return list(result.values())


def patch_lora_slots(node: dict, registry: dict, settings: list[dict], *, slots: int = 16) -> None:
    selected = [setting for setting in settings if setting["enabled"]]
    if slots < 8 or len(selected) > slots:
        raise ValueError("LoRA slot capacity exceeded")
    header = {"divider": {}, "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"}}
    for index in range(slots):
        if index < len(selected):
            setting = selected[index]
            entry = registry.get(setting["id"])
            if entry is None:
                raise ValueError("unknown LoRA identifier")
            row = {"on": True, "lora": entry["filename"], "strength": setting["strength"], "strengthTwo": None}
        else:
            row = {"on": False, "lora": "None", "strength": 0.0, "strengthTwo": None}
        header[f"lora_{index + 1}"] = row
    header["➕ Add Lora"] = ""
    node["widgets_values_named"] = header
    node["widgets_values"] = copy.deepcopy(list(header.values()))
