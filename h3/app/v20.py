"""Compose production runtime graphs from the exact, immutable v20 attachment.

The render templates are small composition descriptors. They deliberately do
not contain copies of the Step 0–4 instructions. The CLI receives a fully
materialized UI graph; the original attachment is never rewritten.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

BASELINE_FILE = "H3_v20_heretic_MASTER_DURATION_EXACT.json"
BASELINE_BYTES = 3084071
BASELINE_SHA256 = "276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222"


def verified_baseline(path: Path) -> dict:
    body = path.read_bytes()
    if len(body) != BASELINE_BYTES or hashlib.sha256(body).hexdigest() != BASELINE_SHA256:
        raise ValueError("canonical v20 attachment size/SHA256 mismatch")
    return json.loads(body)


def all_nodes(document):
    for graph in [document, *document.get("definitions", {}).get("subgraphs", [])]:
        yield from graph.get("nodes", [])


def instruction_snapshot(document: dict) -> dict:
    """All saved instruction strings, including upstream prompt builders.

    Model filenames, UI titles, seed values and loader widgets are intentionally
    outside this lock; their production changes cannot alter prompt prose.
    """
    fields = {"system_prompt", "prompt", "f_string", "text", "string"}
    return {(graph.get("id"), node["id"], field): value
            for graph in [document, *document.get("definitions", {}).get("subgraphs", [])]
            for node in graph.get("nodes", [])
            for field, value in node.get("widgets_values_named", {}).items()
            if isinstance(value, str) and (field in fields or
                field == "value" and node.get("type") in {"PrimitiveString", "PrimitiveStringMultiline"})}


def set_widget(node: dict, name: str, value) -> None:
    named = node.get("widgets_values_named")
    if not isinstance(named, dict) or name not in named or not isinstance(node.get("widgets_values"), list):
        raise ValueError("missing self-describing production widget")
    node["widgets_values"][list(named).index(name)] = value
    named[name] = value


def remove_passthrough(graph: dict, node_id: int) -> None:
    """Remove a legacy unload node while preserving its dependency edge."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    target = nodes[node_id]
    links = graph["links"]
    def parts(link):
        return (link["id"], link["origin_id"], link["origin_slot"], link["target_id"], link["target_slot"]) if isinstance(link, dict) else tuple(link[:5])
    inbound = [link for link in links if parts(link)[3] == node_id]
    if len(inbound) != 1:
        raise ValueError("unload passthrough input is ambiguous")
    old_id, source, source_slot, _, _ = parts(inbound[0])
    outbound = [link for link in links if parts(link)[1] == node_id]
    output = nodes[source]["outputs"][source_slot]
    output["links"] = [lid for lid in output.get("links", []) if lid != old_id]
    for link in outbound:
        lid = parts(link)[0]
        if isinstance(link, dict):
            link.update(origin_id=source, origin_slot=source_slot)
        else:
            link[1], link[2] = source, source_slot
        output["links"].append(lid)
    graph["links"] = [link for link in links if parts(link)[0] != old_id]
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != node_id]


def compose(template: Path, profiles: dict, registry: dict, *, writer_stages: dict | None = None) -> dict:
    descriptor = json.loads(template.read_text())
    if descriptor.get("kind") != "v20_render_template" or descriptor.get("schema_version") != 1:
        raise ValueError("invalid production render template")
    profile_id = descriptor.get("profile")
    profile = profiles["profiles"].get(profile_id)
    if profile is None or template.name != profile["render_template"]:
        raise ValueError("render template/profile identity mismatch")
    if descriptor.get("baseline") != BASELINE_FILE or descriptor.get("baseline_sha256") != BASELINE_SHA256 or descriptor.get("lora_slots", 0) < 8:
        raise ValueError("render template v20 baseline lock mismatch")
    workflow = verified_baseline(template.parent / BASELINE_FILE)
    from .writer_policy import stage_routes, native_extra_args
    runtime_profile = dict(profile, writer_stages=writer_stages) if writer_stages is not None else profile
    routes = stage_routes(runtime_profile)
    instructions = instruction_snapshot(workflow)
    nodes = {node["id"]: node for node in all_nodes(workflow)}
    from .temporal import FRAME_EXPRESSION, LENGTH_EXPRESSION
    if nodes[134]["widgets_values_named"]["expression"] != FRAME_EXPRESSION or nodes[137]["widgets_values_named"]["expression"] != LENGTH_EXPRESSION:
        raise ValueError("canonical v20 legal-duration expression changed")
    # v20 output 1 is the frame-derived duration_length, aliased duration_output.
    # Correct the raw-target wiring without changing any instruction prose.
    for nid in (5366, 7454):
        set_widget(nodes[nid], "Constant", "duration_output")
    master_link = next(link for link in workflow["links"] if link[0] == 46933)
    if master_link[1:5] != [1731, 2, 7453, 1]:
        raise ValueError("v20 master duration wiring changed")
    master_link[2] = 1
    nodes[1731]["outputs"][2]["links"].remove(46933)
    nodes[1731]["outputs"][1]["links"].append(46933)
    loader = nodes[4595]
    for field, value in (("unet_name", profile["checkpoint"]), ("clip_name", profile["encoder"]),
                         ("vae_name", profile["video_vae"]), ("vae_name_1", profile["audio_vae"])):
        set_widget(loader, field, value)
    # Step 0 forensic prompts/tiles and Step 1–2 prompts remain untouched.
    for node in nodes.values():
        if node["type"] != "LLMTextProcessor":
            continue
        if node["id"] in (2445, 4275):
            policy = routes["step3" if node["id"] == 2445 else "step4"]
            set_widget(node, "model", policy["model"])
            set_widget(node, "mmproj", "none")
            for field in ("reasoning", "max_tokens", "ctx_size"):
                set_widget(node, field, policy[field])
            set_widget(node, "extra_args", native_extra_args(policy))
            if node["id"] == 4275:
                node["type"] = "AJCompilerTextProcessor"
        else:
            name = node["widgets_values_named"]["model"]
            if "Joycaption" in name:
                set_widget(node, "model", "Llama-Joycaption-Beta-One-Hf-Llava-F16.gguf")
                set_widget(node, "mmproj", "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf")
            elif "Qwen3VL" in name or "Qwen3-VL" in name:
                set_widget(node, "model", "Qwen3-VL-8B-NSFW-Caption-V4.5.f16.gguf")
                set_widget(node, "mmproj", "Qwen3-VL-8B-NSFW-Caption-V4.5.mmproj-f16.gguf")
            elif "Gemma-4-E4B" in name:
                set_widget(node, "model", "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf")
    # Connected model sockets are authoritative. Patch their source widgets as
    # well as mirrors, retaining original prompt strings and sampling values.
    for node in nodes.values():
        named = node.get("widgets_values_named", {})
        for key, value in list(named.items()):
            if not isinstance(value, str) or key in {"system_prompt", "prompt", "f_string", "text", "string"}:
                continue
            if value == "Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf":
                set_widget(node, key, "Llama-Joycaption-Beta-One-Hf-Llava-F16.gguf")
            elif value == "Qwen3VL-8B-Instruct-Q4_K_M.gguf":
                set_widget(node, key, "Qwen3-VL-8B-NSFW-Caption-V4.5.f16.gguf")
            elif value == "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf":
                set_widget(node, key, "Qwen3-VL-8B-NSFW-Caption-V4.5.mmproj-f16.gguf")
            elif value == "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.i1-Q6_K.gguf":
                set_widget(node, key, "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf")
    from .production import patch_lora_slots, resolve_loras
    patch_lora_slots(nodes[6164], registry, resolve_loras(registry, profile_id, None), slots=descriptor["lora_slots"])
    # These legacy CPU-GC nodes do not implement phase-boundary encoder release.
    # The production API adapter installs explicit dependencies instead.
    for graph in [workflow, *workflow["definitions"]["subgraphs"]]:
        for node in list(graph["nodes"]):
            if node["type"] == "UnloadCPUModels":
                remove_passthrough(graph, node["id"])
            elif node["type"] == "ComfyMathExpression" and node.get("widgets_values_named") is None:
                # Pinned ComfyUI nodes_math.py defines one expression widget.
                # Add its name for API conversion; retain the exact expression.
                if len(node.get("widgets_values", [])) != 1:
                    raise ValueError("unexpected v20 duration expression layout")
                node["widgets_values_named"] = {"expression": node["widgets_values"][0]}
    if instruction_snapshot(workflow) != instructions:
        raise ValueError("production composition modified v20 instructions")
    workflow.setdefault("extra", {})["aj_production"] = {"profile": profile_id, "baseline_sha256": BASELINE_SHA256}
    return workflow
