"""Profile routing and persisted split cache; synthetic text, zero inference."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from h3.app.analysis_cache import AnalysisCache, analysis_key, step0_key
from h3.app.v20 import all_nodes, compose, instruction_snapshot
from h3.app.workflow_adapters import get_adapter
from h3.app.temporal import duration_identity
import tests.test_production_architecture as architecture
import tests.test_production_api as api
import tests.test_production_controller as controller
from tests.test_production_controller import TEXTS

ROOT = Path(__file__).resolve().parents[1] / 'h3'


class DualProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        architecture.ProductionArchitectureTests.setUpClass()
        api.ProductionAPITests.setUpClass()
        cls.fixture = architecture.ProductionArchitectureTests()
        cls.profiles = cls.fixture.profiles

    def job(self, profile='h3_full'):
        return self.fixture.job(profile)

    def test_both_exact_quantizations_and_matching_vision_projector_preserve_instructions(self):
        for profile, quant, writer in [('h3_full','Q4_K_M','Qwen3-14B-heretic.Q4_K_M.gguf'),
                                      ('10eros_full','Q6_K','Qwen3.5-27B-heretic.Q5_K_M.gguf')]:
            policy = self.profiles['profiles'][profile]
            workflow = compose(ROOT/'workflows'/policy['render_template'], self.profiles, self.fixture.loras)
            nodes = {n['id']:n for n in all_nodes(workflow)}
            self.assertEqual(policy['writer'], writer)
            for key in (2441,2447):
                self.assertEqual(nodes[key]['widgets_values_named']['model'], f'Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.{quant}.gguf')
                self.assertEqual(nodes[key]['widgets_values_named']['mmproj'], policy['analysis_mmproj'])
                self.assertTrue(any(i['name']=='image' and i['link'] is not None for i in nodes[key]['inputs']))
            self.assertEqual(instruction_snapshot(workflow), instruction_snapshot(self.fixture.baseline))
            self.assertIn('F16',nodes[7386]['widgets_values_named']['model'])
            self.assertIn('V4.5.f16',nodes[7446]['widgets_values_named']['model'])
            self.assertIn('PHILADELPHIA-CLASS.f16',nodes[7450]['widgets_values_named']['model'])

    def test_cache_separates_quantization_and_reuses_step_zero_across_profiles(self):
        cache, saved = AnalysisCache(), {}
        h3, eros = self.job(), self.job('10eros_full')
        cache.record(h3,saved,TEXTS,execution_epoch=cache.epoch)
        self.assertEqual(step0_key(h3),step0_key(eros))
        self.assertNotEqual(analysis_key(h3),analysis_key(eros))
        self.assertEqual(cache.classify(eros,{} ,records=[saved]),'step0_text')
        self.assertEqual(cache.texts(eros,{},records=[saved]),{'visual_facts':TEXTS['visual_facts']})
        self.assertEqual(cache.classify(h3,saved),'cached_text')

    def test_cache_binds_source_revision_hash_projector_runtime_and_instructions(self):
        original = self.job()
        for field in ('revision','sha256','repository','repository_path','destination'):
            other=copy.deepcopy(original)
            other['context']['profile_identity']['analysis_identity']['models'][0][field]='changed'
            self.assertNotEqual(analysis_key(original),analysis_key(other))
            self.assertEqual(step0_key(original),step0_key(other))
        other=copy.deepcopy(original);other['context']['profile_identity']['analysis_identity']['runtime']['llama_cpp_revision']='changed'
        self.assertNotEqual(analysis_key(original),analysis_key(other))
        other=copy.deepcopy(original);other['context']['workflow_baseline_sha256']='different instructions'
        self.assertNotEqual(analysis_key(original),analysis_key(other))
        self.assertNotEqual(step0_key(original),step0_key(other))

    def test_writer_candidate_seed_lora_changes_do_not_invalidate_analysis(self):
        original=self.job();other=copy.deepcopy(original)
        other['prompt_seed']+=99
        other['context']['profile_identity']['writer']='another-writer.gguf'
        other['context']['loras']=[{'id':'movement-v1','enabled':False,'strength':0.2}]
        self.assertEqual(analysis_key(original),analysis_key(other))
        self.assertEqual(step0_key(original),step0_key(other))
        other['analysis_seed']+=1
        self.assertNotEqual(analysis_key(original),analysis_key(other))
        self.assertEqual(step0_key(original),step0_key(other))

    def test_candidates_two_to_ten_and_restart_use_identical_durable_analysis(self):
        cache,batch,first=AnalysisCache(),{},self.job()
        cache.record(first,batch,TEXTS,execution_epoch=cache.epoch)
        persisted=json.loads(json.dumps(batch));restarted=AnalysisCache()
        for index in range(2,11):
            candidate=copy.deepcopy(first);candidate.update(candidate_index=index,prompt_seed=100+index)
            self.assertEqual(restarted.classify(candidate,persisted),'durable_text')
            self.assertEqual(restarted.texts(candidate,persisted),TEXTS)
        restarted.observe_service('a'*32)
        self.assertEqual(restarted.texts(first,persisted),TEXTS)

    def test_unidentified_legacy_records_never_supply_analysis(self):
        cache,job=AnalysisCache(),self.job()
        self.assertEqual(cache.classify(job,{'analysis':{'key':analysis_key(job),'texts':TEXTS}}),'cold_analysis')
        for key in ('step0_identity','analysis_identity'):
            changed=copy.deepcopy(job);changed['context']['profile_identity'].pop(key)
            with self.assertRaisesRegex(ValueError,'identity missing'):cache.classify(changed,{})
        self.assertEqual(cache.classify(job,{'analysis':None}),'cold_analysis')

    def test_partial_step_zero_is_durable_and_cannot_erase_full_analysis(self):
        cache,batch,job=AnalysisCache(),{},self.job()
        cache.record(job,batch,{'visual_facts':'Partial visual facts'},execution_epoch=cache.epoch)
        self.assertEqual(cache.classify(job,batch),'step0_text')
        cache.record(job,batch,TEXTS,execution_epoch=cache.epoch)
        cache.record(job,batch,{'visual_facts':TEXTS['visual_facts']},execution_epoch=cache.epoch)
        self.assertEqual(cache.texts(job,batch),TEXTS)

    def test_partial_and_full_graphs_prune_exact_stages_and_gate_every_model(self):
        fixture=api.ProductionAPITests();adapter=get_adapter('h3_v20')
        for profile in self.profiles['profiles']:
            policy=self.profiles['profiles'][profile]
            context={**duration_identity(20),'profile_identity':policy}
            for texts,count in [({'visual_facts':TEXTS['visual_facts']},4),(TEXTS,2),(None,16)]:
                graph,_=adapter.install_runtime_guards(fixture.convert(profile,'prompt'),fixture.catalog,context,phase='prompt',analysis_texts=texts)
                adapter.validate_final_graph(graph,context,phase='prompt')
                processors=[node for node in graph.values() if node['class_type'] in {'AJPromptTextProcessor','AJGatedCompilerTextProcessor'}]
                self.assertEqual(len(processors),count)
                self.assertFalse(any(n['class_type'] in {'LLMTextProcessor','AJCompilerTextProcessor'} for n in graph.values()))
                if texts:
                    self.assertFalse(any('Step 0A' in n.get('_meta',{}).get('title','') and n['class_type']=='AJPromptTextProcessor' for n in graph.values()))
                for stage,key in adapter.descriptor['api']['writer_stages'].items():
                    self.assertEqual(graph[key]['inputs']['aj_headroom_mb'],policy['overlap_policy']['writer_headroom_mb'])
                    self.assertIs(graph[key]['inputs']['aj_allow_sampling'],True)
                damaged=copy.deepcopy(graph);damaged[adapter.descriptor['api']['writer_stages']['step3']]['inputs']['aj_headroom_mb']=1
                with self.assertRaisesRegex(ValueError,'stage policy'):adapter.validate_final_graph(damaged,context,phase='prompt')

    def test_pending_writer_and_davidau_retirement_remain_fail_closed(self):
        from h3.app.artifacts import validate_manifest
        manifest=json.loads((ROOT/'config/models_manifest.json').read_text())
        self.assertTrue(next(a for a in manifest['pending_artifacts'] if a['id']=='10eros-writer')['required'])
        pending=next(a for a in manifest['pending_artifacts'] if a['id']=='h3-writer-replacement')
        self.assertNotIn('revision',pending)
        self.assertNotIn('sha256',pending)
        self.assertNotIn('size_bytes',pending)
        with self.assertRaises(ValueError):validate_manifest(manifest)
        for id in ('h3-analysis','10eros-analysis','analysis-mmproj','10eros-writer-replacement'):
            self.assertIn(id,validate_manifest(manifest,allow_pending=True))


class DualProfileControllerTests(unittest.IsolatedAsyncioTestCase):
    setUp=controller.ProductionControllerTests.setUp
    batch=controller.ProductionControllerTests.batch
    warm=controller.ProductionControllerTests.warm
    transport=controller.ProductionControllerTests.transport

    async def test_ready_render_has_priority_before_any_new_prompt_boundary(self):
        job=await self.batch();self.warm(job)
        other=self.m.queue[1];other['status']='render_queued_auto'
        self.assertFalse(self.m.phase_admission(job,'prompt')[0])
        self.assertIsNone(self.m.pick_next_prompt_job())
        self.assertIs(self.m.pick_next_render_job(),other)

    async def test_different_profile_recovers_visual_facts_but_not_other_quant_analysis(self):
        first=await self.batch();self.warm(first)
        self.req=self.req.model_copy(update={'request_id':__import__('uuid').uuid4()})
        offset=len(self.m.queue);await self.batch('10eros_full');other=self.m.queue[offset]
        self.assertEqual(self.m.analysis_cache.classify(other,self.m.batch_for_job(other),records=self.m.batches),'step0_text')
        self.m.save_state();self.m.queue.clear();self.m.batches.clear();self.m.load_state()
        recovered=self.m.find_job(other['id'])
        self.assertEqual(self.m.analysis_cache.texts(recovered,self.m.batch_for_job(recovered),records=self.m.batches),{'visual_facts':TEXTS['visual_facts']})

    async def test_writer_failure_retains_completed_analysis_for_next_candidate(self):
        job=await self.batch();adapter=self.m.job_workflow_adapter(job)
        catalog=api.ProductionAPITests.catalog if hasattr(api.ProductionAPITests,'catalog') else __import__('tests.test_production_api',fromlist=['production_catalog']).production_catalog()
        job['active_service']='prompt'
        workflow=json.loads(self.m.patch_workflow(job,self.m.production_workflow(job)).read_text())
        from tests.test_workflows import exact_convert
        graph=adapter.prepare_api_prompt(exact_convert(workflow,catalog),catalog,phase='prompt',context=job['context'])
        graph,_=adapter.install_runtime_guards(graph,catalog,job['context'],phase='prompt')
        outputs={key:{'text':[TEXTS[field]]} for key,n in graph.items() for field,title in adapter.descriptor['api']['captures'].items() if field in TEXTS and n.get('_meta',{}).get('title')==title}
        entry={'prompt':graph,'outputs':outputs,'status':{'completed':True,'status_str':'error','messages':[]}}
        self.assertTrue(self.m._apply_history_outcome(job,'prompt',entry))
        self.assertEqual(job['status'],'prompt_failed')
        self.assertEqual(self.m.analysis_cache.texts(self.m.queue[1],self.m.batch_for_job(job)),TEXTS)
        self.m.save_state();self.m.queue.clear();self.m.batches.clear();self.m.load_state()
        self.assertEqual(self.m.analysis_cache.classify(self.m.queue[1],self.m.batches[0]),'durable_text')

    async def test_analysis_model_drift_blocks_recovery_instead_of_silent_relabel(self):
        job=await self.batch();self.m.PRODUCTION['profiles']['h3_full']['analysis_model']='another.Q4_K_M.gguf'
        with self.assertRaisesRegex(ValueError,'model routing changed'):self.m.job_profile(job)
