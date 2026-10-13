"""Adversarial CPU validation and independent PR #5 composition parity."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from h3.app.workflow_adapters import get_adapter, WorkflowAdapter, WorkflowAdapterCompatibilityError as CompatibilityError
from h3.app.production import read_profiles, read_loras
from h3.app.workflow_graph import instruction_snapshot
from tests.helpers import load_controller
from tests.workflow_adapter_helpers import parity_graphs, graph_digest

ROOT = Path(__file__).resolve().parents[1]


class AdapterValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("h3_v20")
        cls.baseline = cls.adapter.load_and_validate()

    def bad(self, mutate, pattern):
        workflow = copy.deepcopy(self.baseline)
        mutate(workflow)
        with self.assertRaisesRegex(CompatibilityError, pattern):
            self.adapter.validate_baseline(workflow)

    def test_valid_v20_and_all_anchors_pass(self):
        self.adapter.validate_baseline(self.baseline)
        self.assertGreater(len(self.adapter.descriptor["anchors"]), 50)
        self.assertGreater(len(self.adapter.descriptor["required_edges"]), 100)

    def test_missing_node_fails_closed(self):
        nid = self.adapter.descriptor["anchors"]["renderer"]["node_id"]
        self.bad(lambda w: w.update(nodes=[n for n in w["nodes"] if n["id"] != nid]), "renderer.*exactly one")

    def test_duplicate_node_fails_closed(self):
        self.bad(lambda w: w["nodes"].append(copy.deepcopy(self.adapter.node(w, "renderer"))), "renderer.*exactly one")

    def test_wrong_type_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "renderer").update(type="UNETLoader"), "renderer.*type")

    def test_missing_widget_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "renderer")["widgets_values_named"].pop("clip_name"), "widget layout")

    def test_missing_input_slot_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "first_pass")["inputs"].pop(), "inputs.*changed")

    def test_missing_output_slot_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "final_video")["outputs"].pop(), "outputs.*changed")

    def test_wrong_graph_edge_fails_closed(self):
        self.bad(lambda w: next(link for link in w["links"] if link[0] == 46735).__setitem__(1, 7334), "required graph edge")

    def test_missing_graph_edge_fails_closed(self):
        self.bad(lambda w: w.update(links=[link for link in w["links"] if link[0] != 46933]), "required graph edge")

    def test_output_link_mirror_drift_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "duration")["outputs"][2]["links"].remove(46933), "outputs.*changed")

    def test_step3_step4_swap_fails_closed(self):
        def swap(w):
            one, two = self.adapter.node(w, "writer_step3"), self.adapter.node(w, "writer_step4")
            one["widgets_values_named"], two["widgets_values_named"] = two["widgets_values_named"], one["widgets_values_named"]
        self.bad(swap, "writer_step3.*defaults")

    def test_lora_row_missing_expected_structure_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "lora_stack")["widgets_values_named"]["lora_1"].pop("strengthTwo"), "lora_stack.*defaults")

    def test_positional_widget_conflict_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "first_pass")["widgets_values"].__setitem__(3, 20), "positional.*differ")

    def test_canonical_frame_expression_drift_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "frame_expression")["widgets_values_named"].update(expression="a * 24"), "defaults.*changed")

    def test_bypassed_upscaler_activation_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "latent_upscaler").update(mode=0), "type/mode.*changed")

    def test_duration_step_alias_rewire_drift_fails_closed(self):
        self.bad(lambda w: self.adapter.node(w, "duration_step3")["widgets_values_named"].update(Constant="other_duration"), "defaults.*changed")

    def test_second_pass_enable_source_cannot_be_activated(self):
        self.bad(lambda w: self.adapter.node(w, "second_pass_enable").update(mode=0), "type/mode.*changed")

    def test_proxy_subgraph_interface_drift_fails_closed(self):
        def mutate(w):
            kind = self.adapter.node(w, "first_pass")["type"]
            self.adapter.scopes(w)[kind]["inputs"][6]["linkIds"] = [46048]
        self.bad(mutate, "subgraph interface")

    def test_wrong_baseline_sha_fails_closed(self):
        descriptor = copy.deepcopy(self.adapter.descriptor)
        descriptor["baseline_sha256"] = "0" * 64
        with self.assertRaisesRegex(CompatibilityError, "size/SHA256"):
            WorkflowAdapter(descriptor).load_and_validate()

    def test_corrupt_baseline_bytes_fail_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_bytes((ROOT / "h3/workflows" / self.adapter.descriptor["baseline"]).read_bytes()[:-1])
            with self.assertRaisesRegex(CompatibilityError, "size/SHA256"):
                self.adapter.verify_baseline(path)

    def test_untrusted_adapter_paths_and_ids_rejected(self):
        for identifier in ("../h3_v20", "/tmp/evil.py", "https://example.test/adapter", "new_workflow", None):
            with self.subTest(identifier=identifier), self.assertRaises(CompatibilityError):
                get_adapter(identifier)

    def test_model_prompts_and_duration_defaults_preserved(self):
        profiles = read_profiles(ROOT / "h3/config/production_profiles.json")
        registry = read_loras(ROOT / "h3/config/lora_registry.json")
        for profile in profiles["profiles"].values():
            result = self.adapter.compose(ROOT / "h3/workflows" / profile["render_template"], profiles, registry)
            self.assertEqual(instruction_snapshot(result), instruction_snapshot(self.baseline))
            self.assertEqual(self.adapter.node(result, "first_pass")["widgets_values_named"]["steps"], 24)
            self.assertEqual(self.adapter.node(result, "second_pass")["widgets_values_named"]["denoise"], 0.29999999999999993)


class CompositionParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with load_controller() as m:
            cls.graphs = parity_graphs(m)
        cls.fixture = json.loads((ROOT / "tests/fixtures/v20_pr5_parity.json").read_text())

    def check(self, predicate):
        self.assertEqual(self.fixture["source_commit"], "c6f033ab41a4456fd55558e652a563d8c31418d1")
        for key, expected in self.fixture["digests"].items():
            if predicate(key):
                with self.subTest(graph=key):
                    self.assertEqual(graph_digest(self.graphs[key], previous_llm_routes=True), expected)

    def test_default_composition_canonical_json_equivalent_to_pr5(self):
        self.check(lambda key: key.endswith("/composition"))

    def test_patched_job_ui_canonical_json_equivalent_to_pr5(self):
        self.check(lambda key: key.endswith("/ui"))

    def test_guarded_prompt_and_render_api_canonical_json_equivalent_to_pr5(self):
        self.check(lambda key: key.endswith("/api"))

    def test_cached_analysis_api_canonical_json_equivalent_to_pr5(self):
        self.check(lambda key: key.endswith("/cached-api"))
