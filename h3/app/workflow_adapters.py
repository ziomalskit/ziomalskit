"""Fail-closed adapters for immutable, trusted repository workflows.

The browser supplies typed settings and a profile, never graph addresses.
Only this registry can select an adapter or its statically imported hook.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from .workflow_graph import all_nodes, instruction_snapshot, set_widget

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = {"h3_v20": "h3_v20.json"}
IDENTITY_FIELDS = ("workflow_id", "workflow_baseline_sha256", "workflow_adapter_id",
                   "workflow_adapter_schema_version", "workflow_adapter_identity")


class WorkflowAdapterCompatibilityError(ValueError):
    """An anchor, baseline or frozen adapter identity is incompatible."""


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _parts(link):
    return [link.get(key) for key in ("id", "origin_id", "origin_slot", "target_id", "target_slot", "type")] if isinstance(link, dict) else list(link[:6])


def _ports(node, side):
    keys = ("name", "type", "widget", "shape", "link") if side == "inputs" else ("name", "type", "shape", "links")
    return [{key: port[key] for key in keys if key in port} for port in node.get(side, [])]


class WorkflowAdapter:
    def __init__(self, descriptor: dict, root: Path = ROOT):
        self.descriptor = copy.deepcopy(descriptor)
        self.root = root
        if descriptor.get("schema_version") != 1 or descriptor.get("id") not in REGISTRY:
            self.fail("unregistered adapter or schema")
        from .adapters.v20 import V20Hooks
        # One trusted dispatch point. JSON cannot name/import Python code.
        self.hook = {"h3_v20": V20Hooks}[self.id](self)

    @property
    def id(self):
        return self.descriptor["id"]

    def fail(self, detail):
        raise WorkflowAdapterCompatibilityError(f"workflow-adapter {self.id if 'id' in self.descriptor else '?'} compatibility error: {detail}")

    def identity(self) -> dict:
        # Hash the descriptor AND the implementation closure. A hook/engine/
        # converter change cannot masquerade as the same persisted adapter.
        sources = ("workflow_adapters.py", "workflow_graph.py", "adapters/v20.py", "adapters/v20_api.py",
                   "production.py", "analysis_cache.py", "render_settings.py", "writer_policy.py", "temporal.py", "workflow_conversion.py", "workflow_validation.py")
        implementation = {name: hashlib.sha256((self.root / "app" / name).read_bytes()).hexdigest() for name in sources}
        return {"workflow_id": self.id, "workflow_baseline_sha256": self.descriptor["baseline_sha256"],
                "workflow_adapter_id": self.id, "workflow_adapter_schema_version": self.descriptor["schema_version"],
                "workflow_adapter_identity": canonical_hash({"descriptor": self.descriptor, "implementation": implementation})}

    def validate_job(self, context: dict, profile: dict) -> None:
        if profile.get("workflow_id") != self.id or any(context.get(key) != value for key, value in self.identity().items()):
            self.fail("persisted workflow/adapter identity changed or missing; explicit reconciliation required")
        settings = self.resolve_render_settings(context.get("render_settings"), frozen=True)
        if context.get("workflow_render_identity") != self.render_identity(context, settings):
            self.fail("persisted render settings identity differs; explicit reconciliation required")

    def render_identity(self, identity: dict, settings: dict) -> str:
        return canonical_hash({**{key: identity.get(key) for key in IDENTITY_FIELDS}, "render_settings": settings})

    def verify_baseline(self, path: Path | None = None) -> dict:
        path = path or self.root / "workflows" / self.descriptor["baseline"]
        body = path.read_bytes()
        if len(body) != self.descriptor["baseline_size_bytes"] or hashlib.sha256(body).hexdigest() != self.descriptor["baseline_sha256"]:
            self.fail("canonical v20 attachment size/SHA256 mismatch")
        return json.loads(body)

    def load_and_validate(self) -> dict:
        workflow = self.verify_baseline()
        self.validate_baseline(workflow)
        return workflow

    def scopes(self, workflow):
        return {"root": workflow, **{g["id"]: g for g in workflow.get("definitions", {}).get("subgraphs", [])}}

    def node(self, workflow, role):
        spec = self.descriptor["anchors"][role]
        graph = self.scopes(workflow).get(spec["graph"])
        matches = [] if graph is None else [node for node in graph.get("nodes", []) if node.get("id") == spec["node_id"]]
        if len(matches) != 1:
            self.fail(f"anchor {role}: expected exactly one node in {spec['graph']}")
        return matches[0]

    def validate_baseline(self, workflow: dict) -> None:
        """Validate every required anchor before any mutation, with no guesses."""
        scopes = self.scopes(workflow)
        for role, spec in self.descriptor["anchors"].items():
            node = self.node(workflow, role)
            if node.get("type") != spec["expected_type"] or node.get("mode", 0) != spec["mode"]:
                self.fail(f"anchor {role}: node type/mode changed")
            named = node.get("widgets_values_named")
            if (list(named) if isinstance(named, dict) else None) != spec["widget_names"]:
                self.fail(f"anchor {role}: expected widget layout missing or changed")
            if canonical_hash(named if named is not None else node.get("widgets_values")) != spec["widget_defaults_sha256"]:
                self.fail(f"anchor {role}: canonical widget defaults changed")
            if spec.get("lora_slots"):
                actual_rows = [value for value in node.get("widgets_values", []) if isinstance(value, dict) and "lora" in value]
                expected_rows = [value for key, value in named.items() if key.startswith("lora_")]
                if actual_rows != expected_rows:
                    self.fail(f"anchor {role}: named/positional LoRA rows differ")
            elif named is not None and list(named.values()) != node.get("widgets_values", [])[:len(named)]:
                self.fail(f"anchor {role}: named/positional widgets differ")
            for side in ("inputs", "outputs"):
                if _ports(node, side) != spec[side]:
                    self.fail(f"anchor {role}: expected {side} slots/links changed")
            for key, fields in spec.get("lora_slots", {}).items():
                if not isinstance(named.get(key), dict) or list(named[key]) != fields:
                    self.fail(f"anchor {role}: LoRA row structure changed")
        for required in self.descriptor["required_edges"]:
            graph = scopes.get(required["graph"], {})
            found = [_parts(link) for link in graph.get("links", []) if _parts(link)[0] == required["edge"][0]]
            if found != [required["edge"]]:
                self.fail(f"required graph edge {required['graph']}:{required['edge'][0]} changed")
        for graph_id, expected in self.descriptor["subgraph_interfaces"].items():
            graph = scopes.get(graph_id, {})
            actual = {side: [{key: port[key] for key in ("name", "type", "linkIds") if key in port}
                            for port in graph.get(side, [])] for side in ("inputs", "outputs")}
            if actual != expected:
                self.fail(f"subgraph interface {graph_id} changed")

    def set(self, workflow, role, widget, value):
        try:
            set_widget(self.node(workflow, role), widget, value)
        except (KeyError, IndexError, ValueError) as error:
            self.fail(f"anchor {role}.{widget}: {error}")

    def resolve_render_settings(self, values: dict | None = None, *, frozen=False) -> dict:
        controls = self.descriptor["render_controls"]
        if values is None:
            if frozen:
                self.fail("persisted render settings missing; explicit reconciliation required")
            values = {}
        if not isinstance(values, dict) or set(values) - set(controls) or frozen and set(values) != set(controls):
            raise ValueError("unknown or incomplete render settings")
        result = {}
        for key, control in controls.items():
            value = values.get(key, control["default"])
            kinds = (int,) if control["type"] == "integer" else (int, float)
            if (type(value) not in kinds or not control["minimum"] <= value <= control["maximum"] or
                    type(value) is float and not math.isfinite(value)):
                raise ValueError(f"invalid render setting {key}; expected {control['type']} in [{control['minimum']}, {control['maximum']}]")
            result[key] = value if control["type"] == "integer" else float(value)
        return result

    def render_capabilities(self) -> dict:
        workflow = self.load_and_validate()
        public = ("label", "type", "default", "minimum", "maximum", "step", "group", "help")
        for control in self.descriptor["render_controls"].values():
            if self.node(workflow, control["anchor"])["widgets_values_named"][control["widget"]] != control["default"]:
                self.fail("render control default differs from immutable baseline")
        return {"id": self.id, "label": self.descriptor["label"],
                "render_controls": {key: {field: value[field] for field in public}
                                    for key, value in self.descriptor["render_controls"].items()}}

    def apply_render_settings(self, workflow, values: dict):
        values = self.resolve_render_settings(values, frozen=True)
        for key, control in self.descriptor["render_controls"].items():
            self.set(workflow, control["anchor"], control["widget"], values[key])

    def apply_profile(self, workflow, profile):
        for field, (role, widget) in self.descriptor["profile_widgets"].items():
            self.set(workflow, role, widget, profile[field])
        for patch in self.descriptor["analysis_model_patches"]:
            self.set(workflow, patch["anchor"], patch["widget"], patch["value"])
        from .writer_policy import stage_routes, native_extra_args
        for stage, policy in stage_routes(profile).items():
            role = self.descriptor["writer_anchors"][stage]
            for field in ("model", "reasoning", "max_tokens", "ctx_size"):
                self.set(workflow, role, field, policy[field])
            self.set(workflow, role, "mmproj", "none")
            self.set(workflow, role, "extra_args", native_extra_args(policy))
        self.node(workflow, self.descriptor["writer_anchors"]["step4"])["type"] = "AJCompilerTextProcessor"

    def apply_duration(self, workflow, seconds):
        self.set(workflow, "duration", "value", seconds)

    def apply_references(self, workflow, pictures):
        roles = self.descriptor["reference_anchors"]
        if len(pictures) != len(roles):
            self.fail("expected exactly six picture slots")
        for role, filename in zip(roles, pictures):
            self.set(workflow, role, "image", filename)

    def apply_audio(self, workflow, slots, enabled):
        if len(slots) != len(self.descriptor["audio_anchors"]):
            self.fail("expected normalized audio slots")
        for role, filename in zip(self.descriptor["audio_anchors"], slots):
            self.set(workflow, role, "audio", filename)
        self.set(workflow, "collect_references", "choice_1", "LLM sees Audio 0" if enabled else "LLM does not see audio")
        self.set(workflow, "audio_enabled", "value_3", bool(enabled))

    def apply_loras(self, workflow, registry, settings):
        from .production import patch_lora_slots
        patch_lora_slots(self.node(workflow, "lora_stack"), registry, settings, slots=self.descriptor["lora_slots"])

    def compose(self, template: Path, profiles: dict, registry: dict, *, writer_stages=None) -> dict:
        descriptor = json.loads(template.read_text())
        profile_id = descriptor.get("profile")
        profile = profiles["profiles"].get(profile_id)
        if (descriptor.get("kind") != self.descriptor["template_kind"] or descriptor.get("schema_version") != 1 or
                profile is None or template.name != profile["render_template"] or profile.get("workflow_id") != self.id):
            self.fail("render template/profile identity mismatch")
        if (descriptor.get("baseline") != self.descriptor["baseline"] or descriptor.get("baseline_sha256") != self.descriptor["baseline_sha256"] or
                descriptor.get("lora_slots") != self.descriptor["lora_slots"]):
            self.fail("render template baseline/LoRA lock mismatch")
        workflow = self.load_and_validate()
        instructions = instruction_snapshot(workflow)
        runtime_profile = dict(profile, writer_stages=writer_stages) if writer_stages is not None else profile
        self.apply_profile(workflow, runtime_profile)
        from .production import resolve_loras
        self.apply_loras(workflow, registry, resolve_loras(registry, profile_id, None))
        self.hook.normalize_baseline(workflow)
        if instruction_snapshot(workflow) != instructions:
            self.fail("production composition modified immutable instructions")
        workflow.setdefault("extra", {})["aj_production"] = {"profile": profile_id,
            "baseline_sha256": self.descriptor["baseline_sha256"], **self.identity()}
        return workflow

    def compose_job(self, template, profiles, registry, job, audio_slots, approved_prompt, phase):
        context = job["context"]
        profile = profiles["profiles"][job["profile"]]
        self.validate_job(context, profile)
        workflow = self.compose(template, profiles, registry, writer_stages=context["profile_identity"]["writer_stages"])
        self.set(workflow, "user_prompt", "positive", context["prompt"])
        self.set(workflow, "prompt_override", "positive", approved_prompt or "")
        self.apply_references(workflow, context["pictures"])
        self.apply_audio(workflow, audio_slots, bool(context["audio"]))
        for source, mirror, offset in (("render_seed_first", "first_pass", 0), ("render_seed_second", "second_pass", 1)):
            seed = int(job["render_seed"]) + offset
            self.set(workflow, source, "seed", seed)
            self.set(workflow, mirror, "noise_seed", seed)
        self.set(workflow, "analysis_seed", "seed", int(job["analysis_seed"]))
        for binding in self.descriptor["writer_seed_bindings"]:
            seed = int(job[binding["seed"]]) + binding["offset"]
            self.set(workflow, "writer_seed_controller", binding["widget"], seed)
            self.set(workflow, binding["mirror"], "seed", seed)
        from .production import resolve_loras
        self.apply_loras(workflow, registry, resolve_loras(registry, job["profile"], context.get("loras"), frozen=True))
        self.apply_duration(workflow, context["duration_seconds"])
        bridge = context["conditioning_bridge"]
        self.set(workflow, "bridge", "enabled", bridge["enabled"])
        self.set(workflow, "bridge", "alpha", bridge["strength"])
        self.apply_render_settings(workflow, context["render_settings"])
        workflow["extra"]["aj_production"].update(job_id=job["id"], phase=phase)
        return workflow

    def approved_prompt(self, workflow):
        return self.node(workflow, "prompt_override")["widgets_values_named"]["positive"]

    def capture_contract(self):
        api = self.descriptor["api"]
        return {**{f"step{index}_{field}_title": api["captures"][key]
                   for index, field, key in ((0, "visual_facts", "visual_facts"), (1, "expanded_intent", "expanded_intent"),
                     (2, "reference_map", "reference_map"), (3, "creative_plan", "creative_plan"), (4, "final_prompt", "final_h3_prompt"))},
                "fallback_top_level_final_node_id": api["final_preview"]}

    def prepare_api_prompt(self, prompt, catalog, *, phase, approved_prompt=None):
        from .workflow_conversion import prepare_api_prompt
        result = prepare_api_prompt(prompt, catalog, phase=phase, approved_prompt=approved_prompt, contract=self.descriptor["api"])
        if phase == "render":
            for edge in self.descriptor["api"]["edges"]:
                if result.get(edge["target"], {}).get("inputs", {}).get(edge["input"]) != edge["source"]:
                    self.fail(f"converted graph edge {edge['target']}.{edge['input']} changed")
        return result

    def install_runtime_guards(self, prompt, catalog, context, *, phase, analysis_texts=None):
        return self.hook.install_runtime_guards(prompt, catalog, context, phase=phase, analysis_texts=analysis_texts)

    def validate_writer_graph(self, prompt, routes):
        from .writer_policy import validate_writer_graph
        validate_writer_graph(prompt, routes, contract=self.descriptor["api"])

    def validate_final_graph(self, prompt, context, *, phase):
        self.hook.validate_final_graph(prompt, context, phase=phase)


def get_adapter(workflow_id: str, *, root: Path = ROOT) -> WorkflowAdapter:
    if workflow_id not in REGISTRY:
        raise WorkflowAdapterCompatibilityError("unregistered workflow adapter; explicit reconciliation required")
    descriptor = json.loads((root / "workflow_adapters" / REGISTRY[workflow_id]).read_text())
    if descriptor.get("id") != workflow_id:
        raise WorkflowAdapterCompatibilityError("workflow-adapter descriptor identity mismatch")
    return WorkflowAdapter(descriptor, root)


def compose(template: Path, profiles: dict, registry: dict, *, writer_stages=None) -> dict:
    descriptor = json.loads(template.read_text())
    profile = profiles["profiles"].get(descriptor.get("profile"), {})
    return get_adapter(profile.get("workflow_id"), root=template.parent.parent).compose(
        template, profiles, registry, writer_stages=writer_stages)
