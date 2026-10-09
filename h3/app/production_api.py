"""Production-only API graph adaptation; prompt instructions remain in v20."""
from __future__ import annotations

import copy

from .analysis_cache import ANALYSIS_FIELDS
from .workflow_conversion import _ancestors, validate_api_prompt, WorkflowPreparationError

ANALYSIS_TITLES = dict(zip(ANALYSIS_FIELDS, (
    "STEP 0 — JoyCaption Visual Facts / Output",
    "STEP 1 — Expanded Intent / Output",
    "STEP 2 — Reference Map / Output",
)))
CONDITIONING_BOUNDARY = "aj_conditioning_complete"
VIDEO_BOUNDARY = "aj_video_complete"


def frozen_duration(prompt: dict, identity: dict, catalog: dict) -> dict:
    from .temporal import validate_duration, FRAME_EXPRESSION, LENGTH_EXPRESSION
    validate_duration(identity)
    result = copy.deepcopy(prompt)
    for key, expression in (("1731:184:134", FRAME_EXPRESSION), ("1731:184:137", LENGTH_EXPRESSION)):
        if key in result and result[key].get("inputs", {}).get("expression") != expression:
            raise WorkflowPreparationError("v20 legal duration mapping changed")
    if result.get("1731:184:135", {}).get("inputs", {}).get("value") != identity["requested_duration_seconds"]:
        raise WorkflowPreparationError("requested duration differs from persisted identity")
    replacements = {("1731:184:134", 1): ["aj_frozen_duration", 0], ("1731:184:137", 0): ["aj_frozen_duration", 1]}
    for node in result.values():
        for name, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and tuple(value) in replacements:
                node["inputs"][name] = replacements[tuple(value)]
    result["aj_frozen_duration"] = {"class_type": "AJFrozenDuration", "inputs": {
        "legal_frame_count": identity["legal_frame_count"], "effective_duration_seconds": identity["effective_duration_seconds"]}}
    roots = {key for key, node in result.items() if catalog[node["class_type"]].get("output_node") is True}
    retained = _ancestors(result, roots)
    result = {key: node for key, node in result.items() if key in retained}
    validate_api_prompt(result, catalog)
    return result


def temporal_guards(prompt: dict, catalog: dict) -> dict:
    result = copy.deepcopy(prompt)
    def insert(title, identifier, node):
        captures = [value for value in result.values() if value.get("class_type") == "PreviewAny" and value.get("_meta", {}).get("title") == title]
        if len(captures) != 1 or not isinstance(captures[0]["inputs"].get("source"), list):
            raise WorkflowPreparationError("ambiguous v20 temporal capture mapping")
        source = captures[0]["inputs"]["source"]
        for consumer in result.values():
            for field, value in list(consumer["inputs"].items()):
                if value == source:
                    consumer["inputs"][field] = [identifier, 0]
        node["inputs"]["creative_plan" if identifier == "aj_creative_timeline" else "final_h3_prompt"] = source
        result[identifier] = node
    insert("STEP 3 — Creative Director / Output", "aj_creative_timeline", {
        "class_type": "AJCreativeTimelineGuard", "inputs": {"effective_duration_seconds": ["aj_frozen_duration", 1]}})
    insert("STEP 4 — Final H3 Prompt / Output", "aj_final_timeline", {
        "class_type": "AJFinalPromptTimeGuard", "inputs": {"canonical_creative_plan": ["aj_creative_timeline", 0],
        "effective_duration_seconds": ["aj_frozen_duration", 1]}})
    if "aj_creative_timeline" not in _ancestors(result, {"2551:2640:4275"}):
        raise WorkflowPreparationError("Step 4 does not consume guarded creative timeline")
    # No raw Step 3 branch may bypass the guard on its way to Step 4.
    seen, pending = set(), ["2551:2640:4275"]
    while pending:
        key = pending.pop()
        if key in seen or key == "aj_creative_timeline":
            continue
        seen.add(key)
        pending.extend(link[0] for link in result[key]["inputs"].values() if isinstance(link, list) and len(link) == 2 and link[0] in result)
    if "2551:2640:2445" in seen:
        raise WorkflowPreparationError("raw creative plan bypasses temporal guard")
    validate_api_prompt(result, catalog)
    return result


def validate_temporal_graph(prompt: dict, identity: dict) -> None:
    expected = {"legal_frame_count": identity["legal_frame_count"], "effective_duration_seconds": identity["effective_duration_seconds"]}
    if prompt.get("aj_frozen_duration", {}) != {"class_type": "AJFrozenDuration", "inputs": expected}:
        raise ValueError("persisted duration differs from submitted graph")
    for title, identifier, kind in (("STEP 3 — Creative Director / Output", "aj_creative_timeline", "AJCreativeTimelineGuard"),
                                    ("STEP 4 — Final H3 Prompt / Output", "aj_final_timeline", "AJFinalPromptTimeGuard")):
        if prompt.get(identifier, {}).get("class_type") != kind or prompt[identifier]["inputs"].get("effective_duration_seconds") != ["aj_frozen_duration", 1]:
            raise ValueError("temporal guard mapping is missing or changed")
        captures = [node for node in prompt.values() if node.get("class_type") == "PreviewAny" and node.get("_meta", {}).get("title") == title]
        if len(captures) != 1 or captures[0]["inputs"].get("source") != [identifier, 0]:
            raise ValueError("prompt capture bypasses temporal guard")
    if prompt["aj_final_timeline"]["inputs"].get("canonical_creative_plan") != ["aj_creative_timeline", 0]:
        raise ValueError("final guard uses an unguarded creative plan")


def reuse_analysis(prompt: dict, texts: dict, catalog: dict) -> dict:
    """Substitute durable Step 0–2 outputs before pruning their dependencies.

    Every consumer of each preview's source receives the same exact text. This
    includes the writer's inputs, rather than only the controller's previews.
    No model cache is assumed to survive a service restart.
    """
    result = copy.deepcopy(prompt)
    replacements = {}
    for field, title in ANALYSIS_TITLES.items():
        if not isinstance(texts.get(field), str) or not texts[field].strip():
            raise WorkflowPreparationError("incomplete durable analysis")
        matches = [node for node in result.values() if node.get("class_type") == "PreviewAny"
                   and node.get("_meta", {}).get("title") == title]
        if len(matches) != 1:
            raise WorkflowPreparationError("ambiguous v20 analysis capture mapping")
        source = matches[0]["inputs"].get("source")
        if not isinstance(source, list) or len(source) != 2:
            raise WorkflowPreparationError("missing v20 analysis source")
        identifier = "aj_analysis_" + field
        result[identifier] = {"class_type": "AJAnalysisText", "inputs": {"text": texts[field]},
                              "_meta": {"title": "Durable " + field}}
        replacements[tuple(source)] = [identifier, 0]
    for node in result.values():
        for name, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and tuple(value) in replacements:
                node["inputs"][name] = replacements[tuple(value)]
    # Tile/image previews were useful during the first analysis. Keeping them
    # as output roots would needlessly re-run the heavy vision pipeline.
    roots = {key for key, node in result.items() if node["class_type"] == "PreviewAny" and (
        node.get("_meta", {}).get("title") in set(ANALYSIS_TITLES.values()) | {
            "STEP 3 — Creative Director / Output", "STEP 4 — Final H3 Prompt / Output"}
        or key == "5732")}
    retained = _ancestors(result, roots)
    result = {key: node for key, node in result.items() if key in retained}
    processors = [node for node in result.values() if node["class_type"] in {"LLMTextProcessor", "AJCompilerTextProcessor"}]
    if len(processors) != 2:
        raise WorkflowPreparationError("durable analysis must leave exactly Step 3 and Step 4")
    validate_api_prompt(result, catalog)
    return result


def phase_boundaries(prompt: dict, catalog: dict, policy: dict) -> tuple[dict, dict]:
    """Make encoder release an ancestor of the transformer and both samplers."""
    result = copy.deepcopy(prompt)
    loader_id = "4595:4529"
    guiders = ("7334:6769", "7335:29")
    loader = result.get(loader_id, {})
    if loader.get("class_type") != "UNETLoader" or any(result.get(key, {}).get("class_type") != "BasicGuider" for key in guiders):
        raise WorkflowPreparationError("v20 renderer dependency mapping changed")
    conditions = [result[key]["inputs"]["conditioning"] for key in guiders]
    dependencies = _ancestors(result, {value[0] for value in conditions})
    if any(result[key]["class_type"] in {"UNETLoader", "SamplerCustomAdvanced"} for key in dependencies):
        raise WorkflowPreparationError("conditioning depends on heavy sampling; late loading would be cyclic")
    result[CONDITIONING_BOUNDARY] = {
        "class_type": "AJConditioningBoundary", "inputs": {
            "first": conditions[0], "second": conditions[1],
            "release_encoder": policy["encoder_release"] == "after_conditioning"},
        "_meta": {"title": "Release encoder before transformer"}}
    for index, key in enumerate(guiders):
        result[key]["inputs"]["conditioning"] = [CONDITIONING_BOUNDARY, index]
    loader["class_type"] = "AJLateUNETLoader"
    loader["inputs"]["after_conditioning"] = [CONDITIONING_BOUNDARY, 2]
    sink = result["7331"]
    video = sink["inputs"]["video"]
    result[VIDEO_BOUNDARY] = {
        "class_type": "AJVideoBoundary", "inputs": {"video": video,
            "release_models": policy["render_release"] == "after_video"},
        "_meta": {"title": "Release render resources after video"}}
    sink["inputs"]["video"] = [VIDEO_BOUNDARY, 0]
    validate_api_prompt(result, catalog)
    return result, {
        "conditioning": sorted(dependencies | {CONDITIONING_BOUNDARY}),
        "sampling": [key for key, node in result.items() if node["class_type"] == "SamplerCustomAdvanced"],
        "decoding": [key for key, node in result.items() if node["class_type"] in {"VAEDecode", "VAEDecodeAudio", "CreateVideo", "AJVideoBoundary", "SaveVideo"}],
    }


def event_phase(node_id: str, mapping: dict) -> str:
    # Unknown nodes never open an overlap window. Progress within a sampler
    # keeps its sampling phase; a decoding event closes that window.
    for phase in ("decoding", "sampling", "conditioning"):
        if node_id in mapping.get(phase, []):
            return phase
    return "unknown"
