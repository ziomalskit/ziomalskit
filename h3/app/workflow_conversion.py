"""Validate and isolate the API graph produced by the pinned Comfy converter.

This module never invokes ComfyUI or submits work.  The caller must convert the
patched UI workflow using the live worker's catalog before calling it.
"""

from __future__ import annotations

import copy
from typing import Any, Literal


class WorkflowPreparationError(ValueError):
    """The converted graph does not satisfy the audited execution contract."""


# These classes have no Python backend in the audited rgthree revision. Unknown
# custom classes are deliberately not treated as frontend decorations.
FRONTEND_ONLY_CLASSES = frozenset({"Label (rgthree)", "Fast Groups Bypasser (rgthree)"})
PROMPT_PREVIEW_CLASSES = frozenset({"PreviewAny", "PreviewImage"})
RENDER_SINK_ID = "7331"
FINAL_PREVIEW_ID = "5732"
PROMPT_OVERRIDE_ID = "2632"
FINAL_RESOLVER_ID = "3384:2633"


def _link(value: Any, *, node_id: str, input_name: str) -> tuple[str, int] | None:
    if not isinstance(value, list):
        return None
    if (
        len(value) != 2
        or not isinstance(value[0], str)
        or not value[0]
        or not isinstance(value[1], int)
        or isinstance(value[1], bool)
        or value[1] < 0
    ):
        raise WorkflowPreparationError(f"Malformed API link at {node_id}.{input_name}")
    return value[0], value[1]


def validate_api_prompt(prompt: dict, object_info: dict) -> None:
    """Reject unknown classes, malformed nodes, dangling links and bad slots.

    This is a structural check against a real catalog, not model/inference
    validation. ComfyUI still performs its input and execution validation.
    """
    if not isinstance(prompt, dict) or not prompt:
        raise WorkflowPreparationError("Converted API prompt must be a nonempty object")
    if not isinstance(object_info, dict) or not object_info:
        raise WorkflowPreparationError("The worker's nonempty /object_info catalog is required")
    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not node_id or not isinstance(node, dict):
            raise WorkflowPreparationError("API node ids must be nonempty strings and nodes must be objects")
        cls = node.get("class_type")
        if not isinstance(cls, str) or not cls or not isinstance(object_info.get(cls), dict):
            raise WorkflowPreparationError(f"Unregistered backend class at node {node_id}: {cls!r}")
        if not isinstance(node.get("inputs"), dict):
            raise WorkflowPreparationError(f"Missing input object at node {node_id}")
    for node_id, node in prompt.items():
        for name, value in node["inputs"].items():
            source = _link(value, node_id=node_id, input_name=name)
            if source is None:
                continue
            source_id, slot = source
            if source_id not in prompt:
                raise WorkflowPreparationError(f"Dangling API link at {node_id}.{name}: {source_id}")
            outputs = object_info[prompt[source_id]["class_type"]].get("output")
            if not isinstance(outputs, list) or slot >= len(outputs):
                raise WorkflowPreparationError(f"Invalid output slot at {node_id}.{name}: {source_id}[{slot}]")


def _ancestors(prompt: dict, roots: set[str]) -> set[str]:
    result: set[str] = set()
    pending = list(roots)
    while pending:
        node_id = pending.pop()
        if node_id in result:
            continue
        result.add(node_id)
        for name, value in prompt[node_id]["inputs"].items():
            source = _link(value, node_id=node_id, input_name=name)
            if source is not None:
                pending.append(source[0])
    return result


def _route_approved_prompt(prompt: dict, approved_prompt: str | None) -> None:
    if not isinstance(approved_prompt, str) or not approved_prompt.strip():
        raise WorkflowPreparationError("Rendering requires the candidate's nonempty approved prompt")
    override = prompt.get(PROMPT_OVERRIDE_ID)
    resolver = prompt.get(FINAL_RESOLVER_ID)
    if not isinstance(override, dict) or override.get("class_type") != "easy positive":
        raise WorkflowPreparationError("Audited prompt override node 2632 is missing or changed")
    if override["inputs"].get("positive") != approved_prompt:
        raise WorkflowPreparationError("Converted prompt override differs from the approved candidate")
    if (
        not isinstance(resolver, dict)
        or resolver.get("class_type") != "ComfySwitchNode"
        or resolver["inputs"].get("on_false") != [PROMPT_OVERRIDE_ID, 0]
    ):
        raise WorkflowPreparationError("Audited final-prompt resolver contract has changed")
    rewired = 0
    for node_id, node in prompt.items():
        for name, value in node["inputs"].items():
            source = _link(value, node_id=node_id, input_name=name)
            if source == (FINAL_RESOLVER_ID, 0):
                node["inputs"][name] = [PROMPT_OVERRIDE_ID, 0]
                rewired += 1
    if not rewired:
        raise WorkflowPreparationError("No consumers of the audited final prompt were found")


def prepare_api_prompt(
    converted_prompt: dict,
    object_info: dict,
    *,
    phase: Literal["prompt", "render"],
    approved_prompt: str | None = None,
) -> dict:
    """Return a validated backend graph containing only this phase's sinks.

    ``converted_prompt`` is the ``data.prompt`` object from the pinned CLI's
    ``comfy --json run --workflow ... --print-prompt`` envelope. ``object_info``
    must come from the same worker. Both arguments remain unchanged.
    """
    if phase not in ("prompt", "render"):
        raise WorkflowPreparationError(f"Unknown workflow phase: {phase!r}")
    if not isinstance(converted_prompt, dict):
        raise WorkflowPreparationError("Converted API prompt must be an object")
    prompt = copy.deepcopy(converted_prompt)
    for node_id, node in list(prompt.items()):
        if isinstance(node, dict) and node.get("class_type") in FRONTEND_ONLY_CLASSES:
            del prompt[node_id]
    # Validate BEFORE pruning so a missing custom package cannot be silently
    # hidden simply because its node happens to be disconnected in this graph.
    validate_api_prompt(prompt, object_info)
    if phase == "render":
        sink = prompt.get(RENDER_SINK_ID)
        if not isinstance(sink, dict) or sink.get("class_type") != "SaveVideo":
            raise WorkflowPreparationError("Audited SaveVideo output 7331 is required for rendering")
        if object_info["SaveVideo"].get("output_node") is not True:
            raise WorkflowPreparationError("The worker does not register SaveVideo as an output node")
        _route_approved_prompt(prompt, approved_prompt)
        roots = {RENDER_SINK_ID}
    else:
        if approved_prompt is not None:
            raise WorkflowPreparationError("Prompt generation must not receive a render override")
        final = prompt.get(FINAL_PREVIEW_ID)
        if not isinstance(final, dict) or final.get("class_type") != "PreviewAny":
            raise WorkflowPreparationError("Final prompt PreviewAny output 5732 is required")
        roots = {
            node_id
            for node_id, node in prompt.items()
            if node["class_type"] in PROMPT_PREVIEW_CLASSES
            and object_info[node["class_type"]].get("output_node") is True
        }
        if FINAL_PREVIEW_ID not in roots:
            raise WorkflowPreparationError("The worker does not register final prompt preview as an output node")
    retained = _ancestors(prompt, roots)
    prepared = {node_id: node for node_id, node in prompt.items() if node_id in retained}
    classes = {node["class_type"] for node in prepared.values()}
    if phase == "render" and classes & {"LLMTextProcessor", "AJCompilerTextProcessor"}:
        raise WorkflowPreparationError("Render output still depends on the autoprompter")
    if phase == "prompt" and classes & {"SamplerCustomAdvanced", "KSampler", "KSamplerAdvanced", "SaveVideo"}:
        raise WorkflowPreparationError("Prompt previews unexpectedly depend on rendering")
    if phase == "render" and PROMPT_OVERRIDE_ID not in prepared:
        raise WorkflowPreparationError("Render output does not consume the approved prompt")
    validate_api_prompt(prepared, object_info)
    return prepared
