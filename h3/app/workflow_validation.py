"""Fail closed before a UI graph loses identity or widget layout information."""
from __future__ import annotations

import json

from .workflow_conversion import WorkflowPreparationError


# The controller patches these exact interfaces, including subgraph instances.
# Matching a similarly named node or an unrelated class is never sufficient.
PATCH_NODE_TYPES = {
    4595: "3a3c555b-0f97-43fb-a8ca-cc713f8a6e3e",
    2249: "6429b9f0-c968-4a09-a1b9-7a98388f1e7a",
    3423: "e32d0da3-0ead-4f8a-b864-a0802bf626aa",
    7334: "62fc1cf4-4ac3-413d-bafc-12558c24914e",
    7335: "f9092623-6d48-4b5a-b478-6b65bfaf31eb",
    4022: "add39ab2-8579-4aa1-b8fe-3245ef2c8eec",
    **{nid: "Seed (rgthree)" for nid in (1854, 6067, 7360)},
    **{nid: "easy positive" for nid in (2624, 2632)},
    **{nid: "LoadImage" for nid in (3554, 4613, 4624, 4635, 4646, 4657)},
    **{nid: "LoadAudio" for nid in (3944, 4734, 4749)},
    **{nid: "LLMTextProcessor" for nid in (2441, 2447, 2445, 4275)},
    6164: "Power Lora Loader (rgthree)",
}
DECORATIONS = {"Note", "MarkdownNote", "Label (rgthree)", "Fast Groups Bypasser (rgthree)"}
VIRTUAL_CLASSES = {"SetNode", "GetNode", "PrimitiveNode", "Reroute"}
PATCH_FIELDS = {
    4595: ("unet_name", "clip_name", "vae_name", "vae_name_1"),
    2249: ("value", "choice", "choice_1", "f_string"),
    3423: ("value_3", "value", "value_1", "value_2"),
    4022: ("value_5", "value", "value_1", "value_2", "value_3", "value_4",
           "value_1_1", "value_2_1", "value_3_1", "value_4_1", "value_1_2",
           "value_2_2", "value_3_2", "value_4_2", "value_1_3", "value_2_3", "value_3_3", "value_4_3"),
    **{nid: ("noise_seed", "sampler_name", "scheduler", "steps", "denoise") for nid in (7334, 7335)},
    **{nid: ("seed", "🎲 Randomize Each Time", "🎲 New Fixed Random", "USE_LAST_SEED") for nid in (1854, 6067, 7360)},
    **{nid: ("positive",) for nid in (2624, 2632)},
    **{nid: ("image", "upload") for nid in (3554, 4613, 4624, 4635, 4646, 4657)},
    **{nid: ("audio", "upload") for nid in (3944, 4734, 4749)},
}


def graph_scopes(document):
    yield document
    definitions = document.get("definitions", {})
    if not isinstance(definitions, dict) or not isinstance(definitions.get("subgraphs", []), list):
        raise WorkflowPreparationError("Malformed UI subgraph definitions")
    for graph in definitions.get("subgraphs", []):
        if not isinstance(graph, dict):
            raise WorkflowPreparationError("Malformed UI subgraph")
        yield from graph_scopes(graph)


def _widget_names(schema, saved):
    result = []
    inputs = schema.get("input", {})
    ordering = schema.get("input_order", {})
    if not isinstance(inputs, dict) or not isinstance(ordering, dict):
        raise WorkflowPreparationError("Malformed catalog input layout")
    for section in ("required", "optional"):
        fields = inputs.get(section, {})
        if not isinstance(fields, dict):
            raise WorkflowPreparationError("Malformed catalog input section")
        listed = ordering.get(section, [])
        if not isinstance(listed, list) or len(listed) != len(set(listed)) or any(name not in fields for name in listed):
            raise WorkflowPreparationError("Ambiguous catalog input ordering")
        for name in listed + [name for name in fields if name not in listed]:
            spec = fields[name]
            if not isinstance(spec, (list, tuple)) or not spec:
                raise WorkflowPreparationError(f"Malformed input specification: {name}")
            kind = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if options.get("forceInput") or options.get("defaultInput"):
                continue
            if isinstance(kind, str) and kind.startswith("COMFY_") and "COMBO" in kind:
                result.append(name)
                options_list = options.get("options")
                if not isinstance(options_list, list):
                    raise WorkflowPreparationError("Missing dynamic widget options")
                matches = [option for option in options_list if isinstance(option, dict) and option.get("key") == saved.get(name)]
                if len(matches) != 1 or not isinstance(matches[0].get("inputs"), dict):
                    raise WorkflowPreparationError("Unknown or ambiguous dynamic widget selection")
                nested_saved = {key[len(name) + 1:]: value for key, value in saved.items() if key.startswith(name + ".")}
                result.extend(name + "." + key for key in _widget_names({"input": matches[0]["inputs"]}, nested_saved))
                continue
            if isinstance(kind, (list, tuple)) or kind in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO") or options.get("widgetType") or options.get("socketless"):
                result.append(name)
    return result


def _validate_widgets(node, catalog):
    named = node.get("widgets_values_named")
    values = node.get("widgets_values")
    label = f"{node['id']} ({node['type']})"
    if named is None and values in (None, []):
        if catalog is not None and node["type"] in catalog and _widget_names(catalog[node["type"]], {}):
            raise WorkflowPreparationError(f"Missing saved widgets at {label}")
        return
    if not isinstance(named, dict) or not isinstance(values, (list, dict)):
        raise WorkflowPreparationError(f"Missing self-describing widget layout at {label}")
    if isinstance(values, dict):
        ui_only = {"preview", "videopreview", "choose video to upload"}
        if {key: value for key, value in values.items() if key not in ui_only} != {key: value for key, value in named.items() if key not in ui_only}:
            raise WorkflowPreparationError(f"Conflicting widget storage at {label}")
    elif node["type"] == "Power Lora Loader (rgthree)":
        # rgthree serializes divider/button widgets in addition to LoRA rows.
        rows = [value for value in values if isinstance(value, dict) and "lora" in value]
        expected = [value for key, value in named.items() if key.startswith("lora_")]
        if rows != expected:
            raise WorkflowPreparationError(f"Conflicting LoRA widget storage at {label}")
    else:
        expected = list(named.values())
        if values[:len(expected)] != expected:
            raise WorkflowPreparationError(f"Conflicting or missing widget storage at {label}")
        # LoadAudio has two frontend upload placeholders. No other arbitrary
        # extra serialized value is allowed to alter positional alignment.
        extra = values[len(expected):]
        if extra and not (node["type"] == "LoadAudio" and all(value is None for value in extra)):
            raise WorkflowPreparationError(f"Unknown extra widget slots at {label}")
    if catalog is not None and node["type"] in catalog and node["type"] != "Power Lora Loader (rgthree)":
        names = _widget_names(catalog[node["type"]], named)
        # CustomCombo serializes a variable count of option rows. Only omitted
        # trailing empty rows are harmless; selected dynamic branch fields are
        # always required and never inferred from another branch or defaults.
        if node["type"] == "CustomCombo":
            required = catalog[node["type"]].get("input", {}).get("required", {})
            names = [name for name in names if name in named or not (
                name.startswith("option") and required.get(name, [None, {}])[1].get("default") == ""
            )]
        saved = [name for name in named if name in names]
        if saved != names:
            raise WorkflowPreparationError(f"Incompatible catalog widget layout at {label}: {names!r} != {saved!r}")


def validate_ui_workflow(document, catalog=None, *, patch_contract=False):
    if not isinstance(document, dict):
        raise WorkflowPreparationError("UI workflow must be an object")
    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise WorkflowPreparationError("UI workflow contains invalid JSON values") from error
    scopes = list(graph_scopes(document))
    definition_ids = [scope.get("id") for scope in scopes[1:]]
    if any(not isinstance(value, str) or not value for value in definition_ids) or len(set(definition_ids)) != len(definition_ids):
        raise WorkflowPreparationError("Duplicate or missing subgraph definition identity")
    found = {nid: [] for nid in PATCH_NODE_TYPES}
    for scope in scopes:
        nodes = scope.get("nodes")
        if not isinstance(nodes, list):
            raise WorkflowPreparationError("UI graph nodes must be an array")
        ids = set()
        for node in nodes:
            if not isinstance(node, dict) or type(node.get("id")) is not int or not isinstance(node.get("type"), str):
                raise WorkflowPreparationError("Malformed UI node identity/type")
            if node["id"] in ids:
                raise WorkflowPreparationError(f"Duplicate UI node ID: {node['id']}")
            ids.add(node["id"])
            if node["id"] in found:
                found[node["id"]].append(node)
            if node["type"] in DECORATIONS:
                continue
            if catalog is not None and node["type"] not in definition_ids and node["type"] not in VIRTUAL_CLASSES and node["type"] not in catalog:
                raise WorkflowPreparationError(f"Unregistered UI node class: {node['type']}")
            _validate_widgets(node, catalog)
        link_ids = []
        for link in scope.get("links", []):
            value = link.get("id") if isinstance(link, dict) else link[0] if isinstance(link, list) and link else None
            if type(value) is not int:
                raise WorkflowPreparationError("Malformed UI link identity")
            link_ids.append(value)
        if len(set(link_ids)) != len(link_ids):
            raise WorkflowPreparationError("Duplicate UI link identity")
    if patch_contract:
        for nid, kind in PATCH_NODE_TYPES.items():
            if nid == 4275 and document.get("extra", {}).get("aj_production"):
                kind = "AJCompilerTextProcessor"
            matches = found[nid]
            if len(matches) != 1 or matches[0]["type"] != kind:
                raise WorkflowPreparationError(f"Expected exactly one {kind} node at {nid}")
            fields = PATCH_FIELDS.get(nid)
            if fields and tuple(matches[0].get("widgets_values_named", {})) != fields:
                raise WorkflowPreparationError(f"Unexpected patch widget fields/order at {nid}")


def validate_converted_widgets(document, prompt, catalog):
    """Check the converter actually consumed the saved, unlinked widget values.

    Its catalog fetch is independent of ours. A catalog change or dropped widget
    cannot be rescued by otherwise valid defaults in the converted API graph.
    """
    definitions = {graph["id"]: graph for graph in graph_scopes(document) if graph is not document}

    def visit(graph, prefix="", active=()):
        for node in graph["nodes"]:
            key = prefix + str(node["id"])
            kind = node["type"]
            if kind in definitions:
                if kind in active:
                    raise WorkflowPreparationError("Recursive subgraph instance")
                visit(definitions[kind], key + ":", (*active, kind))
                continue
            if kind in DECORATIONS or kind in VIRTUAL_CLASSES or key not in prompt:
                continue
            converted = prompt[key]
            if converted.get("class_type") != kind:
                raise WorkflowPreparationError(f"Converted node type changed at {key}")
            named = node.get("widgets_values_named", {})
            linked = {item["name"] for item in node.get("inputs", []) if item.get("link") is not None}
            if kind == "Power Lora Loader (rgthree)":
                expected = {name: {k: v for k, v in value.items() if k != "strengthTwo" or v is not None}
                            for name, value in named.items() if name.startswith("lora_")}
            else:
                expected = {name: named[name] for name in _widget_names(catalog[kind], named) if name in named and name not in linked}
            for name, value in expected.items():
                if name not in converted.get("inputs", {}) or converted["inputs"][name] != value:
                    raise WorkflowPreparationError(f"Converter changed or dropped saved widget {key}.{name}")
    visit(document)


def lora_slots(document, expected_names):
    validate_ui_workflow(document, patch_contract=True)
    matches = [node for node in document["nodes"] if node["id"] == 6164]
    node = matches[0]
    named = node["widgets_values_named"]
    slots = {}
    for key, value in named.items():
        if not key.startswith("lora_"):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("lora"), str) or value["lora"] in slots:
            raise WorkflowPreparationError("Ambiguous LoRA slot identity")
        slots[value["lora"]] = (key, value)
    if set(slots) != set(expected_names):
        raise WorkflowPreparationError("LoRA template slots differ from the supported manifest")
    return slots
