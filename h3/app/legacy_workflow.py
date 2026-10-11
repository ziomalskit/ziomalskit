"""Explicit legacy diagnostic graphs; production composition never calls this."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

def node_by_id(wf: dict, node_id: int) -> dict:
    matches = [node for node in wf["nodes"] if node.get("id") == node_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one UI node at {node_id}, found {len(matches)}")
    return matches[0]


def iter_all_nodes(doc: dict):
    for n in doc.get("nodes", []):
        yield n
    for sg in doc.get("definitions", {}).get("subgraphs", []):
        yield from iter_all_nodes(sg)


def set_named_and_positional(node: dict, field: str, value: Any) -> None:
    node.setdefault("widgets_values_named", {})[field] = value
    if node.get("id") == 4595 and field == "unet_name":
        node["widgets_values"][0] = value
    elif node.get("id") in (2624, 2632) and field == "positive":
        node["widgets_values"][0] = value
    elif node.get("type") == "LoadImage" and field == "image":
        node["widgets_values"][0] = value
    elif node.get("type") == "LoadAudio" and field == "audio":
        node["widgets_values"][0] = value


def patch_rgthree_seed(wf: dict, node_id: int, seed: int) -> None:
    n = node_by_id(wf, node_id)
    if n.get("type") != "Seed (rgthree)":
        raise RuntimeError(f"Expected Seed (rgthree) at node {node_id}, got {n.get('type')}")
    n.setdefault("widgets_values_named", {})["seed"] = int(seed)
    if not n.get("widgets_values"):
        raise RuntimeError(f"Seed node {node_id} has no positional widgets")
    n["widgets_values"][0] = int(seed)


def set_collect_refs_audio_mode(wf: dict, choice: str) -> None:
    n = node_by_id(wf, 2249)
    n.setdefault("widgets_values_named", {})["choice_1"] = choice
    vals = n.get("widgets_values") or []
    if len(vals) < 3:
        raise RuntimeError("Collect Refs node 2249 has unexpected widget layout")
    vals[2] = choice


def set_primary_audio_reference_enabled(wf: dict, enabled: bool) -> None:
    # Node 3423 exposes Audio 0 `is_reference`; its BOOLEAN output feeds
    # Set_ref_audio_0_apply. This makes "no audio" truly mean no H3 audio ref.
    n = node_by_id(wf, 3423)
    vals = n.get("widgets_values") or []
    if not vals:
        raise RuntimeError("Audio 0 controller 3423 has unexpected widget layout")
    n.setdefault("widgets_values_named", {})["value_3"] = bool(enabled)
    vals[0] = bool(enabled)


def patch_llm_pipeline_seeds(wf: dict, analysis_seed: int, prompt_seed: int) -> None:
    """
    Patch the REAL seed sources used by the LLM dashboard.

    The four LLMTextProcessor nodes have their `seed` sockets connected, so their
    local widget values are only mirrors. Node 4022 is the upstream controller
    that actually feeds Step 1/2/3/4 seeds into the nested dashboard.

    Step 1–2: fixed per batch -> cacheable across 10 candidates.
    Step 3–4: unique per candidate -> actual prompt diversity.
    """
    controller = None
    downstream = {}
    for n in iter_all_nodes(wf):
        if n.get("id") == 4022:
            controller = n
        if n.get("id") in (2441, 2447, 2445, 4275):
            downstream[n["id"]] = n

    if controller is None:
        raise RuntimeError("Could not find LLM dashboard seed controller node 4022")

    vals = controller.get("widgets_values") or []
    if len(vals) <= 17:
        raise RuntimeError("LLM dashboard seed controller 4022 has unexpected widget layout")

    seeds = {
        "value_4": int(analysis_seed),       # Step 1
        "value_4_1": int(analysis_seed) + 1, # Step 2
        "value_4_2": int(prompt_seed),       # Step 3
        "value_4_3": int(prompt_seed) + 1,   # Step 4
    }
    controller.setdefault("widgets_values_named", {}).update(seeds)
    for idx, value in ((5, seeds["value_4"]), (9, seeds["value_4_1"]),
                       (13, seeds["value_4_2"]), (17, seeds["value_4_3"])):
        vals[idx] = value

    # Keep local widgets synchronized too; connected sockets remain authoritative.
    mirrors = {
        2441: seeds["value_4"],
        2447: seeds["value_4_1"],
        2445: seeds["value_4_2"],
        4275: seeds["value_4_3"],
    }
    missing = set(mirrors) - set(downstream)
    if missing:
        raise RuntimeError(f"Missing LLM processor nodes: {sorted(missing)}")
    for nid, seed in mirrors.items():
        n = downstream[nid]
        expected_type = "AJCompilerTextProcessor" if nid == 4275 and wf.get("extra", {}).get("aj_production") else "LLMTextProcessor"
        if n.get("type") != expected_type:
            raise RuntimeError(f"Expected LLMTextProcessor at {nid}, got {n.get('type')}")
        n.setdefault("widgets_values_named", {})["seed"] = int(seed)
        if len(n.get("widgets_values", [])) <= 13:
            raise RuntimeError(f"LLMTextProcessor {nid} has unexpected widget layout")
        n["widgets_values"][13] = int(seed)


def approved_prompt(workflow):
    override = node_by_id(workflow, 2632)
    return override.get("widgets_values_named", {}).get("positive", override["widgets_values"][0])


def patch_diagnostic_workflow(job, workflow_template, api_map, audio_slots, audio_choice, *, approved_prompt=None, prompt_only=False, setting_model):
    wf = json.loads(workflow_template.read_text(encoding="utf-8"))
    from .workflow_validation import validate_ui_workflow, lora_slots
    validate_ui_workflow(wf, patch_contract=True)
    ctx = job["context"]

    checkpoint = api_map["nodes"]["model_loader"]["alternatives"].get(ctx["model"])
    if not checkpoint:
        raise ValueError(f"Unknown model preset: {ctx['model']}")
    set_named_and_positional(node_by_id(wf, 4595), "unet_name", checkpoint)
    set_named_and_positional(node_by_id(wf, 2624), "positive", ctx["prompt"])
    set_named_and_positional(node_by_id(wf, 2632), "positive", approved_prompt or "")

    # Exact fixed-width reference context is bound to the batch and copied into every candidate.
    pictures = list(ctx.get("pictures", []))
    if len(pictures) != 6:
        raise RuntimeError("job context does not contain exactly 6 picture slots")
    for slot, filename in enumerate(pictures, start=1):
        nid = api_map["nodes"]["pictures"][slot - 1]["id"]
        set_named_and_positional(node_by_id(wf, nid), "image", filename)

    requested_audio = list(ctx.get("audio", []))
    for slot, filename in enumerate(audio_slots, start=1):
        nid = api_map["nodes"]["audio"][slot - 1]["id"]
        set_named_and_positional(node_by_id(wf, nid), "audio", filename)
    set_collect_refs_audio_mode(wf, audio_choice)
    set_primary_audio_reference_enabled(wf, bool(requested_audio))

    # Connected Seed (rgthree) nodes are authoritative; sampler widget values are only UI mirrors.
    render_seed = int(job["render_seed"])
    patch_rgthree_seed(wf, 1854, render_seed)
    patch_rgthree_seed(wf, 6067, render_seed + 1)
    for nid, seed in ((7334, render_seed), (7335, render_seed + 1)):
        n = node_by_id(wf, nid)
        n.setdefault("widgets_values_named", {})["noise_seed"] = seed
        if n.get("widgets_values"):
            n["widgets_values"][0] = seed

    # One analysis token per batch stabilizes Step 0-2 input text.
    patch_rgthree_seed(wf, 7360, int(job["analysis_seed"]))
    if prompt_only:
        patch_llm_pipeline_seeds(
            wf,
            int(job["analysis_seed"]),
            int(job["prompt_seed"]),
        )

    if ctx.get("loras"):
        n = node_by_id(wf, 6164)
        settings = [setting_model.model_validate(value) for value in ctx["loras"]]
        if len({setting.name for setting in settings}) != len(settings):
            raise ValueError("Duplicate LoRA settings")
        supported = [item["lora"] for item in api_map["nodes"]["first_pass_loras"]["entries"]]
        slots = lora_slots(wf, supported)
        for setting in settings:
            if setting.name not in slots:
                raise ValueError(f"Unsupported LoRA: {setting.name}")
            _, named = slots[setting.name]
            rows = [item for item in n["widgets_values"] if isinstance(item, dict) and item.get("lora") == setting.name]
            if len(rows) != 1:
                raise ValueError(f"Ambiguous positional LoRA slot: {setting.name}")
            for item in (named, rows[0]):
                item.update(on=setting.enabled, strength=setting.strength)
        lora_slots(wf, supported)

    validate_ui_workflow(wf, patch_contract=True)

    return wf
