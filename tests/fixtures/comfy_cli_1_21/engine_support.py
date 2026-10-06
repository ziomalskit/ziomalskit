"""Only constants and subgraph indexing needed by the exact converter.
Extracted verbatim from comfy-cli 1.21.0 engine.py.
"""

_FRONTEND_DOM_WIDGET_TYPES = frozenset(
    {"LOAD_3D", "LOAD_3D_ADVANCED", "PREVIEW_3D", "AUDIO_UI", "IMAGEUPLOAD", "AUDIOUPLOAD"}
)

_LOAD_3D_BUTTON_SLOTS: tuple[tuple[str, str], ...] = (
    ("upload 3d model", "upload3dmodel"),
    ("upload extra resources", "uploadExtraResources"),
    ("clear", "clear"),
)

LOAD_3D_BUTTON_VALUES = frozenset(value for _name, value in _LOAD_3D_BUTTON_SLOTS)

def _subgraph_defs_by_id(workflow: dict) -> dict[str, dict]:
    """Index subgraph definitions so an instance's ``type`` resolves to its def.

    A subgraph *instance* node's ``type`` is normally the UUID ``id`` of its
    definition, so the UUID is the primary key. Real ComfyUI saves can also
    carry several distinct defs sharing the cosmetic ``name`` "New Subgraph";
    keying by name alone would silently map instances onto the wrong def, so id
    always wins. We still register ``name`` as a *fallback* key (only when it
    doesn't shadow an id and isn't ambiguous across defs) to support older
    name-typed templates that predate UUID ids.
    """
    defs = (workflow.get("definitions") or {}).get("subgraphs") or []
    by_id: dict[str, dict] = {}
    name_counts: dict[str, int] = {}
    name_first: dict[str, dict] = {}
    for sg in defs:
        if not isinstance(sg, dict):
            continue
        sg_id = sg.get("id")
        if isinstance(sg_id, str) and sg_id:
            by_id[sg_id] = sg
        name = sg.get("name")
        if isinstance(name, str) and name:
            name_counts[name] = name_counts.get(name, 0) + 1
            name_first.setdefault(name, sg)
    for name, count in name_counts.items():
        if count == 1 and name not in by_id:
            by_id[name] = name_first[name]
    return by_id
