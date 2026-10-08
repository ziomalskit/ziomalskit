"""CPU graph regressions; fixtures do not claim live ComfyUI compatibility."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types
import unittest

from h3.app.workflow_conversion import (
    WorkflowPreparationError,
    prepare_api_prompt,
    validate_api_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "tests/fixtures/comfy_cli_1_21"
CATALOG = json.loads((ROOT / "tests/fixtures/workflow_catalog.json").read_text())["catalog"]
APPROVED = "subject_definitions: reference person\nsummary: approved candidate\ndetailed_description: test"


def exact_convert(workflow, catalog=None):
    """Load verified upstream pure source without importing the whole CLI."""
    provenance = json.loads((VENDOR / "PROVENANCE.json").read_text())
    for name, digest in provenance["files_sha256"].items():
        if hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f"Upstream converter fixture changed: {name}")
    schema_fixture = provenance["test_schema_fixture"]
    if hashlib.sha256((VENDOR / schema_fixture["file"]).read_bytes()).hexdigest() != schema_fixture["sha256"]:
        raise AssertionError("CPU structural schema fixture changed")
    previous = {k: v for k, v in sys.modules.items() if k == "comfy_cli" or k.startswith("comfy_cli.")}
    for k in previous:
        del sys.modules[k]
    logger = logging.getLogger("comfy_cli.workflow_to_api")
    was_disabled = logger.disabled
    logger.disabled = True
    try:
        for name in ("comfy_cli", "comfy_cli.cql"):
            pkg = types.ModuleType(name)
            pkg.__path__ = []
            sys.modules[name] = pkg
        modules = {}
        for name, file in (
            ("comfy_cli.cql.engine", "engine_support.py"),
            ("comfy_cli.cql.promoted", "promoted.py"),
            ("comfy_cli.workflow_to_api", "workflow_to_api.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, VENDOR / file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
        sys.modules["comfy_cli.cql"].promoted = modules["comfy_cli.cql.promoted"]
        return modules["comfy_cli.workflow_to_api"].convert_ui_to_api(workflow, CATALOG if catalog is None else catalog)
    finally:
        for k in list(sys.modules):
            if k == "comfy_cli" or k.startswith("comfy_cli."):
                del sys.modules[k]
        sys.modules.update(previous)
        logger.disabled = was_disabled


def patched_ui(filename):
    workflow = json.loads((ROOT / "h3/workflows" / filename).read_text())
    nodes = {n["id"]: n for graph in [workflow, *workflow["definitions"]["subgraphs"]] for n in graph["nodes"]}
    for nid, value in ((1854, 123), (6067, 124), (7360, 456)):
        nodes[nid]["widgets_values"][0] = value
    for index, seed in ((5, 456), (9, 457), (13, 789), (17, 790)):
        nodes[4022]["widgets_values"][index] = seed
    for nid, name in zip((3554, 4613, 4624, 4635, 4646, 4657), (f"reference_{i}.png" for i in range(1, 7))):
        nodes[nid]["widgets_values"][0] = name
    nodes[2624]["widgets_values"][0] = "User intent"
    nodes[2632]["widgets_values"][0] = APPROVED if filename.startswith("VAST_H3_MASTER") else ""
    return workflow


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.render = exact_convert(patched_ui("VAST_H3_MASTER_NATIVE_INT8_96GB.json"))
        cls.prompt = exact_convert(patched_ui("VAST_H3_PROMPT_ONLY_STAGE2.json"))

    def test_exact_baseline_converter_retains_unregistered_ui_nodes(self):
        for graph in (self.render, self.prompt):
            virtual = [n for n in graph.values() if n["class_type"] in {"Label (rgthree)", "Fast Groups Bypasser (rgthree)"}]
            self.assertEqual(len(virtual), 9)
            with self.assertRaisesRegex(WorkflowPreparationError, "Unregistered backend class"):
                validate_api_prompt(graph, CATALOG)

    def test_render_uses_only_video_output_and_approved_prompt(self):
        prepared = prepare_api_prompt(self.render, CATALOG, phase="render", approved_prompt=APPROVED)
        validate_api_prompt(prepared, CATALOG)
        classes = {n["class_type"] for n in prepared.values()}
        self.assertNotIn("LLMTextProcessor", classes)
        self.assertNotIn("PreviewAny", classes)
        self.assertNotIn("PreviewImage", classes)
        self.assertIn("SamplerCustomAdvanced", classes)
        self.assertEqual(prepared["2632"]["inputs"]["positive"], APPROVED)
        self.assertNotIn("3384:2633", prepared)
        self.assertEqual([k for k, n in prepared.items() if n["class_type"] == "SaveVideo"], ["7331"])

    def test_prompt_retains_all_stage_and_tile_previews_without_sampling(self):
        prepared = prepare_api_prompt(self.prompt, CATALOG, phase="prompt")
        validate_api_prompt(prepared, CATALOG)
        self.assertEqual(sum(n["class_type"] == "LLMTextProcessor" for n in prepared.values()), 16)
        self.assertFalse({"SamplerCustomAdvanced", "SaveVideo", "UNETLoader"} & {n["class_type"] for n in prepared.values()})
        titles = {n.get("_meta", {}).get("title") for n in prepared.values()}
        for title in (
            "STEP 0 — JoyCaption Visual Facts / Output", "STEP 1 — Expanded Intent / Output",
            "STEP 2 — Reference Map / Output", "STEP 3 — Creative Director / Output",
            "STEP 4 — Final H3 Prompt / Output",
        ):
            self.assertIn(title, titles)
        self.assertIn("5732", prepared)

    def test_model_host_override_render_seeds_and_references_survive(self):
        prepared = prepare_api_prompt(self.render, CATALOG, phase="render", approved_prompt=APPROVED)
        self.assertEqual(prepared["4595:4529"]["inputs"]["unet_name"], "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
        self.assertEqual(prepared["4595:130"]["inputs"]["clip_name"], "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
        self.assertEqual(prepared["4595:121"]["inputs"]["vae_name"], "minimax_h3_video_vae_int8_convrot.safetensors")
        self.assertEqual(prepared["1854"]["inputs"]["seed"], 123)
        self.assertEqual(prepared["6067"]["inputs"]["seed"], 124)
        for nid, name in zip((3554, 4613, 4624, 4635, 4646, 4657), (f"reference_{i}.png" for i in range(1, 7))):
            self.assertEqual(prepared[str(nid)]["inputs"]["image"], name)

    def test_upstream_analysis_and_creative_seeds_survive_prompt_pruning(self):
        prepared = prepare_api_prompt(self.prompt, CATALOG, phase="prompt")
        for nid, expected in ((2441, 456), (2447, 457), (2445, 789), (4275, 790)):
            source = prepared[f"2551:2640:{nid}"]["inputs"]["seed"][0]
            self.assertEqual(prepared[source]["inputs"]["value"], expected)

    def test_unknown_backend_even_on_dead_branch_is_rejected(self):
        graph = copy.deepcopy(self.prompt)
        graph["unknown"] = {"class_type": "MissingCustomPackage", "inputs": {}}
        with self.assertRaisesRegex(WorkflowPreparationError, "MissingCustomPackage"):
            prepare_api_prompt(graph, CATALOG, phase="prompt")

    def test_missing_class_inputs_or_catalog_are_rejected(self):
        for field in ("class_type", "inputs"):
            graph = copy.deepcopy(self.prompt)
            del graph["5732"][field]
            with self.assertRaises(WorkflowPreparationError):
                prepare_api_prompt(graph, CATALOG, phase="prompt")
        with self.assertRaisesRegex(WorkflowPreparationError, "catalog is required"):
            prepare_api_prompt(self.prompt, {}, phase="prompt")

    def test_active_lora_stack_survives_render_preparation(self):
        prepared = prepare_api_prompt(self.render, CATALOG, phase="render", approved_prompt=APPROVED)
        loras = [v for k, v in prepared["6164"]["inputs"].items() if k.startswith("lora_")]
        active = {v["lora"]: v["strength"] for v in loras if v["on"]}
        self.assertEqual(active, {
            "HMBreastsV2.safetensors": 1,
            "MysticXXX_MMH3-V4-ref2va.safetensors": 0.6,
            "movement_h3_lora_v1_500.safetensors": 0.5,
        })

    def test_dangling_link_and_invalid_output_slot_are_rejected(self):
        for source in (["missing", 0], ["2632", 99], ["2632", "0"], ["2632", True]):
            graph = copy.deepcopy(self.prompt)
            graph["5732"]["inputs"]["source"] = source
            with self.assertRaises(WorkflowPreparationError):
                prepare_api_prompt(graph, CATALOG, phase="prompt")

    def test_render_requires_exact_approved_candidate_and_resolver_contract(self):
        for value in (None, "", "different candidate"):
            with self.assertRaises(WorkflowPreparationError):
                prepare_api_prompt(self.render, CATALOG, phase="render", approved_prompt=value)
        graph = copy.deepcopy(self.render)
        graph["3384:2633"]["inputs"]["on_false"] = ["2624", 0]
        with self.assertRaisesRegex(WorkflowPreparationError, "resolver contract"):
            prepare_api_prompt(graph, CATALOG, phase="render", approved_prompt=APPROVED)

    def test_wrong_phase_output_paths_fail_closed(self):
        graph = copy.deepcopy(self.prompt)
        graph["5732"]["inputs"]["source"] = ["7334:6773", 0]
        with self.assertRaisesRegex(WorkflowPreparationError, "depend on rendering"):
            prepare_api_prompt(graph, CATALOG, phase="prompt")
        graph = copy.deepcopy(self.render)
        graph["7331"]["inputs"]["video"] = ["2551:2640:4275", 0]
        with self.assertRaisesRegex(WorkflowPreparationError, "depends on the autoprompter"):
            prepare_api_prompt(graph, CATALOG, phase="render", approved_prompt=APPROVED)

    def test_arguments_remain_unchanged(self):
        graph, catalog = copy.deepcopy(self.render), copy.deepcopy(CATALOG)
        prepare_api_prompt(graph, catalog, phase="render", approved_prompt=APPROVED)
        self.assertEqual(graph, self.render)
        self.assertEqual(catalog, CATALOG)


if __name__ == "__main__":
    unittest.main()
