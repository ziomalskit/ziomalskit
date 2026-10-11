"""Pure graph helpers shared by trusted repository adapters."""
from __future__ import annotations

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
