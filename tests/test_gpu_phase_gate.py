"""Real OS leases and native queue hooks; no CUDA/model/provider activity."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'h3/custom_nodes/aj_production/gpu_gate.py'
spec=importlib.util.spec_from_file_location('_cpu_gpu_gate',SOURCE)
gate_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate_module)


class GPUPhaseGateTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'gpu'
        self.render=gate_module.GPUGate(self.path,measure=lambda:65536)
        self.addCleanup(self.render.close)
        self.render.start_renderer()
        self.prompt=gate_module.GPUGate(self.path,measure=lambda:65536)
        self.addCleanup(self.prompt.close)

    def test_sampling_requires_fresh_measurement_and_exact_configured_threshold(self):
        self.render.transition('sampling')
        for free in (None,True,-1,40959):
            with patch.object(self.prompt,'measure',return_value=free):
                with self.assertRaises(TimeoutError):
                    with self.prompt.prompt(40960,True,.08):self.fail('admitted unsafe LLM')
        with patch.object(self.prompt,'measure',return_value=40960):
            with self.prompt.prompt(40960,True,1):pass
        for headroom,allow in ((0,True),(True,True),(1,1)):
            with self.assertRaises(ValueError):
                with self.prompt.prompt(headroom,allow,1):pass

    def test_all_closed_render_phases_pause_new_stages(self):
        for phase in ('conditioning','decoding','recovery','unknown','unrecognized'):
            self.render.transition(phase)
            with self.assertRaises(TimeoutError):
                with self.prompt.prompt(1,True,.07):self.fail('admitted in '+phase)
        self.render.transition('sampling')
        with self.assertRaises(TimeoutError):
            with self.prompt.prompt(1,False,.07):self.fail('cold analysis overlap')
        self.render.transition('idle')
        with self.prompt.prompt(1,False,1):pass

    def test_idle_unknown_gpu_measurement_also_pauses(self):
        self.render.transition('idle')
        with patch.object(self.prompt,'measure',return_value=None):
            with self.assertRaises(TimeoutError):
                with self.prompt.prompt(1,True,.08):self.fail('GPU unknown')

    def test_render_closes_admission_before_waiting_for_current_llm_to_reap(self):
        self.render.transition('sampling')
        complete=threading.Event();errors=[]
        def decode():
            try:self.render.transition('decoding');complete.set()
            except BaseException as error:errors.append(error)
        with self.prompt.prompt(24576,True,1):
            thread=threading.Thread(target=decode);thread.start()
            deadline=time.monotonic()+1
            while self.prompt._read()['phase']!='decoding' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(self.prompt._read()['phase'],'decoding')
            self.assertFalse(complete.is_set())
            second=gate_module.GPUGate(self.path,measure=lambda:65536)
            try:
                with self.assertRaises(TimeoutError):
                    with second.prompt(1,True,.08):self.fail('render priority lost')
            finally:second.close()
        thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertTrue(complete.is_set())

    def test_renderer_ownership_and_stale_heartbeat_fail_closed(self):
        other=gate_module.GPUGate(self.path,measure=lambda:65536)
        try:
            with self.assertRaises(BlockingIOError):other.start_renderer()
        finally:other.close()
        self.render.transition('sampling')
        self.render._closed.set();self.render._heartbeat.join(2)
        state=json.loads((self.path/'state.json').read_text());state['at']=time.monotonic()-10
        (self.path/'state.json').write_text(json.dumps(state))
        with self.assertRaises(TimeoutError):
            with self.prompt.prompt(1,True,.08):pass
        state['at']=time.monotonic();state['start']='reused-pid'
        (self.path/'state.json').write_text(json.dumps(state))
        with self.assertRaises(TimeoutError):
            with self.prompt.prompt(1,True,.08):pass

    def test_fresh_state_without_renderer_ownership_and_zombie_pid_is_unknown(self):
        self.render.transition('sampling')
        self.render._closed.set();self.render._heartbeat.join(2)
        import os
        os.close(self.render.owner_lease);self.render.owner_lease=None
        with self.assertRaises(TimeoutError):
            with self.prompt.prompt(1,True,.08):pass
        stat_line='123 (worker) Z '+' '.join(['0']*18+['12345'])
        with patch.object(gate_module.Path,'read_text',return_value=stat_line):
            with self.assertRaisesRegex(ValueError,'no longer alive'):gate_module.process_start(123)

    def test_missing_malformed_or_nonfinite_state_never_opens_sampling(self):
        self.render._closed.set();self.render._heartbeat.join(2)
        self.render.transition('sampling')
        for value in ('null','[]','{"phase":"sampling","pid":true,"at":NaN}'):
            (self.path/'state.json').write_text(value)
            with self.assertRaises(TimeoutError):
                with self.prompt.prompt(1,True,.08):pass
        (self.path/'state.json').unlink()
        with self.assertRaises(TimeoutError):
            with self.prompt.prompt(1,True,.08):pass

    def test_wait_remains_interruptible_and_does_not_invoke_inference(self):
        calls=[]
        def interrupted():calls.append('checked');raise RuntimeError('STOP')
        with patch.object(self.prompt,'check',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'STOP'):
                with self.prompt.prompt(1,True,1):self.fail('inference began')
        self.assertEqual(calls,['checked'])

    def test_symlink_and_hardlink_coordination_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            outside=Path(directory)/'outside';outside.mkdir()
            alias=Path(directory)/'alias';alias.symlink_to(outside,target_is_directory=True)
            with self.assertRaises(OSError):gate_module.GPUGate(alias/'gpu')
            self.assertFalse((outside/'gpu').exists())
        self.render._closed.set();self.render._heartbeat.join(2)
        target=self.path/'state.json';target.unlink();target.symlink_to(self.path/'state.lock')
        with self.assertRaises(ValueError):self.render.transition('idle')
        target.unlink()
        import os
        os.link(self.path/'state.lock',self.path/'state.json')
        with self.assertRaises(ValueError):self.render.transition('idle')

    def test_cross_process_inference_lease_excludes_decoder_until_child_exit(self):
        self.render.transition('sampling')
        code='''import importlib.util,sys
s=importlib.util.spec_from_file_location('gate',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
g=m.GPUGate(sys.argv[2],measure=lambda:65536)
with g.prompt(24576,True,2):
 print('HELD',flush=True)
 sys.stdin.readline()
g.close()
'''
        child=subprocess.Popen([sys.executable,'-c',code,str(SOURCE),str(self.path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            import select
            self.assertTrue(select.select([child.stdout],[],[],3)[0])
            self.assertEqual(child.stdout.readline().strip(),'HELD')
            completed=threading.Event()
            thread=threading.Thread(target=lambda:(self.render.transition('decoding'),completed.set()))
            thread.start();self.assertFalse(completed.wait(.1))
            child.stdin.write('release\n');child.stdin.flush()
            child.communicate(timeout=3)
            thread.join(3)
            self.assertEqual(child.returncode,0);self.assertTrue(completed.is_set());self.assertFalse(thread.is_alive())
        finally:
            if child.poll() is None:child.kill()
            child.communicate(timeout=3)

    def test_vram_probe_rejects_multiple_gpus_errors_and_unparseable_output(self):
        for output,expected in [('40960\n',40960),('40960\n40960\n',None),('N/A\n',None),('-1\n',None)]:
            with patch.object(gate_module.subprocess,'check_output',return_value=output):
                self.assertEqual(gate_module.measured_free_mb(),expected)
        with patch.object(gate_module.subprocess,'check_output',side_effect=subprocess.TimeoutExpired('CPU fixture',2)):
            self.assertIsNone(gate_module.measured_free_mb())


class NativePhaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tests.test_production_nodes as node_tests
        node_tests.ProductionNodeTests.setUp(self)

    def test_native_queue_closes_before_conditioning_and_decode_and_keeps_durable_order(self):
        from tests.fixtures.pinned_comfy_prompt_queue import PromptQueue
        events=[]
        server=sys.modules['server'].PromptServer.instance
        server.queue_updated=lambda:None
        server.send_sync=lambda event,data,sid=None:events.append(('send',event))
        server.prompt_queue=PromptQueue(server)
        with tempfile.TemporaryDirectory() as directory:
            journal=self.module.ExecutionJournal(server.prompt_queue,Path(directory)/'results')
            runtime=sys.modules[self.module.__name__+'.gpu_runtime']
            fake=types.SimpleNamespace(start_renderer=lambda:events.append('start'),transition=lambda phase:events.append(phase))
            original=journal.write
            def write(*args):events.append('durable');return original(*args)
            with patch.object(runtime,'gate',return_value=fake),patch.object(journal,'write',side_effect=write):
                runtime.install_renderer(server,8188)
                import uuid
                graph={'sampler':{'class_type':'SamplerCustomAdvanced'},'decoder':{'class_type':'VAEDecode'}}
                server.prompt_queue.put((0,str(uuid.uuid4()),graph,{},['decoder']))
                item,index=server.prompt_queue.get(timeout=.01)
                self.assertEqual(events[:3],['start','idle','conditioning'])
                server.send_sync('executing',{'node':'sampler'})
                self.assertEqual(events[-2:],[ 'unknown',('send','executing')])
                self.assertNotIn('sampling',events)
                server.send_sync('progress',{'node':'unrecognized','value':1})
                self.assertNotIn('sampling',events)
                server.send_sync('progress',{'node':'sampler','value':1})
                server.send_sync('executing',{'node':'decoder'})
                self.assertIn('sampling',events);self.assertEqual(events[-2:],[ 'decoding',('send','executing')])
                server.prompt_queue.task_done(index,{'outputs':{}},PromptQueue.ExecutionStatus('success',True,[]))
                self.assertEqual(events[-2:],['durable','idle'])
                server.prompt_queue.put((0,str(uuid.uuid4()),graph,{},['decoder']))
                _,index=server.prompt_queue.get(timeout=.01)
                server.prompt_queue.task_done(index,{'outputs':{}},PromptQueue.ExecutionStatus('error',False,[]))
                self.assertEqual(events[-2:],['durable','recovery'])
                count=len(events);server.prompt_queue.get(timeout=.01)
                self.assertNotIn('idle',events[count:])

    def test_gated_compiler_delegates_unchanged_native_contract_inside_lease(self):
        events=[]
        @contextmanager
        def lease(headroom,allow,timeout):
            events.append(('lease',headroom,allow,timeout));yield;events.append('released')
        values={'timeout_seconds':600,'reasoning':'on','mmproj':'none'}
        with patch.object(self.module,'prompt_lease',side_effect=lease),patch.object(self.module.AJCompilerTextProcessor,'generate',return_value=('FINAL','PRIVATE','')) as native:
            result=self.module.AJGatedCompilerTextProcessor().generate(aj_headroom_mb=40960,aj_allow_sampling=True,**values)
        self.assertEqual(result,('FINAL','PRIVATE',''));native.assert_called_once_with(**values)
        self.assertEqual(events,[('lease',40960,True,600),'released'])

    def test_native_renderer_gate_respects_configured_worker_ports(self):
        from tests.fixtures.pinned_comfy_prompt_queue import PromptQueue
        server=sys.modules['server'].PromptServer.instance
        server.queue_updated=lambda:None;server.send_sync=lambda *args:None
        server.prompt_queue=PromptQueue(server)
        runtime=sys.modules[self.module.__name__+'.gpu_runtime']
        events=[]
        fake=types.SimpleNamespace(start_renderer=lambda:events.append('started'),transition=lambda phase:None)
        with patch.dict('os.environ',{'RENDER_PORT':'9188','PROMPT_PORT':'9189'}),patch.object(runtime,'gate',return_value=fake):
            runtime.install_renderer(server,9189);self.assertEqual(events,[])
            runtime.install_renderer(server,9188);self.assertEqual(events,['started'])
        with patch.dict('os.environ',{'RENDER_PORT':'9188','PROMPT_PORT':'9188'}):
            with self.assertRaises(ValueError):runtime.install_renderer(server,9188)

    def test_missing_native_worker_queue_fails_closed_before_gpu_lease(self):
        runtime=sys.modules[self.module.__name__+'.gpu_runtime']
        with self.assertRaisesRegex(ValueError,'ownership is unavailable'):
            runtime.prompt_lease(24576,True,1)

    def test_native_child_keeps_lease_after_parent_releases_it(self):
        runtime=sys.modules[self.module.__name__+'.gpu_runtime']
        with tempfile.TemporaryDirectory() as directory:
            render=runtime.GPUGate(directory,measure=lambda:65536)
            prompt=runtime.GPUGate(directory,measure=lambda:65536)
            child=None;thread=None
            try:
                render.start_renderer();render.transition('sampling')
                with prompt.prompt(24576,True,1) as descriptor,runtime.native_lease(descriptor):
                    child=runtime.LeaseSubprocess(subprocess).Popen([sys.executable,'-c',
                        "import sys;print('HELD',flush=True);sys.stdin.readline()"],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    import select
                    self.assertTrue(select.select([child.stdout],[],[],3)[0])
                    self.assertEqual(child.stdout.readline().strip(),'HELD')
                self.assertIsNone(runtime.current_lease())
                complete=threading.Event()
                thread=threading.Thread(target=lambda:(render.transition('decoding'),complete.set()))
                thread.start()
                self.assertFalse(complete.wait(.1),'decoder started while native child still held GPU lease')
                child.stdin.write('finish\n');child.stdin.flush();child.communicate(timeout=3)
                thread.join(3)
                self.assertEqual(child.returncode,0);self.assertTrue(complete.is_set());self.assertFalse(thread.is_alive())
            finally:
                if child is not None:
                    if child.poll() is None:child.kill()
                    child.communicate(timeout=3)
                if thread is not None:thread.join(3)
                prompt.close();render.close()
