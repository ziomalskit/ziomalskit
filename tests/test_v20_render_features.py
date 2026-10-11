"""Canonical switches, isolated MODEL branches and AV routes; no inference."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from h3.app.production import read_profiles, read_loras
from h3.app.render_settings import RenderSettings
from h3.app.temporal import duration_identity
from h3.app.workflow_adapters import get_adapter, WorkflowAdapterCompatibilityError
from h3.app.workflow_conversion import _ancestors
from h3.app.workflow_validation import validate_ui_workflow, validate_converted_widgets
from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert

ROOT = Path(__file__).resolve().parents[1] / 'h3'
SELECTION = [{'id': 'hm-breasts-v2', 'enabled': True, 'strength': .75}]


def selected_latent_route(graph):
    """Evaluate only pinned PassOrNone/IfElse logic and latent dependencies.

    ComfyUI 6b747c... nodes_logic.SwitchNode selects a lazy branch; Easy Use
    450b1c... PassOrNone returns (anything, anything is None). Samplers/upscaler
    are opaque markers, never invoked. Structural ancestry alone is insufficient.
    """
    visited = []
    def evaluate(key, slot=0):
        node = graph[key]
        inputs, kind = node['inputs'], node['class_type']
        def link(name):
            value = inputs[name]
            return evaluate(*value) if isinstance(value, list) else value
        if kind in {'PrimitiveBoolean', 'Bool-🔬'}:
            return inputs['value']
        if kind == 'easy PassOrNone':
            value = link('anything') if 'anything' in inputs else None
            return value is None if slot == 1 else value
        if kind == 'ComfySwitchNode':
            return link('on_true' if link('switch') else 'on_false')
        if kind == 'SamplerCustomAdvanced':
            if key == '7335:469':
                link('latent_image')
            visited.append(key)
            return (key, slot)
        if kind == 'LTXVSeparateAVLatent':
            link('av_latent')
        elif kind == 'MinimaxH3LatentUpscaler3D':
            link('latent')
        elif kind == 'LTXVConcatAVLatent':
            link('video_latent')
            link('audio_latent')
        else:
            raise AssertionError('Unexpected latent route: '+kind)
        visited.append(key)
        return (key, slot)
    source = graph['6960:6959']['inputs']['samples']
    result = evaluate(*source)
    return result, visited


class V20RenderFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = get_adapter('h3_v20')
        cls.profiles = read_profiles(ROOT / 'config/production_profiles.json')
        cls.registry = read_loras(ROOT / 'config/lora_registry.json')

    def workflow(self, values=None, profile='h3_full'):
        a = self.a
        w = a.compose(ROOT / 'workflows' / self.profiles['profiles'][profile]['render_template'], self.profiles, self.registry)
        a.set(w, 'duration', 'value', 20)
        a.set(w, 'prompt_override', 'positive', 'CPU exact approved prompt')
        settings = a.resolve_render_settings(values, profile=profile)
        a.apply_render_settings(w, settings, profile=profile)
        return w, {'profile': profile, 'render_settings': settings,
                   'profile_identity': self.profiles['profiles'][profile], **duration_identity(20)}

    def prepare(self, values=None, profile='h3_full'):
        w, context = self.workflow(values, profile)
        catalog = production_catalog()
        validate_ui_workflow(w, catalog)
        converted = exact_convert(w, catalog)
        validate_converted_widgets(w, converted, catalog)
        graph = self.a.prepare_api_prompt(converted, catalog, phase='render',
            approved_prompt='CPU exact approved prompt', context=context)
        graph, _ = self.a.install_runtime_guards(graph, catalog, context, phase='render')
        self.a.validate_final_graph(graph, context, phase='render')
        return graph, context

    def test_second_pass_and_upscaler_defaults_are_off_with_product_target_1_2(self):
        defaults = self.a.resolve_render_settings()
        self.assertEqual(defaults['latent_upscaler_mpx'], 1.2)
        self.assertIs(defaults['latent_upscaler_enabled'], False)
        self.assertIs(defaults['second_pass_enabled'], False)
        self.assertEqual(defaults['second_pass_loras'], [])

    def test_off_preserves_canonical_widgets_and_entire_runtime_ui(self):
        w = self.a.compose(ROOT / 'workflows/H3_FULL_RENDER.json', self.profiles, self.registry)
        before = copy.deepcopy(w)
        self.a.apply_render_settings(w, self.a.resolve_render_settings())
        self.assertEqual(w, before)
        self.assertEqual(self.a.node(w, 'latent_upscaler')['widgets_values_named']['mode.megapixels'], .8)

    def test_default_prepared_output_selects_first_pass_and_no_upscale_or_second_execution(self):
        graph, _ = self.prepare()
        output, executed = selected_latent_route(graph)
        self.assertEqual(output, ('7334:6773', 0))
        self.assertNotIn('7335:469', executed)
        self.assertNotIn('6175', executed)
        self.assertNotIn('6165', graph)

    def test_second_steps_alone_cannot_enable_second_pass(self):
        graph, _ = self.prepare({'second_steps': 99})
        self.assertEqual(graph['7335:360']['inputs']['steps'], 99)
        self.assertEqual(selected_latent_route(graph)[0], ('7334:6773', 0))
        self.assertNotIn('6263', graph)

    def test_second_lora_selection_alone_cannot_enable_or_execute_second_branch(self):
        graph, context = self.prepare({'second_pass_loras': SELECTION})
        self.assertEqual(context['render_settings']['second_pass_loras'], SELECTION)
        self.assertNotIn('6165', graph)
        self.assertNotIn('7335:469', selected_latent_route(graph)[1])

    def test_on_uses_real_boolean_alias_retrieval_logic_and_lazy_output_switch(self):
        w, _ = self.workflow({'second_pass_enabled': True})
        self.assertEqual(self.a.node(w, 'second_pass_enable')['mode'], 0)
        self.assertEqual(self.a.node(w, 'second_pass_enable_alias')['mode'], 4)
        graph, _ = self.prepare({'second_pass_enabled': True})
        self.assertEqual(graph['6263']['class_type'], 'Bool-🔬')
        self.assertIs(graph['6263']['inputs']['value'], True)
        self.assertEqual(graph['7335:6266']['inputs']['anything'], ['6263', 0])
        self.assertEqual(graph['7335:6268']['inputs']['switch'], ['7335:6266', 1])
        self.assertEqual(graph['7335:6173']['inputs']['switch'], ['7335:6268', 0])
        self.assertEqual(selected_latent_route(graph)[0], ('7335:469', 0))

    def test_second_pass_off_after_on_restores_exact_disabled_graph(self):
        w, _ = self.workflow()
        before = copy.deepcopy(w)
        self.a.apply_render_settings(w, self.a.resolve_render_settings({'second_pass_enabled': True}))
        self.a.apply_render_settings(w, self.a.resolve_render_settings())
        self.assertEqual(w, before)

    def test_second_steps_reach_real_scheduler_when_on(self):
        graph, _ = self.prepare({'second_pass_enabled': True, 'second_steps': 17})
        self.assertEqual(graph['7335:360']['inputs']['steps'], 17)
        self.assertEqual(graph['7335:469']['inputs']['sigmas'], ['7335:360', 0])

    def test_upscaler_target_alone_does_not_activate_it(self):
        graph, context = self.prepare({'latent_upscaler_mpx': 1.9})
        self.assertEqual(context['render_settings']['latent_upscaler_mpx'], 1.9)
        self.assertNotIn('6175', graph)
        self.assertEqual(selected_latent_route(graph)[0], ('7334:6773', 0))

    def test_upscaler_activates_real_video_split_node_audio_split_and_av_join(self):
        graph, _ = self.prepare({'latent_upscaler_enabled': True})
        self.assertEqual(graph['6175']['class_type'], 'MinimaxH3LatentUpscaler3D')
        self.assertEqual(graph['6175']['inputs']['latent'], ['6176', 0])
        for key in ['6176', '6177']:
            self.assertEqual(graph[key]['inputs']['av_latent'], ['7334:6773', 0])
        self.assertEqual(graph['6178']['inputs'], {'video_latent': ['6175', 0], 'audio_latent': ['6177', 1]})
        self.assertEqual(graph['7335:6173']['inputs']['on_false'], ['6178', 0])
        self.assertEqual(graph['7335:469']['inputs']['latent_image'], ['6178', 0])

    def test_upscaler_off_after_on_restores_exact_bypass_and_saved_0_8(self):
        w, _ = self.workflow()
        before = copy.deepcopy(w)
        self.a.apply_render_settings(w, self.a.resolve_render_settings({'latent_upscaler_enabled': True}))
        self.a.apply_render_settings(w, self.a.resolve_render_settings())
        self.assertEqual(w, before)

    def test_upscaler_enabled_target_reaches_actual_dynamic_mpx_widget(self):
        graph, _ = self.prepare({'latent_upscaler_enabled': True, 'latent_upscaler_mpx': 1.6})
        self.assertEqual(graph['6175']['inputs']['mode'], 'megapixels')
        self.assertEqual(graph['6175']['inputs']['mode.megapixels'], 1.6)

    def test_initial_area_and_upscale_target_are_independent(self):
        graph, _ = self.prepare({'target_mpx': .6, 'latent_upscaler_enabled': True, 'latent_upscaler_mpx': 1.5})
        self.assertEqual(graph['1731:1735']['inputs']['megapixels'], .6)
        self.assertEqual(graph['6175']['inputs']['mode.megapixels'], 1.5)

    def test_upscaler_fixed_chunking_unload_device_precision_and_model_preserved(self):
        graph, _ = self.prepare({'latent_upscaler_enabled': True})
        for field, expected in self.a.descriptor['api']['render_routes']['upscaler_fixed_inputs'].items():
            self.assertEqual(graph['6175']['inputs'][field], expected)
        self.assertIs(graph['6175']['inputs']['enable_temporal_chunking'], True)
        self.assertIs(graph['6175']['inputs']['force_unload'], False)

    def assert_combination(self, upscaler, second):
        for profile in ['h3_full', '10eros_full']:
            graph, _ = self.prepare({'latent_upscaler_enabled': upscaler, 'second_pass_enabled': second}, profile)
            incoming = ['6178', 0] if upscaler else ['7334:6773', 0]
            self.assertEqual(graph['7335:469']['inputs']['latent_image'], incoming)
            self.assertEqual(graph['7335:6173']['inputs']['on_false'], incoming)
            self.assertEqual(graph['7335:6173']['inputs']['on_true'], ['7335:469', 0])
            result, executed = selected_latent_route(graph)
            self.assertEqual(result, ('7335:469', 0) if second else tuple(incoming))
            self.assertEqual('7335:469' in executed, second)
            self.assertEqual('6175' in executed, upscaler)
            self.assertEqual('6263' in graph, second)
            if upscaler and second:
                self.assertLess(executed.index('7334:6773'), executed.index('6175'))
                self.assertLess(executed.index('6175'), executed.index('7335:469'))

    def test_combination_off_off(self): self.assert_combination(False, False)
    def test_combination_on_off(self): self.assert_combination(True, False)
    def test_combination_off_on(self): self.assert_combination(False, True)
    def test_combination_on_on(self): self.assert_combination(True, True)

    def test_existing_second_lora_loader_is_reused_on_isolated_model_path(self):
        graph, _ = self.prepare({'second_pass_enabled': True, 'second_pass_loras': SELECTION})
        self.assertEqual(graph['6165']['class_type'], 'Power Lora Loader (rgthree)')
        self.assertEqual(graph['6165']['inputs']['model'], ['6091:6079', 0])
        self.assertEqual(graph['6165']['inputs']['lora_1'], {'on': True, 'lora': self.registry['hm-breasts-v2']['filename'], 'strength': .75})
        self.assertEqual(graph['7335:6179']['inputs']['anything'], ['6165', 0])
        for key in ['7335:360', '7335:29']:
            self.assertEqual(graph[key]['inputs']['model'], ['7335:6179', 0])
        self.assertNotIn('clip', graph['6165']['inputs'])

    def test_second_lora_leaves_entire_first_model_and_sampler_ancestry_identical(self):
        before, _ = self.prepare({'second_pass_enabled': True})
        after, _ = self.prepare({'second_pass_enabled': True, 'second_pass_loras': SELECTION})
        keys = _ancestors(before, {'7334:6773'})
        self.assertEqual(_ancestors(after, {'7334:6773'}), keys)
        self.assertEqual({key: before[key] for key in keys}, {key: after[key] for key in keys})
        self.assertNotIn('6165', keys)

    def test_multiple_second_loras_reuse_registry_ordered_stack(self):
        selection = SELECTION + [{'id': 'movement-h3', 'enabled': True, 'strength': .25}]
        # Registry IDs are trusted; choose the next registered ID rather than
        # depending on a display filename or a future added entry.
        selection[1]['id'] = next(key for key in self.registry if key != SELECTION[0]['id'])
        graph, context = self.prepare({'second_pass_enabled': True, 'second_pass_loras': selection})
        self.assertEqual(context['render_settings']['second_pass_loras'], selection)
        self.assertEqual(graph['6165']['inputs']['lora_2']['lora'], self.registry[selection[1]['id']]['filename'])
        self.assertEqual(graph['6165']['inputs']['lora_2']['strength'], .25)

    def test_second_lora_off_after_on_restores_empty_canonical_loader(self):
        w, _ = self.workflow()
        before = copy.deepcopy(w)
        on = self.a.resolve_render_settings({'second_pass_enabled': True, 'second_pass_loras': SELECTION}, profile='h3_full')
        self.a.apply_render_settings(w, on, profile='h3_full')
        self.a.apply_render_settings(w, self.a.resolve_render_settings({'second_pass_loras': SELECTION}, profile='h3_full'), profile='h3_full')
        self.assertEqual(w, before)

    def test_second_lora_with_upscaler_off(self):
        graph, _ = self.prepare({'second_pass_enabled': True, 'second_pass_loras': SELECTION})
        self.assertNotIn('6175', graph)
        self.assertIn('6165', graph)
        self.assertEqual(graph['7335:469']['inputs']['latent_image'], ['7334:6773', 0])

    def test_second_lora_with_upscaler_on(self):
        graph, _ = self.prepare({'second_pass_enabled': True, 'latent_upscaler_enabled': True, 'second_pass_loras': SELECTION})
        self.assertIn('6175', graph)
        self.assertIn('6165', graph)
        self.assertEqual(graph['7335:469']['inputs']['latent_image'], ['6178', 0])

    def test_mode_only_upscaler_activation_is_rejected(self):
        w, ctx = self.workflow()
        self.a.node(w, 'latent_upscaler')['mode'] = 0
        ctx['render_settings']['latent_upscaler_enabled'] = True
        with self.assertRaises(WorkflowAdapterCompatibilityError):
            self.a.prepare_api_prompt(exact_convert(w, production_catalog()), production_catalog(), phase='render',
                approved_prompt='CPU exact approved prompt', context=ctx)

    def test_wrong_latent_routing_or_audio_rejoin_fails_closed(self):
        graph, ctx = self.prepare({'latent_upscaler_enabled': True, 'second_pass_enabled': True})
        for key, field, value in [('6178', 'audio_latent', ['6177', 0]), ('6175', 'latent', ['7334:6773', 0]),
                                   ('7335:469', 'latent_image', ['7334:6773', 0])]:
            broken = copy.deepcopy(graph); broken[key]['inputs'][field] = value
            with self.subTest(key=key), self.assertRaises(WorkflowAdapterCompatibilityError):
                self.a.validate_final_graph(broken, ctx, phase='render')

    def test_wrong_second_model_route_or_first_pass_lora_leak_fails_closed(self):
        graph, ctx = self.prepare({'second_pass_enabled': True, 'second_pass_loras': SELECTION})
        for key, field, value in [('7334:6771', 'model', ['6165', 0]), ('7334:6769', 'model', ['6165', 0]),
                                   ('7335:6179', 'anything', ['6164', 0]), ('6165', 'model', ['6164', 0])]:
            broken = copy.deepcopy(graph); broken[key]['inputs'][field] = value
            with self.subTest(key=key), self.assertRaises(WorkflowAdapterCompatibilityError):
                self.a.validate_final_graph(broken, ctx, phase='render')

    def test_wrong_enable_logic_or_missing_lazy_catalog_fails_closed(self):
        w, ctx = self.workflow({'second_pass_enabled': True})
        cat = production_catalog(); converted = exact_convert(w, cat)
        converted['7335:6266']['inputs']['anything'] = ['7335:6269', 0]
        with self.assertRaises(WorkflowAdapterCompatibilityError):
            self.a.prepare_api_prompt(converted, cat, phase='render', approved_prompt='CPU exact approved prompt', context=ctx)
        for key, kind in [('7335:6269', 'Bool-🔬'), ('7335:6268', 'easy PassOrNone'),
                          ('7335:6173', 'easy PassOrNone'), ('7335:6179', 'ComfySwitchNode')]:
            broken = exact_convert(w, cat); broken[key]['class_type'] = kind
            with self.subTest(key=key), self.assertRaisesRegex(WorkflowAdapterCompatibilityError, 'type/inputs changed'):
                self.a.prepare_api_prompt(broken, cat, phase='render', approved_prompt='CPU exact approved prompt', context=ctx)
        broken = exact_convert(w, cat); broken['7335:6269']['inputs']['value'] = 0
        with self.assertRaisesRegex(WorkflowAdapterCompatibilityError, 'false boolean'):
            self.a.prepare_api_prompt(broken, cat, phase='render', approved_prompt='CPU exact approved prompt', context=ctx)
        for name in ['on_false', 'on_true']:
            for options in [{'lazy': False}, {}, None, 'lazy', True]:
                cat = production_catalog(); cat['ComfySwitchNode']['input']['optional'][name][1] = options
                # Conversion uses the valid pinned schema. A malformed live
                # worker contract must still raise an explicit adapter error.
                with self.subTest(name=name, options=options), self.assertRaisesRegex(WorkflowAdapterCompatibilityError, 'lazy branches'):
                    self.a.prepare_api_prompt(exact_convert(w, production_catalog()), cat, phase='render', approved_prompt='CPU exact approved prompt', context=ctx)
        for schema in [None, [], 'lazy', {'required': None}, {'optional': None}]:
            cat = production_catalog(); cat['ComfySwitchNode']['input'] = schema
            with self.subTest(schema=schema), self.assertRaisesRegex(WorkflowAdapterCompatibilityError, 'lazy branches'):
                self.a.prepare_api_prompt(exact_convert(w, production_catalog()), cat, phase='render', approved_prompt='CPU exact approved prompt', context=ctx)

    def test_upscaler_and_second_lora_canonical_anchors_fail_closed_on_corruption(self):
        for role in ['upscale_video_split', 'upscale_audio_split', 'upscale_av_join', 'second_lora_stack', 'second_model_alias', 'second_model_get']:
            w = self.a.load_and_validate(); self.a.node(w, role)['type'] = 'Unknown'
            with self.subTest(role=role), self.assertRaises(WorkflowAdapterCompatibilityError):
                self.a.validate_baseline(w)

    def test_empty_second_lora_positional_header_corruption_fails_closed(self):
        w = self.a.load_and_validate()
        self.a.node(w, 'second_lora_stack')['widgets_values'][1]['type'] = 'OtherWidget'
        with self.assertRaisesRegex(WorkflowAdapterCompatibilityError, 'positional widgets'):
            self.a.validate_baseline(w)

    def test_invalid_toggle_json_types_rejected_by_model_and_adapter(self):
        for key in ['latent_upscaler_enabled', 'second_pass_enabled']:
            for value in [None, 0, 1, .0, 'true', 'false', [], {}]:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): RenderSettings.model_validate({key: value})
                with self.assertRaises(ValueError): self.a.resolve_render_settings({key: value})

    def test_invalid_upscale_targets_and_second_steps_rejected(self):
        for value in [None, True, '1.2', -.1, .09, 2.01, float('nan'), float('inf'), 10**400]:
            with self.subTest(value=value), self.assertRaises(ValueError): self.a.resolve_render_settings({'latent_upscaler_mpx': value})
        for value in [False, '4', 4.0, 4.5, 0, 101, float('inf')]:
            with self.assertRaises(ValueError): self.a.resolve_render_settings({'second_steps': value})

    def test_upscale_target_bounds_accepted(self):
        for value in [.1, 2.0]:
            self.assertEqual(self.a.resolve_render_settings({'latent_upscaler_mpx': value})['latent_upscaler_mpx'], value)

    def test_downscaling_only_rejected_when_upscaler_on(self):
        values = {'target_mpx': 1.6, 'latent_upscaler_mpx': 1.2}
        self.assertFalse(self.a.resolve_render_settings(values)['latent_upscaler_enabled'])
        with self.assertRaisesRegex(ValueError, '(?i)at least render area'):
            self.a.resolve_render_settings({**values, 'latent_upscaler_enabled': True})

    def test_unknown_filenames_paths_duplicate_ids_and_bad_strengths_rejected(self):
        for identifier in ['unknown', self.registry['hm-breasts-v2']['filename'], '../hm-breasts-v2', '/tmp/lora.safetensors']:
            with self.assertRaises(ValueError): self.a.resolve_render_settings({'second_pass_loras': [{'id': identifier, 'strength': 1}]}, profile='h3_full')
        for value in [True, '1', None, -2.01, 2.01, float('nan'), float('inf'), 10**400]:
            with self.assertRaises(ValueError): self.a.resolve_render_settings({'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': value}]}, profile='h3_full')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.a.resolve_render_settings({'second_pass_loras': SELECTION * 2}, profile='h3_full')

    def test_lora_strength_boundaries_and_registered_defaults(self):
        for strength in [-2, 2]:
            result = self.a.resolve_render_settings({'second_pass_loras': [{'id': 'hm-breasts-v2', 'strength': strength}]}, profile='h3_full')
            self.assertEqual(result['second_pass_loras'][0], {'id': 'hm-breasts-v2', 'enabled': True, 'strength': float(strength)})

    def test_registry_strength_bounds_and_profile_incompatibility_are_authoritative(self):
        registry = copy.deepcopy(self.registry)
        registry['hm-breasts-v2']['strength_range'] = [-.5, .5]
        with patch('h3.app.production.read_loras', return_value=registry), self.assertRaisesRegex(ValueError, 'registry bounds'):
            self.a.resolve_render_settings({'second_pass_loras': SELECTION}, profile='h3_full')
        registry = copy.deepcopy(self.registry)
        registry['hm-breasts-v2']['compatibility']['h3_full'] = 'incompatible'
        with patch('h3.app.production.read_loras', return_value=registry), self.assertRaisesRegex(ValueError, 'incompatible'):
            self.a.resolve_render_settings({'second_pass_loras': SELECTION}, profile='h3_full')

    def test_frozen_extended_settings_never_refill_missing_fields_or_lora_defaults(self):
        defaults = self.a.resolve_render_settings()
        for key in ['latent_upscaler_mpx', 'latent_upscaler_enabled', 'second_pass_enabled', 'second_pass_loras']:
            values = copy.deepcopy(defaults); values.pop(key)
            with self.assertRaises(ValueError): self.a.resolve_render_settings(values, frozen=True)
        with self.assertRaises(ValueError):
            self.a.resolve_render_settings({**defaults, 'second_pass_loras': [{'id': 'hm-breasts-v2'}]}, frozen=True, profile='h3_full')

    def test_unknown_graph_addresses_widgets_and_unsupported_controls_rejected(self):
        for key in ['node_id', 'widget', 'mode.megapixels', '6175', 'graph_path', 'on_steps', 'off_steps']:
            with self.assertRaises(ValueError): RenderSettings.model_validate({key: 1})
        for key in ['filename', 'path', 'model', 'widget', 'node_id']:
            with self.assertRaises(ValueError): RenderSettings.model_validate({'second_pass_loras': [{'id': 'hm-breasts-v2', key: 'evil'}]})

    def test_capabilities_declare_dependencies_and_product_default_without_addresses(self):
        c = self.a.render_capabilities()['render_controls']
        self.assertEqual(c['latent_upscaler_mpx']['default'], 1.2)
        self.assertEqual(c['latent_upscaler_mpx']['enabled_when'], 'latent_upscaler_enabled')
        self.assertEqual(c['latent_upscaler_mpx']['minimum_from'], 'target_mpx')
        self.assertEqual(c['second_steps']['enabled_when'], 'second_pass_enabled')
        self.assertEqual(c['second_pass_loras']['enabled_when'], 'second_pass_enabled')
        self.assertFalse(any(set(control) & {'node_id', 'anchor', 'widget', 'mode_anchors'} for control in c.values()))
