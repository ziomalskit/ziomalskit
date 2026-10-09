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
    processors = [node for node in result.values() if node["class_type"] == "LLMTextProcessor"]
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
