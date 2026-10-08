"""Ambiguous UI storage, pinned converter consumption and capture provenance."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from h3.app.workflow_validation import validate_ui_workflow, validate_converted_widgets
from h3.app.workflow_conversion import WorkflowPreparationError
from tests.helpers import load_controller
from tests.step3_helpers import ADAPTER_CATALOG
from tests.test_controller import history
from tests.test_submission import fixture_job, client_factory
from tests.test_workflows import exact_convert, APPROVED


class Step3WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, m, path, catalog):
        requests = []
        async def cli(service, *args):
            if args[0] == 'run':
                return {'data':{'status':'preview','prompt':exact_convert(json.loads(path.read_text()), catalog)}}
            return {'data':{'valid':True,'error_count':0,'spends_credits':False,'partner_nodes':[]}}
        def respond(req):
            requests.append(req.method)
            if req.method == 'GET': return httpx.Response(200, json=catalog)
            body = json.loads(req.content)
            return httpx.Response(200, json={'prompt_id':body['prompt_id']})
        with patch.object(m, 'run_cli_envelope', side_effect=cli), \
             patch.object(m.httpx, 'AsyncClient', side_effect=client_factory(respond)):
            prepared = await m._prepare_workflow_api(path, 'render')
        self.assertEqual(requests, ['GET'])
        return prepared

    async def test_real_converter_preserves_source_sigma_seed_and_both_phases(self):
        with load_controller() as m:
            job = fixture_job(m)
            path = m.patch_workflow(job, m.MASTER, approved_prompt=APPROVED)
            prepared = await self.prepare(m, path, ADAPTER_CATALOG)
            self.assertEqual(prepared['1854']['inputs']['seed'], 123)
            self.assertEqual(prepared['4537:4553']['inputs']['shift_video'], 6)
            self.assertEqual(prepared['4537:4553']['inputs']['shift_audio'], 3)
            path = m.patch_workflow(job, m.PROMPT_ONLY)
            ui = json.loads(path.read_text())
            validate_ui_workflow(ui, ADAPTER_CATALOG, patch_contract=True)
            graph = exact_convert(ui, ADAPTER_CATALOG)
            validate_converted_widgets(ui, graph, ADAPTER_CATALOG)
            self.assertEqual(graph['2551:2640:2441']['class_type'], 'LLMTextProcessor')

    async def test_duplicate_identity_in_scope_and_patch_namespace_fails_before_conversion(self):
        with load_controller() as m:
            for mutation in ('same_scope','different_scope','wrong_type','missing_field'):
                with self.subTest(mutation=mutation):
                    ui = json.loads(m.MASTER.read_text())
                    seed = next(n for n in ui['nodes'] if n['id'] == 1854)
                    duplicate = copy.deepcopy(seed)
                    duplicate['widgets_values'][0] = 999; duplicate['widgets_values_named']['seed'] = 999
                    if mutation == 'same_scope': ui['nodes'].append(duplicate)
                    elif mutation == 'different_scope': ui['definitions']['subgraphs'][0]['nodes'].append(duplicate)
                    elif mutation == 'wrong_type': seed['type'] = 'PrimitiveNode'
                    else:
                        node = next(n for n in ui['nodes'] if n['id'] == 2632)
                        node['widgets_values_named'].clear(); node['widgets_values'].clear()
                    path = m.STATE / 'invalid-template.json'; path.write_text(json.dumps(ui))
                    cli = AsyncMock()
                    with patch.object(m, 'run_cli_envelope', cli), patch.object(m.httpx, 'AsyncClient') as client:
                        with self.assertRaises(ValueError): m.patch_workflow(fixture_job(m), path, approved_prompt=APPROVED)
                        with self.assertRaises(ValueError): await m._submit_workflow(fixture_job(m), path, 'render')
                    cli.assert_not_awaited(); client.assert_not_called()

    async def test_conflicting_missing_and_reordered_widgets_fail_zero_submission(self):
        for mutation in ('conflict','missing_positional','missing_named','catalog_order','catalog_duplicate_order','missing_dynamic_field'):
            with self.subTest(mutation=mutation), load_controller() as m:
                job = fixture_job(m); path = m.patch_workflow(job, m.MASTER, approved_prompt=APPROVED)
                ui = json.loads(path.read_text()); catalog = copy.deepcopy(ADAPTER_CATALOG)
                sigma = next(n for g in ui['definitions']['subgraphs'] for n in g['nodes'] if n['id'] == 4553)
                if mutation == 'conflict': sigma['widgets_values_named']['shift_video'] = 9
                elif mutation == 'missing_positional': sigma['widgets_values'] = []
                elif mutation == 'missing_named': sigma.pop('widgets_values_named')
                elif mutation == 'catalog_order': catalog['MiniMaxH3SigmaShift']['input_order'] = {'required':['shift_audio','shift_video']}
                elif mutation == 'catalog_duplicate_order': catalog['MiniMaxH3SigmaShift']['input_order'] = {'required':['shift_video','shift_video']}
                else:
                    node = next(n for n in ui['nodes'] if n['type'] == 'ResizeImageMaskAlt')
                    node['widgets_values_named'].pop('resize_type.megapixels')
                    node['widgets_values'] = list(node['widgets_values_named'].values())
                path.write_text(json.dumps(ui))
                posts = []
                def http(req):
                    if req.method == 'POST': posts.append(req)
                    return httpx.Response(200, json=catalog)
                async def cli(service, *args):
                    if args[0] == 'run': return {'data':{'status':'preview','prompt':exact_convert(ui, catalog)}}
                    return {'data':{'valid':True,'error_count':0}}
                with patch.object(m, 'run_cli_envelope', side_effect=cli), \
                     patch.object(m.httpx, 'AsyncClient', side_effect=client_factory(http)):
                    with self.assertRaises(ValueError): await m._submit_workflow(job, path, 'render')
                self.assertEqual(posts, [])
                self.assertFalse(job.get('render_submission_attempted'))

    async def test_converter_catalog_change_or_corruption_cannot_silently_use_default(self):
        with load_controller() as m:
            job = fixture_job(m); path = m.patch_workflow(job, m.MASTER, approved_prompt=APPROVED)
            ui = json.loads(path.read_text()); graph = exact_convert(ui, ADAPTER_CATALOG)
            for value in (999, None):
                candidate = copy.deepcopy(graph)
                if value is None: candidate['1854']['inputs'].pop('seed')
                else: candidate['1854']['inputs']['seed'] = value
                with patch.object(m.httpx, 'AsyncClient', side_effect=client_factory(lambda req: httpx.Response(200, json=ADAPTER_CATALOG))), \
                     patch.object(m, '_dispatch_prepared_workflow', AsyncMock()) as post:
                    with self.assertRaisesRegex(ValueError, 'changed or dropped'):
                        await m._prepare_workflow_api(path, 'render', preview_envelope={'data':{'status':'preview','prompt':candidate}})
                post.assert_not_awaited()

    async def test_duplicate_each_logical_capture_fails_without_fallback_or_render(self):
        with load_controller() as m:
            for key in ('step0_visual_facts_title','step1_expanded_intent_title','step2_reference_map_title','step3_creative_plan_title','step4_final_prompt_title'):
                with self.subTest(title=key):
                    title = m.MAP['prompt_capture'][key]
                    entry = history()
                    entry['prompt'].update({str(n):{'class_type':'PreviewAny','_meta':{'title':title}} for n in (900,901)})
                    entry['outputs'] = {'900':{'text':['correct']},'901':{'text':['WRONG']},'5732':{'text':['fallback must not hide ambiguity']}}
                    with self.assertRaisesRegex(ValueError, 'Ambiguous'): m.extract_prompt_texts(entry)
                    job = dict(id='capture', status='prompt_running', review_required=False, batch_seq=1,candidate_index=1,created_at=1)
                    m.queue[:] = [job]
                    self.assertTrue(m._apply_history_outcome(job, 'prompt', entry))
                    self.assertEqual(job['status'], 'prompt_failed')
                    self.assertIsNone(job['final_h3_prompt'])
                    self.assertIsNone(m.pick_next_render_job())

    async def test_capture_unique_titles_and_verified_fallback_preserve_exact_text(self):
        with load_controller() as m:
            entry = history(); entry['outputs'] = {'5732':{'text':['final exact']}}
            self.assertEqual(m.extract_prompt_texts(entry)['final_h3_prompt'], 'final exact')
            title = m.MAP['prompt_capture']['step4_final_prompt_title']
            entry['prompt']['900'] = {'class_type':'PreviewAny','_meta':{'title':title}}
            entry['outputs']['900'] = {'text':['first','second']}
            self.assertEqual(m.extract_prompt_texts(entry)['final_h3_prompt'], 'first\nsecond')
            entry['prompt']['900']['class_type'] = 'LLMTextProcessor'
            with self.assertRaisesRegex(ValueError, 'Unexpected'): m.extract_prompt_texts(entry)
            for graph in ({}, {'5732':{'class_type':'LLMTextProcessor'}}):
                entry['prompt'] = graph
                with self.assertRaisesRegex(ValueError, 'identity/type'): m.extract_prompt_texts(entry)

    async def test_real_converted_capture_ambiguity_never_dispatches_automatic_render(self):
        with load_controller() as m:
            job = fixture_job(m)
            job.update(status='prompt_queued', review_required=False, batch_seq=1, candidate_index=1, created_at=1)
            m.queue[:] = [job]
            ui = json.loads(m.PROMPT_ONLY.read_text())
            for graph in [ui, *ui['definitions']['subgraphs']]:
                for node in graph['nodes']:
                    if node['id'] == 2932:
                        node['title'] = m.MAP['prompt_capture']['step4_final_prompt_title']
            m.PROMPT_ONLY.write_text(json.dumps(ui))
            posts = []
            async def cli(service, *args):
                if args[0] == 'run':
                    path = Path(args[args.index('--workflow') + 1])
                    return {'data':{'status':'preview','prompt':exact_convert(json.loads(path.read_text()), ADAPTER_CATALOG)}}
                return {'data':{'valid':True,'error_count':0}}
            def respond(req):
                if req.method == 'GET': return httpx.Response(200, json=ADAPTER_CATALOG)
                self.assertEqual(str(req.url), m.PROMPT_COMFY_URL + '/prompt')
                body = json.loads(req.content); posts.append(body)
                return httpx.Response(200, json={'prompt_id':body['prompt_id']})
            async def terminal(service, pid):
                graph = posts[0]['prompt']
                entry = history()
                entry['prompt'] = [0, pid, graph]
                entry['outputs'] = {key:{'text':['captured ' + node.get('_meta',{}).get('title','')]}
                                    for key, node in graph.items() if node['class_type'] == 'PreviewAny'}
                entry['outputs']['2551:2472'] = {'text':['CORRECT final candidate']}
                entry['outputs']['2551:2932'] = {'text':['WRONG creative plan']}
                entry['outputs']['5732'] = {'text':['CORRECT fallback']}
                return entry
            with patch.object(m, 'run_cli_envelope', side_effect=cli), \
                 patch.object(m.httpx, 'AsyncClient', side_effect=client_factory(respond)), \
                 patch.object(m, 'watch_prompt', AsyncMock(return_value={'ok':True})), \
                 patch.object(m, 'fetch_history', side_effect=terminal):
                await m.generate_prompt_candidate(job)
            self.assertEqual(len(posts), 1)
            self.assertEqual(job['status'], 'prompt_failed')
            self.assertIn('Ambiguous', job['error'])
            self.assertIsNone(job['final_h3_prompt'])
            self.assertIsNone(m.pick_next_render_job())

    async def test_lora_slot_duplicates_and_wrong_type_reject_before_patch(self):
        with load_controller() as m:
            for mutation in ('duplicate','wrong_type'):
                ui = json.loads(m.MASTER.read_text()); node = next(n for n in ui['nodes'] if n['id'] == 6164)
                if mutation == 'duplicate':
                    node['widgets_values_named']['lora_8'] = copy.deepcopy(node['widgets_values_named']['lora_1'])
                    node['widgets_values'].append(copy.deepcopy(node['widgets_values_named']['lora_1']))
                else: node['type'] = 'LoraLoader'
                path = m.STATE / 'bad-lora.json'; path.write_text(json.dumps(ui))
                job = fixture_job(m); name = m.MAP['nodes']['first_pass_loras']['entries'][0]['lora']
                job['context']['loras'] = [dict(name=name, enabled=True, strength=0.7)]
                with self.assertRaises(ValueError): m.patch_workflow(job, path, approved_prompt=APPROVED)
