"""Exercise AJ node contracts with CPU stubs, never import real CUDA modules."""
import asyncio
import importlib.util
import math
import hashlib
import json
import shlex
import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1] / "h3"


class ProductionNodeTests(unittest.TestCase):
    def test_owned_journal_installs_before_prompt_worker_can_cache_queue_methods(self):
        from tests.fixtures.pinned_comfy_prompt_queue import PromptQueue
        server = sys.modules['server'].PromptServer.instance
        server.prompt_queue = PromptQueue(types.SimpleNamespace(queue_updated=lambda:None))
        folder_paths = types.ModuleType('folder_paths')
        cli_args = types.ModuleType('comfy.cli_args');cli_args.args = types.SimpleNamespace(port=8189)
        with tempfile.TemporaryDirectory() as directory:
            folder_paths.get_output_directory = lambda:directory
            with patch.dict(sys.modules, {'folder_paths':folder_paths, 'comfy.cli_args':cli_args}):
                name = '_aj_eager_journal_' + uuid.uuid4().hex
                spec = importlib.util.spec_from_file_location(name, ROOT / 'custom_nodes/aj_production/__init__.py')
                module = importlib.util.module_from_spec(spec);sys.modules[name] = module
                self.addCleanup(sys.modules.pop, name, None)
                spec.loader.exec_module(module)
                self.assertIs(module._journal.queue, server.prompt_queue)
                self.assertEqual(module._journal.directory, Path(directory) / '.aj-results/8189')
                self.assertEqual(server.prompt_queue.task_done.__module__, name + '.execution_journal')

    def setUp(self):
        self.events = []
        memory = types.ModuleType("comfy.model_management")
        memory.unload_all_models = lambda: self.events.append("unload")
        memory.soft_empty_cache = lambda: self.events.append("empty")
        comfy = types.ModuleType("comfy")
        comfy.model_management = memory
        nodes = types.ModuleType("nodes")
        events = self.events
        class Loader:
            @classmethod
            def INPUT_TYPES(cls):
                return {"required": {"unet_name": (["CPUFixture"],), "weight_dtype": (["default"],)}}
            def load_unet(self, unet_name, weight_dtype):
                events.append(("load", unet_name, weight_dtype))
                return ("CPU model sentinel",)
        nodes.UNETLoader = Loader
        self.routes = {}
        class Routes:
            def post(_self, path):
                return _self.get(path)
            def get(_self, path):
                def register(function):
                    self.routes[path] = function
                    return function
                return register
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes()))
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.web = types.SimpleNamespace(json_response=lambda data: data)
        modules = {"comfy": comfy, "comfy.model_management": memory, "nodes": nodes, "server": server, "aiohttp": aiohttp}
        self.context = patch.dict(sys.modules, modules)
        self.context.start()
        self.addCleanup(self.context.stop)
        spec = importlib.util.spec_from_file_location("_aj_cpu_node_" + uuid.uuid4().hex, ROOT / "custom_nodes/aj_production/__init__.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(self.module)

    def test_conditioning_boundary_releases_before_late_transformer_load(self):
        first, second = object(), object()
        result = self.module.AJConditioningBoundary().release(first, second, True)
        self.assertEqual(result, (first, second, 1))
        self.assertEqual(self.events, ["unload", "empty"])
        self.module.AJLateUNETLoader().load_after_conditioning("CPUFixture", "default", result[2])
        self.assertEqual(self.events[-1], ("load", "CPUFixture", "default"))
        self.assertTrue(math.isnan(self.module.AJConditioningBoundary.IS_CHANGED()))
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.module.AJLateUNETLoader().load_after_conditioning("CPUFixture", "default", 0)
        self.assertEqual(len(self.events), 3)

    def test_video_boundary_releases_once_and_retains_output_object(self):
        video = object()
        self.assertEqual(self.module.AJVideoBoundary().release(video, True), (video,))
        self.assertEqual(self.events, ["unload", "empty"])
        self.assertTrue(math.isnan(self.module.AJVideoBoundary.IS_CHANGED()))
        self.module.AJVideoBoundary().release(video, False)
        self.assertEqual(len(self.events), 2)

    def test_analysis_text_and_epoch_endpoint_have_no_model_or_mutation_side_effect(self):
        text = "exact durable Step 0–2 analysis"
        self.assertEqual(self.module.AJAnalysisText().value(text), (text,))
        before = self.module.SERVICE_EPOCH
        self.assertEqual(asyncio.run(self.routes["/aj/service_epoch"](None)), {"service_epoch": before})
        self.assertEqual(self.module.SERVICE_EPOCH, before)
        self.assertEqual(len(before), 32)
        self.assertEqual(self.events, [])

    def pinned_llm(self):
        """Import verbatim pinned Python against CPU stubs, not a fake node."""
        fixture = ROOT.parent / "tests/fixtures/pinned_llm"
        name = "_aj_pinned_llm_" + uuid.uuid4().hex
        package = types.ModuleType(name)
        package.__path__ = [str(fixture)]
        registry = types.ModuleType(name + ".folder_registry")
        registry.NO_MMPROJ = "none"
        registry.model_options = lambda: ["CPUFixture.gguf"]
        registry.mmproj_options = registry.system_prompt_options = lambda: ["none"]
        registry.full_model_path = lambda _: Path("/CPUFixture.gguf")
        registry.full_mmproj_path = registry.full_system_prompt_path = lambda _: None
        binary = types.ModuleType(name + ".llama_binary")
        binary.ensure_llama_cli_paths = lambda: types.SimpleNamespace(cli=Path("/CPUFixture/llama-cli"))
        for key, value in ((name,package),(registry.__name__,registry),(binary.__name__,binary)):
            sys.modules[key]=value
            self.addCleanup(sys.modules.pop,key,None)
        memory = sys.modules["comfy.model_management"]
        memory.processing_interrupted = lambda: False
        for filename in ("llama_cli", "nodes"):
            spec=importlib.util.spec_from_file_location(name+"."+filename,fixture/(filename+".py"))
            module=importlib.util.module_from_spec(spec)
            sys.modules[spec.name]=module
            self.addCleanup(sys.modules.pop,spec.name,None)
            spec.loader.exec_module(module)
        sys.modules["nodes"].NODE_CLASS_MAPPINGS=module.NODE_CLASS_MAPPINGS
        return sys.modules[name+".llama_cli"],module

    def compiler_values(self):
        return dict(model="CPUFixture.gguf",mmproj="none",system_prompt="none",prompt="Exact v20 fixture instruction.",
                    max_tokens=12352,temperature=.8,top_p=.95,top_k=64,repeat_penalty=1.05,ctx_size=29184,
                    memory_mode="gpu_layers",n_gpu_layers=50,n_cpu_moe_layers=1,seed=202,timeout_seconds=600,
                    reasoning="on",enable_processing=True,extra_args="--reasoning-format deepseek --reasoning-budget 4096")

    def test_pinned_llm_fixture_has_exact_authoritative_source_hashes(self):
        fixture=ROOT.parent/"tests/fixtures/pinned_llm"
        provenance=json.loads((fixture/"PROVENANCE.json").read_text())
        self.assertEqual(provenance["revision"],"65983de33681816f856db7e16767da906084c688")
        for name,metadata in provenance["files"].items():
            body=(fixture/name).read_bytes()
            self.assertEqual(len(body),metadata["size_bytes"])
            self.assertEqual(hashlib.sha256(body).hexdigest(),metadata["sha256"])

    def test_real_pinned_command_and_native_file_keep_reasoning_out_of_final(self):
        from tests.test_temporal_contract import FINAL
        cli,_=self.pinned_llm()
        from unittest.mock import MagicMock
        compiler=sys.modules[self.module.__name__+'.compiler']
        commands=[]
        processes=[]
        def spawn(command,**kwargs):
            commands.append(command)
            self.assertIs(kwargs['shell'],False)
            process=types.SimpleNamespace(poll=lambda:None,terminate=lambda:processes.append('terminate'),wait=lambda **_:0)
            return process
        response=MagicMock();response.__enter__.return_value.status=200
        opener=MagicMock();opener.open.return_value=response
        def completion(url,payload,timeout):
            self.assertEqual(payload['messages'][0]['content'],self.compiler_values()['prompt']+' '*501)
            self.assertIs(payload['stream'],False)
            return {'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':FINAL,'reasoning_content':'INTERNAL_ONLY_REASONING'}}],
                    '__verbose':{'stop':True,'stop_type':'eos','truncated':False}}
        with patch.object(compiler,'verified_server',return_value=Path('/CPUFixture/llama-server')), \
             patch.object(compiler.subprocess,'Popen',side_effect=spawn), \
             patch.object(compiler.urllib.request,'build_opener',return_value=opener), \
             patch.object(compiler,'post_completion',side_effect=completion):
            final,reasoning,perf=self.module.AJCompilerTextProcessor().generate(**self.compiler_values())
        self.assertEqual(final,FINAL)
        self.assertEqual(reasoning,"INTERNAL_ONLY_REASONING")
        self.assertNotIn("INTERNAL_ONLY_REASONING",final)
        self.assertNotIn("STDERR_ONLY",final)
        self.assertEqual(perf,"")
        command=commands[0]
        for flag,value in (("--reasoning","on"),("--reasoning-budget","4096"),("-n","12352"),("-c","29184"),("--seed","202")):
            self.assertEqual(command[command.index(flag)+1],value)
        self.assertNotIn("--mmproj",command)
        self.assertEqual(processes,['terminate'])
        self.assertNotIn('-f',command)
        self.assertEqual(command[0],'/CPUFixture/llama-server')

    def test_native_file_preserves_quoted_think_prompt_echo_and_timing_text(self):
        from tests.test_temporal_contract import FINAL
        final=FINAL.replace("Natural sounds.",'Quoted "<think>example</think>", "[End thinking]", "... (truncated)", "[ Prompt: 1.0 t/s | Generation: 2.0 t/s ]" remain literal.')
        self.assertEqual(self.module.compiler_file_response("Assistant:\n[Start thinking]\n\nPrivate trace[End thinking]\n\n"+final+"\n\n", completion={'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':final}}],'__verbose':{'stop':True,'stop_type':'eos','truncated':False}})[0],final)

    def test_native_incomplete_nested_or_ambiguous_reasoning_fails_closed(self):
        from tests.test_temporal_contract import FINAL
        for body in ("Assistant:\n[Start thinking]\n\nUnfinished\n\n",
                     "Assistant:\n[Start thinking]\n\n[Start thinking]Nested[End thinking]\n\n"+FINAL+"\n\n",
                     "Assistant:\n[End thinking]\n\n"+FINAL+"\n\n",
                     "Assistant:\n[Start thinking]\n\nPrivate[End thinking]\n\n"+FINAL+"\n[End thinking]\n\n"):
            with self.assertRaises(ValueError):self.module.compiler_file_response(body, {'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':FINAL}}],'__verbose':{'stop':True,'stop_type':'eos','truncated':False}})

    def test_compiler_cannot_bypass_processing_or_select_vision(self):
        for changed in ({"enable_processing":False},{"reasoning":"off"},{"mmproj":"vision.gguf"},{"image":object()}):
            with self.assertRaises(ValueError):self.module.AJCompilerTextProcessor().generate(**dict(self.compiler_values(),**changed))

    def test_native_timeout_reaps_process_and_removes_all_temporary_files(self):
        cli,_=self.pinned_llm()
        compiler=sys.modules[self.module.__name__+'.compiler']
        stopped=[];temporary=[]
        original=cli.build_command
        def build(**values):
            command,paths=original(**values);temporary.extend(paths);return command,paths
        process=types.SimpleNamespace(poll=lambda:None,terminate=lambda:stopped.append('terminate'),wait=lambda **_:0)
        native=sys.modules['nodes'].NODE_CLASS_MAPPINGS['LLMTextProcessor']
        with patch.dict(native.generate.__globals__,{'build_command':build}), \
             patch.object(compiler,'verified_server',return_value=Path('/CPUFixture/llama-server')), \
             patch.object(compiler.subprocess,'Popen',return_value=process), \
             patch.object(compiler.time,'monotonic',side_effect=[0,601]):
            with self.assertRaises(TimeoutError):self.module.AJCompilerTextProcessor().generate(**self.compiler_values())
        self.assertEqual(stopped,['terminate'])
        self.assertTrue(temporary)
        self.assertTrue(all(not path.exists() for path in temporary))

    def test_actual_owned_guards_fix_ninety_seconds_and_bind_final_cuts(self):
        from tests.test_temporal_contract import RAW_NINETY,CANONICAL,FINAL
        plan=self.module.AJCreativeTimelineGuard().guard(RAW_NINETY,20.04)[0]
        self.assertEqual(plan,CANONICAL)
        wrong=FINAL.replace("00:06.680","01:30.000")
        self.assertEqual(self.module.AJFinalPromptTimeGuard().guard(wrong,plan,20.04),(FINAL,))
        self.assertEqual(self.module.AJFrozenDuration().value(481,20.04),(481,20.04))
        self.assertEqual(self.events,[])

    def test_owned_guard_rejects_inline_extra_cut_and_keeps_clear_quoted_reference(self):
        from tests.test_temporal_contract import CANONICAL, FINAL
        wrong = FINAL.replace('finish naturally through the final frame.', 'finish naturally. [Shot 4] At 01:30.000, cut again.')
        with self.assertRaises(ValueError):self.module.AJFinalPromptTimeGuard().guard(wrong, CANONICAL, 20.04)
        quoted = FINAL.replace('finish naturally through the final frame.', 'finish naturally. A sign reads "[Shot 4] At 01:30.000, a reference".')
        self.assertEqual(self.module.AJFinalPromptTimeGuard().guard(quoted, CANONICAL, 20.04), (quoted,))
