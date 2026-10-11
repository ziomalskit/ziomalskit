"""Only verified settings may address a trusted graph; no browser paths."""
import copy
import unittest

from h3.app.render_settings import RenderSettings
from h3.app.workflow_adapters import get_adapter
from h3.app.production import read_profiles, read_loras
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert


class RenderSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("h3_v20")
        cls.profiles = read_profiles(cls.adapter.root / "config/production_profiles.json")
        cls.registry = read_loras(cls.adapter.root / "config/lora_registry.json")

    def composed(self):
        return self.adapter.compose(self.adapter.root / "workflows/H3_FULL_RENDER.json", self.profiles, self.registry)

    def test_exact_authoritative_defaults_preserve_whole_graph(self):
        workflow = self.composed()
        before = copy.deepcopy(workflow)
        defaults = self.adapter.resolve_render_settings()
        self.assertEqual(defaults, {"target_mpx": 0.4, "first_steps": 24, "second_steps": 4})
        self.adapter.apply_render_settings(workflow, defaults)
        self.assertEqual(workflow, before)

    def changes_only_target(self, key, value):
        workflow = self.composed()
        before = copy.deepcopy(workflow)
        values = self.adapter.resolve_render_settings({key: value})
        self.adapter.apply_render_settings(workflow, values)
        control = self.adapter.descriptor["render_controls"][key]
        node = self.adapter.node(workflow, control["anchor"])
        original = self.adapter.node(before, control["anchor"])
        field = control["widget"]
        self.assertEqual(node["widgets_values_named"][field], value)
        node["widgets_values_named"][field] = original["widgets_values_named"][field]
        index = list(node["widgets_values_named"]).index(field)
        node["widgets_values"][index] = original["widgets_values"][index]
        self.assertEqual(workflow, before)

    def test_area_changes_only_authoritative_widget(self):
        self.changes_only_target("target_mpx", 0.8)

    def test_first_steps_changes_only_authoritative_widget(self):
        self.changes_only_target("first_steps", 32)

    def test_second_steps_changes_only_authoritative_widget(self):
        self.changes_only_target("second_steps", 8)

    def test_pinned_converter_consumes_each_control_at_its_actual_destination(self):
        for key, value in (("target_mpx", 0.8), ("first_steps", 32), ("second_steps", 8)):
            workflow = self.composed()
            self.adapter.apply_render_settings(workflow, self.adapter.resolve_render_settings({key: value}))
            converted = exact_convert(workflow, production_catalog())
            control = self.adapter.descriptor["render_controls"][key]
            self.assertEqual(converted[control["api_node"]]["inputs"][control["api_input"]], value)

    def test_boundary_values_accepted(self):
        for key, control in self.adapter.descriptor["render_controls"].items():
            for boundary in (control["minimum"], control["maximum"]):
                self.assertEqual(self.adapter.resolve_render_settings({key: boundary})[key], boundary)

    def test_out_of_range_values_rejected(self):
        for key, values in (("target_mpx", (0.0, 0.09, 2.01)), ("first_steps", (0, -1, 101, 10**400)), ("second_steps", (0, -1, 101, 10**400))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.adapter.resolve_render_settings({key: value})

    def test_nan_infinity_and_numeric_overflow_rejected(self):
        for key in ("target_mpx", "first_steps", "second_steps"):
            for value in (float("nan"), float("inf"), -float("inf"), 1e400):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.adapter.resolve_render_settings({key: value})
                with self.assertRaises(ValueError):
                    RenderSettings.model_validate({key: value})

    def test_strings_booleans_null_and_fractional_steps_rejected(self):
        for key in ("target_mpx", "first_steps", "second_steps"):
            for value in ("1", True, None, [], {}):
                with self.assertRaises(ValueError):
                    RenderSettings.model_validate({key: value})
                with self.assertRaises(ValueError):
                    self.adapter.resolve_render_settings({key: value})
        for key in ("first_steps", "second_steps"):
            with self.assertRaises(ValueError):
                RenderSettings.model_validate({key: 24.0})

    def test_unknown_controls_and_arbitrary_node_widget_paths_rejected(self):
        for key in ("on_steps", "off_steps", "node_id", "widgets", "1731", "1731.value_1", "widget_path"):
            with self.assertRaises(ValueError):
                RenderSettings.model_validate({key: 1})
            with self.assertRaises(ValueError):
                self.adapter.resolve_render_settings({key: 1})

    def test_frozen_settings_require_all_fields_and_never_fill_defaults(self):
        for values in (None, {}, {"first_steps": 24}):
            with self.assertRaises(ValueError):
                self.adapter.resolve_render_settings(values, frozen=True)

    def test_capabilities_contain_only_supported_controls_without_graph_paths(self):
        capabilities = self.adapter.render_capabilities()
        self.assertEqual(set(capabilities["render_controls"]), {"target_mpx", "first_steps", "second_steps"})
        for control in capabilities["render_controls"].values():
            self.assertFalse(set(control) & {"anchor", "widget", "api_node", "api_input", "node_id"})
