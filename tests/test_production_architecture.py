"""CPU contracts for the exact v20 source and production configuration."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from h3.app.analysis_cache import AnalysisCache, analysis_key, prompt_admission
from h3.app.production import read_profiles, read_loras, resolve_loras, patch_lora_slots
from h3.app.v20 import BASELINE_FILE, verified_baseline, instruction_snapshot, compose, all_nodes
from h3.app.workflow_validation import validate_ui_workflow

ROOT = Path(__file__).resolve().parents[1] / "h3"


class ProductionArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = read_profiles(ROOT / "config/production_profiles.json")
        cls.loras = read_loras(ROOT / "config/lora_registry.json")
        cls.baseline = verified_baseline(ROOT / "workflows" / BASELINE_FILE)

    def job(self, profile="h3_full"):
        return {"batch_id": "batch", "analysis_seed": 101, "prompt_seed": 201, "profile": profile,
                "context": {"profile": profile, "prompt": "walk", "pictures": [f"ref-{i}.png" for i in range(6)],
                            "audio": [], "duration_seconds": 20}}

    def test_canonical_attachment_exact_size_and_hash(self):
        self.assertEqual(len((ROOT / "workflows" / BASELINE_FILE).read_bytes()), 3084071)
        self.assertEqual(len(self.baseline["nodes"]), 170)

    def test_baseline_corruption_and_size_mismatch_fail_closed(self):
        body = (ROOT / "workflows" / BASELINE_FILE).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "baseline.json"
            for changed in (body[:-1], bytes([body[0] ^ 1]) + body[1:]):
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, "size/SHA256 mismatch"):
                    verified_baseline(path)

    def test_exactly_two_normal_profiles_and_no_quantized_default(self):
        profiles = self.profiles["profiles"]
        self.assertEqual({key: value["label"] for key, value in profiles.items()},
                         {"h3_full": "H3 Full", "10eros_full": "10Eros Full"})
        self.assertEqual(self.profiles["default_profile"], "h3_full")
        self.assertTrue(all(profile["user_facing"] for profile in profiles.values()))

    def test_two_explicit_templates_share_exact_v20_instruction_source(self):
        for identifier, profile in self.profiles["profiles"].items():
            workflow = compose(ROOT / "workflows" / profile["render_template"], self.profiles, self.loras)
            validate_ui_workflow(workflow, patch_contract=True)
            self.assertEqual(instruction_snapshot(workflow), instruction_snapshot(self.baseline))
            self.assertEqual(workflow["extra"]["aj_production"]["profile"], identifier)

    def test_no_later_director_instructions_introduced(self):
        original = instruction_snapshot(self.baseline)
        for profile in self.profiles["profiles"].values():
            actual = instruction_snapshot(compose(ROOT / "workflows" / profile["render_template"], self.profiles, self.loras))
            self.assertEqual(actual, original)
            for phrase in ("Director Refined", "Continuity Contract", "action-budget", "motion-class framework"):
                self.assertEqual(sum(phrase in value for value in actual.values()), sum(phrase in value for value in original.values()))

    def test_prompt_lock_covers_connected_step_1_to_4_instruction_primitives(self):
        original = instruction_snapshot(self.baseline)
        for node_id in (5180, 4331, 5375, 5243, 5244, 5251, 5263):
            keys = [key for key in original if key[1:] == (node_id, "value")]
            self.assertEqual(len(keys), 1)
            changed = copy.deepcopy(self.baseline)
            node = next(node for node in all_nodes(changed) if node["id"] == node_id)
            node["widgets_values_named"]["value"] += "\nDirector Refined"
            self.assertNotEqual(instruction_snapshot(changed), original)

    def test_profile_specific_full_checkpoint_and_both_writer_routes(self):
        for identifier, profile in self.profiles["profiles"].items():
            nodes = {node["id"]: node for node in all_nodes(compose(ROOT / "workflows" / profile["render_template"], self.profiles, self.loras))}
            self.assertEqual(nodes[4595]["widgets_values_named"]["unet_name"], profile["checkpoint"])
            self.assertEqual(nodes[4595]["widgets_values_named"]["clip_name"], "qwen3vl_32b_minimax_h3_bf16.safetensors")
            for nid in (2445, 4275):
                values = nodes[nid]["widgets_values_named"]
                self.assertEqual(values["model"], profile["writer"])
                self.assertEqual(values["mmproj"], "none")
                self.assertEqual(values["reasoning"], "off")
            self.assertNotEqual(nodes[2445]["widgets_values_named"]["temperature"], nodes[4275]["widgets_values_named"]["temperature"])

    def test_v20_duration_contract_and_six_section_instruction_remain_exact(self):
        nodes = {node["id"]: node for node in all_nodes(self.baseline)}
        self.assertIn("{b} seconds", nodes[7453]["widgets_values_named"]["f_string"])
        self.assertIn("[0, {b} s] runtime", nodes[7455]["widgets_values_named"]["f_string"])
        self.assertIn("do not append a seventh section", nodes[7455]["widgets_values_named"]["f_string"].lower())
        for profile in self.profiles["profiles"].values():
            actual = {node["id"]: node for node in all_nodes(compose(ROOT / "workflows" / profile["render_template"], self.profiles, self.loras))}
            for nid in (7453, 7455, 5386):
                self.assertEqual(actual[nid]["widgets_values"], nodes[nid]["widgets_values"])

    def test_controlled_gemma_compiler_benchmark_preserves_prompts_and_raises_overlap_headroom(self):
        profiles = copy.deepcopy(self.profiles)
        profile = profiles["profiles"]["h3_full"]
        profile["compiler"] = "shared_gemma"
        workflow = compose(ROOT / "workflows" / profile["render_template"], profiles, self.loras)
        nodes = {node["id"]: node for node in all_nodes(workflow)}
        self.assertEqual(nodes[2445]["widgets_values_named"]["model"], profile["writer"])
        self.assertEqual(nodes[4275]["widgets_values_named"]["model"], "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf")
        self.assertEqual(instruction_snapshot(workflow), instruction_snapshot(self.baseline))
        self.assertFalse(prompt_admission(prompt_kind="cached_text", prompt_profile="h3_full", profiles=profiles,
            active_render={"profile": "h3_full", "phase": "sampling"}, free_vram_mb=16000)[0])
        self.assertEqual(nodes[4275]["widgets_values_named"]["max_tokens"], 8192)
        self.assertEqual(nodes[4275]["widgets_values_named"]["ctx_size"], 24576)

    def test_shared_analysis_identity_excludes_profile_and_candidate_seed(self):
        first, second = self.job(), self.job("10eros_full")
        second["prompt_seed"] += 2
        second["context"]["loras"] = [{"id": "movement-v1", "enabled": False, "strength": 0.5}]
        self.assertEqual(analysis_key(first), analysis_key(second))
        second["context"]["duration_seconds"] = 10
        self.assertNotEqual(analysis_key(first), analysis_key(second))

    def test_analysis_cache_once_per_batch_and_service_epoch_invalidation(self):
        cache, batch, first = AnalysisCache(), {}, self.job()
        self.assertEqual(cache.classify(first, batch), "cold_analysis")
        texts = {field: field + " exact text" for field in ("visual_facts", "expanded_intent", "reference_map")}
        self.assertTrue(cache.record(first, batch, texts, execution_epoch=cache.epoch))
        self.assertEqual(cache.classify(self.job("10eros_full"), batch), "cached_text")
        cache.observe_service("1" * 32)
        self.assertFalse(cache.resident)
        self.assertEqual(cache.classify(first, batch), "durable_text")
        self.assertEqual(batch["analysis"]["texts"], texts)

    def test_stale_completion_cannot_assert_new_epoch_residency(self):
        cache, batch, job = AnalysisCache(), {}, self.job()
        old = cache.epoch
        cache.invalidate()
        cache.record(job, batch, {field: "facts" for field in ("visual_facts", "expanded_intent", "reference_map")}, execution_epoch=old)
        self.assertEqual(cache.classify(job, batch), "durable_text")

    def admission(self, kind, render_profile="h3_full", phase="sampling", free=40000, prompt_profile="h3_full"):
        return prompt_admission(prompt_kind=kind, prompt_profile=prompt_profile,
                                active_render={"profile": render_profile, "phase": phase},
                                profiles=self.profiles, free_vram_mb=free)[0]

    def test_h3_full_cold_analysis_waits_even_with_large_free_vram(self):
        self.assertFalse(self.admission("cold_analysis", free=96000))

    def test_h3_warm_writer_overlap_requires_sampling_and_headroom(self):
        self.assertTrue(self.admission("cached_text"))
        self.assertTrue(self.admission("durable_text"))
        for phase in ("conditioning", "decoding", "unknown", "recovery"):
            self.assertFalse(self.admission("cached_text", phase=phase))
        for free in (None, 100, 12287):
            self.assertFalse(self.admission("cached_text", free=free))

    def test_10eros_balanced_policy_still_requires_measured_headroom(self):
        self.assertTrue(self.admission("cold_analysis", render_profile="10eros_full"))
        self.assertFalse(self.admission("cold_analysis", render_profile="10eros_full", free=28000))
        self.assertFalse(self.admission("cached_text", prompt_profile="10eros_full", free=20000))

    def test_idle_analysis_window_does_not_require_gpu_mutex(self):
        self.assertEqual(prompt_admission(prompt_kind="cold_analysis", prompt_profile="h3_full", active_render=None,
                                         profiles=self.profiles, free_vram_mb=None), (True, "analysis window: renderer idle"))

    def test_lora_registry_defaults_are_profile_specific(self):
        h3 = {setting["id"]: setting for setting in resolve_loras(self.loras, "h3_full", None)}
        eros = resolve_loras(self.loras, "10eros_full", None)
        self.assertEqual({key for key, value in h3.items() if value["enabled"]}, {"hm-breasts-v2", "mysticxxx-v4", "movement-v1"})
        self.assertEqual([h3[key]["strength"] for key in ("hm-breasts-v2", "mysticxxx-v4", "movement-v1")], [1.0, .6, .5])
        self.assertFalse(any(setting["enabled"] for setting in eros))

    def test_unknown_path_like_and_duplicate_loras_fail_closed(self):
        for identifier in ("unknown", "../HMBreastsV2.safetensors", "/tmp/model.safetensors", "loras/HMBreastsV2.safetensors", "..\\HMBreastsV2.safetensors"):
            with self.assertRaises(ValueError):
                resolve_loras(self.loras, "h3_full", [{"id": identifier, "enabled": True, "strength": 1.0}])
        setting = {"id": "movement-v1", "enabled": True, "strength": .5}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            resolve_loras(self.loras, "h3_full", [setting, setting])

    def test_strength_bounds_and_nonfinite_values_rejected(self):
        for strength in (3, float("nan"), float("inf"), "1"):
            with self.assertRaisesRegex(ValueError, "bounds"):
                resolve_loras(self.loras, "h3_full", [{"id": "movement-v1", "enabled": True, "strength": strength}])

    def test_dynamic_slots_accept_new_registered_lora_and_disable_unused(self):
        registry = copy.deepcopy(self.loras)
        entry = copy.deepcopy(registry["movement-v1"])
        entry.update(id="future-lora", filename="Future.safetensors")
        registry[entry["id"]] = entry
        settings = resolve_loras(registry, "10eros_full", [{"id": "future-lora", "enabled": True, "strength": .25}])
        node = {}
        patch_lora_slots(node, registry, settings)
        rows = [value for value in node["widgets_values"] if isinstance(value, dict) and "lora" in value]
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0], {"on": True, "lora": "Future.safetensors", "strength": .25, "strengthTwo": None})
        self.assertTrue(all(not row["on"] and row["lora"] == "None" and row["strength"] == 0 for row in rows[1:]))

    def test_duplicate_registry_ids_and_paths_are_rejected(self):
        data = json.loads((ROOT / "config/lora_registry.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            for mutation in ("duplicate", "path"):
                candidate = copy.deepcopy(data)
                if mutation == "duplicate":
                    candidate["loras"].append(candidate["loras"][0])
                else:
                    candidate["loras"][0]["filename"] = "../escape.safetensors"
                path.write_text(json.dumps(candidate))
                with self.assertRaises(ValueError):
                    read_loras(path)
