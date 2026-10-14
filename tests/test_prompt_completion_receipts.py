"""F2: exact pinned preparation, native receipts and real CPU HTTP children."""
import base64
import copy
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import tests.test_production_nodes as node_fixtures


def receipt(content='Complete model analysis', **detail):
    return {'choices':[{'index':0,'finish_reason':'stop','message':{
        'role':'assistant','content':content,'reasoning_content':'separate native reasoning'}}],
        '__verbose':{'stop':True,'stop_type':'eos','truncated':False,**detail}}


class PromptCompletionReceiptTests(unittest.TestCase):
    def setUp(self):
        node_fixtures.ProductionNodeTests.setUp(self)
        self.cli,self.native=node_fixtures.ProductionNodeTests.pinned_llm(self)
        self.compiler=sys.modules[self.module.__name__+'.compiler']
        self.runtime=sys.modules[self.module.__name__+'.gpu_runtime']
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.lease=os.open(self.root/'lease',os.O_RDWR|os.O_CREAT,0o600)
        self.addCleanup(os.close,self.lease)
        self.events=[]
        @contextmanager
        def gate(headroom,sampling,timeout):
            self.events.append((headroom,sampling,timeout));yield self.lease
        self.gate_patch=patch.object(self.module,'prompt_lease',side_effect=gate)
        self.gate_patch.start();self.addCleanup(self.gate_patch.stop)
        self.values=dict(model='CPUFixture.gguf',mmproj='none',system_prompt='none',prompt='Exact input',
            max_tokens=8192,temperature=.95,top_p=.9,top_k=20,repeat_penalty=1.03,
            ctx_size=24576,memory_mode='gpu_layers',n_gpu_layers=50,n_cpu_moe_layers=1,
            seed=202,timeout_seconds=4,reasoning='off',enable_processing=True,
            extra_args='--reasoning-format deepseek --reasoning-budget 0')
        self.capture=self.root/'capture.json'
        self.body=self.root/'receipt.json'
        self.server=self.root/'llama-server'
        self.server.write_text('''#!/usr/bin/env python3
import http.server,json,os,sys,time
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{}')
 def do_POST(self):
  payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  fd=int(os.environ['AJ_CPU_LEASE_FD'])
  with open(os.environ['AJ_CPU_CAPTURE'],'w') as stream:
   json.dump({'argv':sys.argv,'payload':payload,'pid':os.getpid(),'lease':os.readlink('/proc/self/fd/'+str(fd))},stream)
  if os.getenv('AJ_CPU_HANG'):time.sleep(10)
  with open(os.environ['AJ_CPU_RECEIPT']) as stream:body=json.load(stream)
  result=json.dumps(body).encode();self.send_response(200);self.end_headers();self.wfile.write(result)
http.server.HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
''')
        self.server.chmod(0o700)

    def invoke(self, body=None, **changes):
        self.body.write_text(json.dumps(receipt() if body is None else body))
        with patch.object(self.compiler,'verified_server',return_value=self.server), \
             patch.dict(os.environ,{'AJ_CPU_CAPTURE':str(self.capture),'AJ_CPU_RECEIPT':str(self.body),
                                    'AJ_CPU_LEASE_FD':str(self.lease)}):
            return self.module.AJPromptTextProcessor().generate(24576,True,**{**self.values,**changes})

    def assert_reaped(self):
        data=json.loads(self.capture.read_text())
        self.assertFalse(Path('/proc/'+str(data['pid'])).exists(),'native child survived completion/failure')
        self.assertIsNone(self.runtime.current_lease())
        return data

    def test_original_cli_length_exit_zero_regression_is_rejected_before_output(self):
        # The original adapter accepts this text tuple after exit 0. The owned
        # stage now ignores that lossy path and checks the real JSON boundary.
        body=receipt('Partial analysis',stop_type='limit')
        body['choices'][0]['finish_reason']='length'
        original_spawn=subprocess.Popen
        def spawn(command,**kwargs):
            if command[0]=='/CPUFixture/llama-cli':
                command=[sys.executable,'-c','print("Partial analysis")']
            return original_spawn(command,**kwargs)
        with patch.object(self.compiler.subprocess,'Popen',side_effect=spawn):
            with self.assertRaises(ValueError):self.invoke(body)
        self.assert_reaped()

    def test_missing_stop_evidence_stop_word_and_context_truncation_all_fail_closed(self):
        for detail in ({'stop':False},{'stop_type':'word'},{'truncated':True}):
            with self.subTest(detail=detail),self.assertRaises(ValueError):self.invoke(receipt(**detail))
            self.assert_reaped()
        body=receipt();body.pop('__verbose')
        with self.assertRaises(ValueError):self.invoke(body)
        self.assert_reaped()

    def test_empty_reasoning_only_wrong_channel_and_tool_result_never_become_final(self):
        for content in ('','   ',None):
            with self.subTest(content=content),self.assertRaises(ValueError):self.invoke(receipt(content))
            self.assert_reaped()
        for field,value in (('role','tool'),('tool_calls',[{'id':'unexpected'}]),('reasoning_content',None)):
            body=receipt();body['choices'][0]['message'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.invoke(body)
            self.assert_reaped()

    def test_native_eos_accepts_exact_final_and_keeps_reasoning_separate(self):
        final='Literal quoted <think> text and [ Prompt: 1 t/s | Generation: 2 t/s ] remain exact.'
        self.assertEqual(self.invoke(receipt(final)),(final,'separate native reasoning',''))
        data=self.assert_reaped()
        self.assertEqual(data['lease'],str(self.root/'lease'))
        self.assertEqual(self.events,[(24576,True,4)])
        payload=data['payload'];argv=data['argv']
        self.assertEqual(payload['messages'],[{'role':'user','content':'Exact input'+' '*501}])
        for key in ('max_tokens','temperature','top_p','top_k','repeat_penalty','seed'):
            self.assertEqual(payload[key],self.values[key])
        self.assertIs(payload['verbose'],True);self.assertIs(payload['stream'],False)
        for flag,value in (('-m','/CPUFixture.gguf'),('-c','24576'),('-n','8192'),('-ngl','50'),
                           ('--seed','202'),('--reasoning','off'),('--reasoning-budget','0')):
            self.assertEqual(argv[argv.index(flag)+1],value)
        self.assertIn('--no-context-shift',argv)

    def test_real_image_bytes_projector_system_prompt_and_order_survive_native_boundary(self):
        pictures=[]
        for index in range(6):
            path=self.root/f'image-{index}.png';path.write_bytes(b'CPU IMAGE '+bytes([index]));pictures.append(path)
        system=self.root/'system.txt';system.write_text('Exact system instruction')
        namespace=self.native.LLMTextProcessor.generate.__globals__
        with patch.dict(namespace,{'mmproj_options':lambda:['projector.gguf'],
                'full_mmproj_path':lambda _:Path('/exact-projector.gguf'),
                'system_prompt_options':lambda:['preset'], 'full_system_prompt_path':lambda _:system}), \
             patch.object(self.cli,'tensor_to_temp_pngs',return_value=pictures):
            self.assertEqual(self.invoke(mmproj='projector.gguf',system_prompt='preset',image=object())[0],receipt()['choices'][0]['message']['content'])
        data=self.assert_reaped();messages=data['payload']['messages'];argv=data['argv']
        self.assertEqual(messages[0],{'role':'system','content':'Exact system instruction'})
        parts=messages[1]['content']
        self.assertEqual([base64.b64decode(part['image_url']['url'].split(',',1)[1]) for part in parts[:-1]],
                         [b'CPU IMAGE '+bytes([i]) for i in range(6)])
        self.assertEqual(parts[-1],{'type':'text','text':'Exact input'+' '*501})
        self.assertEqual(argv[argv.index('--mmproj')+1],'/exact-projector.gguf')
        self.assertNotIn('--image',argv)
        self.assertTrue(all(not path.exists() for path in pictures),'native image temporaries leaked')

    def test_unknown_model_or_image_without_projector_is_refused_before_child(self):
        for changes in ({'model':'foreign.gguf'},{'image':object()}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.invoke(**changes)
        self.assertFalse(self.capture.exists())

    def test_text_stage_preserves_both_moe_placement_and_seed_normalization(self):
        self.invoke(memory_mode='gpu_and_cpu_moe_layers',seed=2**32+202)
        data=self.assert_reaped();argv=data['argv']
        self.assertEqual(argv[argv.index('--n-cpu-moe')+1],'1')
        self.assertEqual(argv[argv.index('-ngl')+1],'50')
        self.assertEqual(data['payload']['seed'],202)

    def test_timeout_interrupts_reaps_native_child_and_releases_lease_context(self):
        with patch.dict(os.environ,{'AJ_CPU_HANG':'1'}),self.assertRaises(TimeoutError):self.invoke(timeout_seconds=1)
        self.assert_reaped()

    def test_disabled_processing_cannot_publish_unproven_passthrough(self):
        with self.assertRaises(ValueError):self.invoke(enable_processing=False)
        self.assertFalse(self.capture.exists())

    def test_unproven_pre_fix_analysis_is_preserved_but_not_reused(self):
        from h3.app.analysis_cache import AnalysisCache,step0_key
        from tests.test_dual_llm_profiles import DualProfileTests
        fixture=DualProfileTests();fixture.setUpClass();job=fixture.job()
        context=job['context']
        legacy={'baseline':context['workflow_baseline_sha256'],'workflow_id':context['workflow_id'],
                'step0_identity':context['profile_identity']['step0_identity'],
                'cache_contract':'v20_split_analysis_v1',
                **{key:context.get(key) for key in ('prompt','pictures','audio','duration_seconds')}}
        old_key=hashlib.sha256(json.dumps(legacy,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        record={'analysis':{'step0_key':old_key,'key':'old-key',
                            'texts':{'visual_facts':'old facts','expanded_intent':'old intent','reference_map':'old map'}}}
        before=copy.deepcopy(record)
        self.assertEqual(AnalysisCache().texts(job,record),{})
        self.assertEqual(record,before)
        self.assertNotEqual(step0_key(job),old_key)
