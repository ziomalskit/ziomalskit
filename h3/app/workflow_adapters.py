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
from .production import immutable_registry, registry_identity

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
    def __init__(self, descriptor: dict, root: Path = ROOT, *, registry=None, bridge=None):
        self.descriptor = copy.deepcopy(descriptor)
        self.root = root
        self.registry = immutable_registry(registry) if registry is not None else None
        self.bridge = immutable_registry(bridge) if bridge is not None else None
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
        return self._implementation_identity(self.native_manifest())

    def _implementation_identity(self, native) -> dict:
        # Hash the descriptor AND the implementation closure. A hook/engine/
        # converter change cannot masquerade as the same persisted adapter.
        sources = ("workflow_adapters.py", "workflow_graph.py", "adapters/v20.py", "adapters/v20_api.py",
                   "production.py", "analysis_cache.py", "render_settings.py", "prompt_modes.py", "writer_policy.py", "temporal.py", "workflow_conversion.py", "workflow_validation.py",
                   "native_identity.py", "request_identity.py", "manual_timing.py", "main.py", "artifacts.py", "model_health.py")
        implementation = {name: hashlib.sha256((self.root / "app" / name).read_bytes()).hexdigest() for name in sources}
        return {"workflow_id": self.id, "workflow_baseline_sha256": self.descriptor["baseline_sha256"],
                "workflow_adapter_id": self.id, "workflow_adapter_schema_version": self.descriptor["schema_version"],
                "workflow_adapter_identity": canonical_hash({"descriptor": self.descriptor, "implementation": implementation,
                                                            "native": native})}

    def native_manifest(self):
        from .native_identity import native_manifest
        return native_manifest(self.root / "custom_nodes/aj_production")

    def verify_deployed_native(self, comfy_root, context):
        from .native_identity import verify_deployed_native
        native = self.native_manifest()
        # Bind the exact captured manifest to the frozen job before using it as
        # the filesystem authority. A second live source read cannot replace it.
        if not isinstance(context, dict) or any(context.get(key) != value for key, value in self._implementation_identity(native).items()):
            self.fail("native implementation changed from frozen job identity")
        verify_deployed_native(Path(comfy_root) / "custom_nodes/aj_production", native)

    def trusted_registry(self):
        if self.registry is None:
            self.fail("a validated controller LoRA registry snapshot is required")
        return self.registry

    def validate_job(self, context: dict, profile: dict) -> None:
        from .prompt_modes import validate_prompt_source
        validate_prompt_source(context)
        if profile.get("workflow_id") != self.id or any(context.get(key) != value for key, value in self.identity().items()):
            self.fail("persisted workflow/adapter identity changed or missing; explicit reconciliation required")
        if context.get("lora_registry_identity") != registry_identity(self.trusted_registry(), self.bridge):
            self.fail("persisted functional LoRA registry identity changed or missing; explicit reconciliation required")
        settings = self.resolve_render_settings(context.get("render_settings"), frozen=True, profile=context.get("profile"))
        if context.get("workflow_render_identity") != self.render_identity(context, settings):
            self.fail("persisted render settings identity differs; explicit reconciliation required")

    def render_identity(self, identity: dict, settings: dict) -> str:
        return canonical_hash({**{key: identity.get(key) for key in IDENTITY_FIELDS}, "render_settings": settings})

    def required_render_artifacts(self, context: dict, manifest: dict) -> list[dict]:
        """Verify trusted render dependencies without requiring unused LLM files.

        Only direct manual rendering uses this scope. Autoprompt retains the
        complete production-manifest gate. Missing/pending render dependencies
        never inherit a writer exception or a mutable filename default.
        """
        from .artifacts import validate_manifest
        entries = validate_manifest(manifest, allow_pending=True)
        bindings = self.descriptor["render_artifacts"]
        destinations = {directory + "/" + context["profile_identity"][field]
                        for field, directory in bindings["profile_fields"].items()}
        settings = self.resolve_render_settings(context["render_settings"], frozen=True, profile=context["profile"])
        for item in bindings["optional_controls"]:
            if settings[item["enabled_when"]]:
                node = self.node(self.load_and_validate(), item["anchor"])
                destinations.add(item["directory"] + "/" + node["widgets_values_named"][item["widget"]])
        if any(item.get("destination") in destinations for item in manifest.get("pending_artifacts", [])):
            self.fail("required render artifact still has pending provenance")
        selected = {item["destination"]: item for item in entries.values() if item["destination"] in destinations}
        if set(selected) != destinations or any(item["required"] is not True or not
                ({"shared", context["profile"]} & set(item["profiles"])) for item in selected.values()):
            self.fail("required render artifact identity/ownership is missing or changed")
        return list(selected.values())

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
            if spec.get("positional_defaults_sha256") and canonical_hash(node.get("widgets_values")) != spec["positional_defaults_sha256"]:
                self.fail(f"anchor {role}: canonical positional widgets changed")
            if node["type"] == "Power Lora Loader (rgthree)":
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

    def resolve_render_settings(self, values: dict | None = None, *, frozen=False, profile=None) -> dict:
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
            if control["type"] == "boolean":
                if type(value) is not bool:
                    raise ValueError(f"invalid render setting {key}; expected JSON boolean")
                result[key] = value
                continue
            if control["type"] == "lora_stack":
                from .render_settings import SecondPassLora
                from .production import resolve_loras
                if type(value) is not list or frozen and any(
                        not isinstance(row, dict) or set(row) != {"id", "enabled", "strength"} for row in value):
                    raise ValueError("invalid or incomplete second-pass LoRA settings")
                rows = [SecondPassLora.model_validate(row).model_dump() for row in value]
                result[key] = resolve_loras(self.trusted_registry(), profile,
                    rows, frozen=frozen, ids_only=True, supplied_only=True) if rows else []
                if sum(row["enabled"] for row in result[key]) > self.descriptor["lora_slots"]:
                    raise ValueError("LoRA slot capacity exceeded")
                continue
            kinds = (int,) if control["type"] == "integer" else (int, float)
            if (type(value) not in kinds or not control["minimum"] <= value <= control["maximum"] or
                    type(value) is float and not math.isfinite(value)):
                raise ValueError(f"invalid render setting {key}; expected {control['type']} in [{control['minimum']}, {control['maximum']}]")
            result[key] = value if control["type"] == "integer" else float(value)
        for key, control in controls.items():
            source = control.get("minimum_from")
            if source and self.control_active(key, result) and result[key] < result[source]:
                raise ValueError(f"{control['label']} must be at least {controls[source]['label']} while {controls[control['enabled_when']]['label']} is ON")
        return result

    def render_capabilities(self) -> dict:
        workflow = self.load_and_validate()
        public = ("label", "type", "default", "minimum", "maximum", "step", "group", "help", "enabled_when", "default_strength", "minimum_from")
        for control in self.descriptor["render_controls"].values():
            if "widget" in control and self.node(workflow, control["anchor"])["widgets_values_named"][control["widget"]] != control.get("baseline_default", control["default"]):
                self.fail("render control default differs from immutable baseline")
            for role, modes in control.get("mode_anchors", {}).items():
                if self.node(workflow, role).get("mode", 0) != modes["off"]:
                    self.fail("render toggle baseline mode differs")
            if control["type"] == "lora_stack" and self.node(workflow, control["anchor"])["widgets_values_named"] != control["baseline_widgets"]:
                self.fail("render LoRA baseline differs")
            if control["type"] == "lora_stack" and self.node(workflow, control["anchor"])["widgets_values"] != control["baseline_values"]:
                self.fail("render LoRA positional baseline differs")
        return {"id": self.id, "label": self.descriptor["label"],
                "render_controls": {key: {field: value[field] for field in public if field in value}
                                    for key, value in self.descriptor["render_controls"].items()}}

    def control_active(self, key, values):
        control = self.descriptor["render_controls"][key]
        return not control.get("enabled_when") or values[control["enabled_when"]]

    def active_render_loras(self, context):
        """Supply enabled adapter stacks to the existing provenance/file gate."""
        values = self.resolve_render_settings(context["render_settings"], frozen=True, profile=context["profile"])
        return [row for key, control in self.descriptor["render_controls"].items()
                if control["type"] == "lora_stack" and self.control_active(key, values)
                for row in values[key] if row["enabled"]]

    def apply_render_settings(self, workflow, values: dict, *, profile=None):
        values = self.resolve_render_settings(values, frozen=True, profile=profile)
        for key, control in self.descriptor["render_controls"].items():
            for role, modes in control.get("mode_anchors", {}).items():
                self.node(workflow, role)["mode"] = modes["on" if values[key] else "off"]
            if "widget" in control:
                value = values[key] if self.control_active(key, values) else control.get("baseline_default", values[key])
                self.set(workflow, control["anchor"], control["widget"], value)
            if control["type"] == "lora_stack":
                from .production import patch_lora_slots
                node = self.node(workflow, control["anchor"])
                active = self.control_active(key, values) and any(row["enabled"] for row in values[key])
                node["mode"] = control["on_mode"] if active else control["off_mode"]
                if active:
                    patch_lora_slots(node, self.trusted_registry(), values[key], slots=self.descriptor["lora_slots"])
                else:
                    node["widgets_values_named"] = copy.deepcopy(control["baseline_widgets"])
                    node["widgets_values"] = copy.deepcopy(control["baseline_values"])

    def api_edges(self, settings):
        for edge in self.descriptor["api"]["edges"]:
            source = edge["source"]
            if "enabled_when" in edge and settings[edge["enabled_when"]]:
                source = edge["enabled_source"]
            if "lora_stack_when" in edge:
                key = edge["lora_stack_when"]
                if self.control_active(key, settings) and any(row["enabled"] for row in settings[key]):
                    source = edge["enabled_source"]
            yield {**edge, "source": source}

    def apply_profile(self, workflow, profile):
        for field, (role, widget) in self.descriptor["profile_widgets"].items():
            self.set(workflow, role, widget, profile[field])
        for patch in self.descriptor["analysis_model_patches"]:
            self.set(workflow, patch["anchor"], patch["widget"], patch["value"])
        # Step 1/2 still consume the canonical image and text inputs. Mistral
        # Small 3.2 requires its own matching vision projector, not Gemma's.
        self.set(workflow, "analysis_source_2640", "model", profile["analysis_model"])
        self.set(workflow, "analysis_source_2640", "mmproj", profile["analysis_mmproj"])
        for role in ("writer_step1", "writer_step2"):
            self.set(workflow, role, "model", profile["analysis_model"])
            self.set(workflow, role, "mmproj", profile["analysis_mmproj"])
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
        if self.registry is not None:
            if registry_identity(registry) != registry_identity(self.registry):
                self.fail("composition registry differs from trusted controller snapshot")
            registry = self.registry
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
        from .prompt_modes import is_manual_final, validate_render_prompt
        context = job["context"]
        profile = profiles["profiles"][job["profile"]]
        self.validate_job(context, profile)
        manual = is_manual_final(job)
        if manual:
            if phase != "render":
                self.fail("manual final jobs cannot use Prompt Worker")
            validate_render_prompt(job, approved_prompt)
        workflow = self.compose(template, profiles, registry, writer_stages=context["profile_identity"]["writer_stages"])
        self.set(workflow, "user_prompt", "positive", context["prompt"])
        self.set(workflow, "prompt_override", "positive", approved_prompt or "")
        self.apply_references(workflow, context["pictures"])
        self.apply_audio(workflow, audio_slots, bool(context["audio"]))
        for source, mirror, offset in (("render_seed_first", "first_pass", 0), ("render_seed_second", "second_pass", 1)):
            seed = int(job["render_seed"]) + offset
            self.set(workflow, source, "seed", seed)
            self.set(workflow, mirror, "noise_seed", seed)
        if not manual:
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
        self.apply_render_settings(workflow, context["render_settings"], profile=job["profile"])
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

    def prepare_api_prompt(self, prompt, catalog, *, phase, approved_prompt=None, context=None):
        from .prompt_modes import validate_prompt_source
        if context is not None and context.get("prompt_mode") == "manual_final":
            validate_prompt_source(context)
            if phase != "render" or approved_prompt != context["manual_final_prompt"]:
                self.fail("manual final graph phase/text differs from frozen source")
        from .workflow_conversion import prepare_api_prompt
        result = prepare_api_prompt(prompt, catalog, phase=phase, approved_prompt=approved_prompt, contract=self.descriptor["api"])
        if phase == "render":
            settings = self.resolve_render_settings(context["render_settings"], frozen=True, profile=context.get("profile")) if context is not None else self.resolve_render_settings()
            for edge in self.api_edges(settings):
                if result.get(edge["target"], {}).get("inputs", {}).get(edge["input"]) != edge["source"]:
                    self.fail(f"converted graph edge {edge['target']}.{edge['input']} changed")
            self.hook.validate_render_routes(result, settings, catalog)
        return result

    def install_runtime_guards(self, prompt, catalog, context, *, phase, analysis_texts=None):
        return self.hook.install_runtime_guards(prompt, catalog, context, phase=phase, analysis_texts=analysis_texts)

    def validate_writer_graph(self, prompt, routes):
        from .writer_policy import validate_writer_graph
        validate_writer_graph(prompt, routes, contract=self.descriptor["api"])

    def validate_final_graph(self, prompt, context, *, phase):
        self.hook.validate_final_graph(prompt, context, phase=phase)


def get_adapter(workflow_id: str, *, root: Path = ROOT, registry=None, bridge=None) -> WorkflowAdapter:
    if workflow_id not in REGISTRY:
        raise WorkflowAdapterCompatibilityError("unregistered workflow adapter; explicit reconciliation required")
    descriptor = json.loads((root / "workflow_adapters" / REGISTRY[workflow_id]).read_text())
    if descriptor.get("id") != workflow_id:
        raise WorkflowAdapterCompatibilityError("workflow-adapter descriptor identity mismatch")
    return WorkflowAdapter(descriptor, root, registry=registry, bridge=bridge)


def compose(template: Path, profiles: dict, registry: dict, *, writer_stages=None) -> dict:
    descriptor = json.loads(template.read_text())
    profile = profiles["profiles"].get(descriptor.get("profile"), {})
    return get_adapter(profile.get("workflow_id"), root=template.parent.parent, registry=registry).compose(
        template, profiles, registry, writer_stages=writer_stages)
