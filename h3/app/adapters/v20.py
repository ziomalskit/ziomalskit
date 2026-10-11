"""Trusted hooks for v20 transformations that require graph traversal."""
from __future__ import annotations

from ..workflow_graph import remove_passthrough
from ..temporal import FRAME_EXPRESSION, LENGTH_EXPRESSION
from ..workflow_conversion import _ancestors
from . import v20_api


class V20Hooks:
    def __init__(self, adapter):
        self.adapter = adapter

    def normalize_baseline(self, workflow):
        a = self.adapter
        for role, expression in (("frame_expression", FRAME_EXPRESSION), ("length_expression", LENGTH_EXPRESSION)):
            if a.node(workflow, role)["widgets_values_named"]["expression"] != expression:
                a.fail("canonical legal-duration expression changed")
        for role in ("duration_step3", "duration_step4"):
            a.set(workflow, role, "Constant", "duration_output")
        rewire = a.descriptor["duration_rewire"]
        graph = a.scopes(workflow)[rewire["graph"]]
        link = next(link for link in graph["links"] if link[0] == rewire["link_id"])
        link[2] = rewire["new_slot"]
        outputs = a.node(workflow, rewire["source"])["outputs"]
        outputs[rewire["old_slot"]]["links"].remove(rewire["link_id"])
        outputs[rewire["new_slot"]]["links"].append(rewire["link_id"])
        for graph in a.scopes(workflow).values():
            for node in list(graph["nodes"]):
                if node["type"] == "UnloadCPUModels":
                    remove_passthrough(graph, node["id"])
                elif node["type"] == "ComfyMathExpression" and node.get("widgets_values_named") is None:
                    if len(node.get("widgets_values", [])) != 1:
                        a.fail("unexpected duration expression layout")
                    node["widgets_values_named"] = {"expression": node["widgets_values"][0]}

    def install_runtime_guards(self, prompt, catalog, context, *, phase, analysis_texts=None):
        contract = self.adapter.descriptor["api"]
        prepared = v20_api.frozen_duration(prompt, context, catalog, contract=contract)
        if phase == "render":
            return v20_api.phase_boundaries(prepared, catalog, context["profile_identity"]["memory_policy"], contract=contract)
        if analysis_texts is not None:
            prepared = v20_api.reuse_analysis(prepared, analysis_texts, catalog, contract=contract)
        prepared = v20_api.temporal_guards(prepared, catalog, contract=contract)
        return prepared, None

    def validate_final_graph(self, prompt, context, *, phase):
        a = self.adapter
        contract = a.descriptor["api"]
        from ..writer_policy import stage_routes
        expected_duration = {"class_type": "AJFrozenDuration", "inputs": {
            "legal_frame_count": context["legal_frame_count"], "effective_duration_seconds": context["effective_duration_seconds"]}}
        if prompt.get("aj_frozen_duration") != expected_duration:
            a.fail("persisted duration differs from submitted graph")
        if phase == "prompt":
            a.validate_writer_graph(prompt, stage_routes(context["profile_identity"]))
            v20_api.validate_temporal_graph(prompt, context, contract=contract)
            return
        sink = contract["render_sink"]
        if prompt.get(sink, {}).get("class_type") != "SaveVideo" or prompt[sink]["inputs"].get("video") != [v20_api.VIDEO_BOUNDARY, 0]:
            a.fail("final video sink does not consume the release boundary")
        video = next(edge for edge in contract["edges"] if edge["target"] == sink)
        if prompt.get(v20_api.VIDEO_BOUNDARY, {}).get("inputs", {}).get("video") != video["source"]:
            a.fail("final video output path changed")
        for edge in contract["edges"]:
            if edge["target"] != sink and edge["target"] not in contract["guiders"] and prompt.get(edge["target"], {}).get("inputs", {}).get(edge["input"]) != edge["source"]:
                a.fail("sampling graph edge changed")
        for invariant in contract["fixed_inputs"]:
            if prompt.get(invariant["node"], {}).get("inputs", {}).get(invariant["input"]) != invariant["value"]:
                a.fail(f"canonical render parameter {invariant['node']}.{invariant['input']} changed")
        policy = context["profile_identity"]
        for role, field in (("renderer", "checkpoint"), ("encoder", "encoder"), ("video_vae", "video_vae"), ("audio_vae", "audio_vae")):
            widget = "unet_name" if role == "renderer" else "clip_name" if role == "encoder" else "vae_name"
            if prompt.get(contract[role], {}).get("inputs", {}).get(widget) != policy[field]:
                a.fail("converted render loader differs from persisted production profile")
        settings = a.resolve_render_settings(context["render_settings"], frozen=True)
        for key, control in a.descriptor["render_controls"].items():
            if (prompt.get(control["api_node"], {}).get("class_type") != control["api_type"] or
                    prompt.get(control["api_node"], {}).get("inputs", {}).get(control["api_input"]) != settings[key]):
                a.fail(f"converted render control {key} differs from frozen setting")
        dependencies = _ancestors(prompt, {sink})
        for key in (contract["renderer"], *contract["samplers"]):
            if key not in dependencies or v20_api.CONDITIONING_BOUNDARY not in _ancestors(prompt, {key}):
                a.fail("conditioning release must precede both sampling passes and transformer")
        boundary = prompt.get(v20_api.CONDITIONING_BOUNDARY, {})
        if (boundary.get("class_type") != "AJConditioningBoundary" or boundary.get("inputs", {}).get("release_encoder") is not True or
                prompt[contract["renderer"]].get("class_type") != "AJLateUNETLoader" or
                prompt[contract["renderer"]]["inputs"].get("after_conditioning") != [v20_api.CONDITIONING_BOUNDARY, 2] or
                prompt[v20_api.VIDEO_BOUNDARY]["inputs"].get("release_models") is not True):
            a.fail("frozen memory release policy changed in graph")
        for index, guider in enumerate(contract["guiders"]):
            if prompt[guider]["inputs"].get("conditioning") != [v20_api.CONDITIONING_BOUNDARY, index]:
                a.fail("guider bypasses conditioning release")
            original = next(edge for edge in contract["edges"] if edge["target"] == guider and edge["input"] == "conditioning")
            if boundary["inputs"].get("first" if index == 0 else "second") != original["source"]:
                a.fail("conditioning release source changed or stages swapped")
