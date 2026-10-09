"""Pinned native writer controls and the unchanged v20 final-answer contract."""
from __future__ import annotations

import copy
import re
from .temporal import final_answer

MODEL_FIELDS = ("checkpoint", "writer", "compiler", "encoder", "video_vae", "audio_vae")
STAGE_NODES = {"step3": "2551:2640:2445", "step4": "2551:2640:4275"}
SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music")
GEMMA = "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf"


def validate_stages(stages: dict) -> None:
    if not isinstance(stages, dict) or set(stages) != {"step3", "step4"}:
        raise ValueError("persisted writer stages missing; explicit reconciliation is required")
    for stage, thinking in (("step3", "off"), ("step4", "on")):
        value = stages[stage]
        if not isinstance(value, dict) or set(value) != {"reasoning", "reasoning_budget", "max_tokens", "ctx_size", "final_answer_tokens", "framing_tokens"}:
            raise ValueError("invalid writer stage policy")
        if value["reasoning"] != thinking or any(type(value[key]) is not int for key in value if key != "reasoning"):
            raise ValueError("invalid stage-specific thinking policy")
        budget = value["reasoning_budget"]
        if (thinking == "off" and budget != 0) or (thinking == "on" and not 0 < budget <= 16384):
            raise ValueError("invalid native reasoning budget")
        if value["final_answer_tokens"] < 8192 or value["framing_tokens"] < (64 if thinking == "on" else 0):
            raise ValueError("writer policy would starve the v20 final answer")
        if not value["final_answer_tokens"] + budget + value["framing_tokens"] <= value["max_tokens"] <= 32768:
            raise ValueError("invalid native total generation budget")
        if not value["max_tokens"] + 16384 <= value["ctx_size"] <= 1048576:
            raise ValueError("writer context would reduce the v20 input allowance")


def stage_routes(profile: dict) -> dict:
    validate_stages(profile.get("writer_stages"))
    result = copy.deepcopy(profile["writer_stages"])
    for stage, policy in result.items():
        policy["model"] = GEMMA if stage == "step4" and profile["compiler"] == "shared_gemma" else profile["writer"]
    return result


def native_extra_args(policy: dict) -> str:
    # Both flags exist in the pinned CLI. Separate channels are mandatory even
    # if inherited LLAMA_ARG_THINK requests an unsafe legacy format.
    return f"--reasoning-format deepseek --reasoning-budget {policy['reasoning_budget']}"


def validate_writer_graph(prompt: dict, routes: dict) -> None:
    from .workflow_conversion import _ancestors
    for stage, identifier in STAGE_NODES.items():
        node = prompt.get(identifier, {})
        policy = routes[stage]
        inputs = node.get("inputs", {})
        expected = {key: policy[key] for key in ("model", "reasoning", "max_tokens", "ctx_size")}
        expected.update(mmproj="none", enable_processing=True, extra_args=native_extra_args(policy))
        expected_class = "LLMTextProcessor" if stage == "step3" else "AJCompilerTextProcessor"
        if node.get("class_type") != expected_class or any(inputs.get(key) != value for key, value in expected.items()):
            raise ValueError("converted writer differs from persisted stage policy")
        for consumer in prompt.values():
            for link in consumer.get("inputs", {}).values():
                if isinstance(link, list) and len(link) == 2 and link[0] == identifier and link[1] != 0:
                    raise ValueError("reasoning/performance output cannot feed the v20 pipeline")
    captures = {key for key, node in prompt.items() if node.get("class_type") == "PreviewAny" and
                (key == "5732" or node.get("_meta", {}).get("title") == "STEP 4 — Final H3 Prompt / Output")}
    if not captures or any(STAGE_NODES["step4"] not in _ancestors(prompt, {key}) for key in captures):
        raise ValueError("final capture does not depend on the native compiler answer")
