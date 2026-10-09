"""Pinned converter exercises both full render routes and durable analysis."""
import copy
from pathlib import Path
import unittest

from h3.app.production import read_profiles, read_loras
from h3.app.production_api import phase_boundaries, reuse_analysis, event_phase
from h3.app.v20 import compose, set_widget
from h3.app.workflow_conversion import prepare_api_prompt, _ancestors, WorkflowPreparationError
from h3.app.workflow_validation import validate_ui_workflow, validate_converted_widgets
from tests.step3_helpers import ADAPTER_CATALOG
from tests.test_workflows import exact_convert

ROOT = Path(__file__).resolve().parents[1] / "h3"


def production_catalog():
    """CPU schemas for pinned core classes and the AJ-owned node contracts."""
    catalog = copy.deepcopy(ADAPTER_CATALOG)
    for name, fields, outputs in (
        ("GetImageSizeAndCount", {"image": ["IMAGE", {}]}, ["IMAGE", "INT", "INT", "INT"]),
        ("ImageBatch", {"image1": ["IMAGE", {}], "image2": ["IMAGE", {}]}, ["IMAGE"]),
        ("RepeatImageBatch", {"image": ["IMAGE", {}], "amount": ["INT", {}]}, ["IMAGE"]),
        ("AJAnalysisText", {"text": ["STRING", {"multiline": True}]}, ["STRING"]),
        ("AJConditioningBoundary", {"first": ["CONDITIONING", {}], "second": ["CONDITIONING", {}],
                                   "release_encoder": ["BOOLEAN", {}]}, ["CONDITIONING", "CONDITIONING", "INT"]),
        ("AJVideoBoundary", {"video": ["VIDEO", {}], "release_models": ["BOOLEAN", {}]}, ["VIDEO"]),
    ):
        catalog[name] = {"input": {"required": fields}, "output": outputs, "output_node": False}
    catalog["AJLateUNETLoader"] = copy.deepcopy(catalog["UNETLoader"])
    catalog["AJLateUNETLoader"]["input"]["required"]["after_conditioning"] = ["INT", {"forceInput": True}]
    return catalog


class ProductionAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = read_profiles(ROOT / "config/production_profiles.json")
        cls.registry = read_loras(ROOT / "config/lora_registry.json")
        cls.catalog = production_catalog()

    def convert(self, profile, phase):
        workflow = compose(ROOT / "workflows" / self.profiles["profiles"][profile]["render_template"],
                           self.profiles, self.registry)
        set_widget(next(node for node in workflow["nodes"] if node["id"] == 2632), "positive", "approved exact candidate")
        set_widget(next(node for node in workflow["nodes"] if node["id"] == 1731), "value", 20)
        validate_ui_workflow(workflow, self.catalog, patch_contract=True)
        converted = exact_convert(workflow, self.catalog)
        validate_converted_widgets(workflow, converted, self.catalog)
        return prepare_api_prompt(converted, self.catalog, phase=phase,
                                  approved_prompt="approved exact candidate" if phase == "render" else None)

    def test_full_render_routing_has_no_prompt_models(self):
        for identifier, profile in self.profiles["profiles"].items():
            prompt = self.convert(identifier, "render")
            self.assertEqual(prompt["4595:4529"]["inputs"]["unet_name"], profile["checkpoint"])
            self.assertEqual(prompt["4595:130"]["inputs"]["clip_name"], profile["encoder"])
            self.assertNotIn("LLMTextProcessor", {node["class_type"] for node in prompt.values()})

    def test_conditioning_release_is_ancestor_of_both_sampling_passes_and_model_loader(self):
        for identifier, profile in self.profiles["profiles"].items():
            prepared, mapping = phase_boundaries(self.convert(identifier, "render"), self.catalog, profile["memory_policy"])
            loader = prepared["4595:4529"]
            self.assertEqual(loader["class_type"], "AJLateUNETLoader")
            for root in ("4595:4529", "7334:6773", "7335:469"):
                self.assertIn("aj_conditioning_complete", _ancestors(prepared, {root}))
            self.assertNotIn("4595:4529", _ancestors(prepared, {"aj_conditioning_complete"}))
            self.assertEqual(prepared["7331"]["inputs"]["video"], ["aj_video_complete", 0])
            self.assertTrue(prepared["aj_conditioning_complete"]["inputs"]["release_encoder"])
            self.assertEqual(event_phase("7334:6773", mapping), "sampling")
            self.assertEqual(event_phase("6960:6959", mapping), "decoding")
            self.assertEqual(event_phase("unexpected-node", mapping), "unknown")

    def test_lifecycle_fails_closed_if_real_node_mapping_changes(self):
        prompt = self.convert("h3_full", "render")
        prompt["7334:6769"]["inputs"]["conditioning"] = ["4595:4529", 0]
        with self.assertRaisesRegex(WorkflowPreparationError, "cyclic"):
            phase_boundaries(prompt, self.catalog, self.profiles["profiles"]["h3_full"]["memory_policy"])

    def test_durable_shared_analysis_prunes_every_heavy_analysis_model(self):
        texts = {field: field + " exact preserved text" for field in ("visual_facts", "expanded_intent", "reference_map")}
        for identifier, profile in self.profiles["profiles"].items():
            prepared = reuse_analysis(self.convert(identifier, "prompt"), texts, self.catalog)
            processors = [node for node in prepared.values() if node["class_type"] == "LLMTextProcessor"]
            self.assertEqual(len(processors), 2)
            self.assertEqual({node["inputs"]["model"] for node in processors}, {profile["writer"]})
            self.assertTrue(all(node["inputs"]["mmproj"] == "none" for node in processors))
            self.assertEqual([prepared["aj_analysis_" + field]["inputs"]["text"] for field in texts], list(texts.values()))
            self.assertFalse(any(node["class_type"] == "PreviewImage" for node in prepared.values()))

    def test_requested_runtime_reaches_both_v20_duration_locks(self):
        prepared = self.convert("h3_full", "prompt")
        self.assertEqual(prepared["1731:184:135"]["inputs"]["value"], 20)
        lock = next(key for key in prepared if key.endswith(":7455") or key == "7455")
        for identifier in ("7453", lock):
            self.assertIn("1731:184:135", _ancestors(prepared, {identifier}))
        self.assertIn("do not append a seventh section", prepared[lock]["inputs"]["f_string"].lower())

    def test_missing_or_ambiguous_cached_text_does_not_fall_back_to_heavy_analysis(self):
        prompt = self.convert("h3_full", "prompt")
        texts = {field: "exact" for field in ("visual_facts", "expanded_intent", "reference_map")}
        with self.assertRaisesRegex(WorkflowPreparationError, "incomplete"):
            reuse_analysis(prompt, {**texts, "reference_map": ""}, self.catalog)
        duplicate = next(node for node in prompt.values() if node.get("_meta", {}).get("title") == "STEP 2 — Reference Map / Output")
        prompt["duplicate"] = copy.deepcopy(duplicate)
        with self.assertRaisesRegex(WorkflowPreparationError, "ambiguous"):
            reuse_analysis(prompt, texts, self.catalog)
