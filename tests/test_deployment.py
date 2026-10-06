"""CPU deployment behavior with disposable paths and inert system/GPU commands."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from h3.scripts import llama_runtime, patch_llama_binary, preflight, runtime_config


ROOT = Path(__file__).resolve().parents[1]
LLAMA_SOURCE = '''from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
import platform
LLAMA_CPP_RELEASE_TAG = "b10472"
@dataclass
class PlatformSpec:
    key: str
    cli_executable: str
    asset_patterns: tuple
    required_files: tuple
WINDOWS_CUDA_13 = PlatformSpec(
    "windows", "llama-cli.exe", (), ("llama-cli.exe",))
def _platform_spec():
    system=platform.system().lower(); machine=platform.machine().lower()
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return WINDOWS_CUDA_13
    raise RuntimeError("unsupported")
def _existing_install(spec):
    path=Path(__file__).parent/'vendor'/'llama.cpp'/LLAMA_CPP_RELEASE_TAG/spec.key/spec.cli_executable
    return SimpleNamespace(cli=path) if path.is_file() else None
def ensure_llama_cli_paths():
    raise AssertionError("a test must never automatically download binaries")
'''


def executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o700)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aj-deploy-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_runtime_credentials_roundtrip_and_retention_without_shell_evaluation(self):
        path = self.base / "runtime.env"
        sentinel = self.base / "must-not-exist"
        password = f"hasło with 'quotes' $HOME `touch {sentinel}` $(touch {sentinel})\nnext line"
        runtime_config.write_runtime(path, {"H3_PANEL_USER": "użytkownik", "H3_PANEL_PASSWORD": password})
        self.assertEqual(runtime_config.read_env(path)["H3_PANEL_PASSWORD"], password)
        output = subprocess.check_output(
            ["bash", "-c", 'set -a; source "$1"; "$2" -c "import os,json; print(json.dumps([os.environ[\'H3_PANEL_USER\'],os.environ[\'H3_PANEL_PASSWORD\']]))"',
             "test", str(path), sys.executable], text=True)
        self.assertEqual(json.loads(output), ["użytkownik", password])
        self.assertFalse(sentinel.exists())
        runtime_config.write_runtime(path, {})
        self.assertEqual(runtime_config.read_env(path)["H3_PANEL_PASSWORD"], password)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_default_password_is_retained_on_rerun(self):
        path = self.base / "runtime.env"
        runtime_config.write_runtime(path, {})
        first = runtime_config.read_env(path)["H3_PANEL_PASSWORD"]
        self.assertGreaterEqual(len(first), 24)
        runtime_config.write_runtime(path, {})
        self.assertEqual(runtime_config.read_env(path)["H3_PANEL_PASSWORD"], first)

    def test_runtime_parser_rejects_unquoted_shell_commands(self):
        path = self.base / "runtime.env"
        path.write_text("H3_PANEL_PASSWORD=two words\n")
        with self.assertRaises(ValueError):
            runtime_config.read_env(path)

    def test_explicit_interpreter_overrides_ambient_virtualenv_and_conda(self):
        environment = dict(os.environ, COMFY_PYTHON=sys.executable,
                           PYTHON_BIN="/not/the/python", VIRTUAL_ENV="/stale/venv", CONDA_PREFIX="/stale/conda")
        command = 'source "$1"; h3_select_python; "$COMFY_PYTHON" -c "import os,json,sys; print(json.dumps([sys.prefix,os.environ[\'VIRTUAL_ENV\'],os.environ[\'PYTHON_BIN\'],os.environ[\'COMFY_PYTHON\'],os.environ.get(\'CONDA_PREFIX\')]))"'
        values = json.loads(subprocess.check_output(["bash", "-c", command, "test", str(ROOT / "h3/scripts/python_env.sh")], env=environment, text=True))
        self.assertEqual(values[:2], [sys.prefix, sys.prefix])
        self.assertEqual(values[2:4], [str(Path(sys.prefix)/"bin/python")]*2)
        self.assertIsNone(values[4])

    def test_parallelism_is_bounded_by_cpu_memory_and_cap(self):
        self.assertEqual(runtime_config.build_parallelism("999", 128, 6*1024**3), 2)
        self.assertEqual(runtime_config.build_parallelism(None, 128, 100*1024**3), 8)
        self.assertEqual(runtime_config.build_parallelism(None, 2, 100*1024**3), 2)
        self.assertEqual(runtime_config.build_parallelism(None, 128, 1024), 1)
        with self.assertRaises(ValueError):
            runtime_config.build_parallelism("0", 8, 100*1024**3)

    def test_llama_patch_preserves_expected_release_and_is_idempotent(self):
        source = patch_llama_binary.patch_source(LLAMA_SOURCE)
        self.assertEqual(patch_llama_binary.patch_source(source), source)
        self.assertIn('LLAMA_CPP_RELEASE_TAG = "b10472"', source)
        self.assertIn('key="linux-x64-cuda"', source)
        with self.assertRaises(ValueError):
            patch_llama_binary.patch_source(LLAMA_SOURCE.replace('"b10472"', '"b8840"'))

    def llama_node(self, tag="b10472", correct_version=True):
        node = self.base / "llm-node"
        node.mkdir(exist_ok=True)
        (node/"llama_binary.py").write_text(patch_llama_binary.patch_source(LLAMA_SOURCE))
        binary = node/"vendor/llama.cpp"/tag/"linux-x64-cuda/llama-cli"
        commit = runtime_config.LLAMA_COMMIT[:7] if correct_version else "incorrect"
        executable(binary, f"#!/bin/sh\nprintf '%s\\n' 'version: 10472 ({commit})'\n")
        binary.with_suffix(".build.json").write_text(json.dumps({"tag": tag, "commit": runtime_config.LLAMA_COMMIT,
            "cuda": "13.0", "arch": "120", "sha256": llama_runtime.sha256(binary)}))
        return node, binary

    def test_runtime_finds_actual_pinned_llama_binary(self):
        node, binary = self.llama_node()
        self.assertEqual(llama_runtime.verify_llama(node), binary)

    def test_legacy_binary_does_not_false_pass_or_download(self):
        node, _ = self.llama_node(tag="b8840")
        with self.assertRaisesRegex(ValueError, "cannot find"):
            llama_runtime.verify_llama(node)

    def test_llama_checksum_and_actual_version_are_verified(self):
        node, binary = self.llama_node()
        binary.write_text(binary.read_text()+"# tampered\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            llama_runtime.verify_llama(node)
        node, _ = self.llama_node(correct_version=False)
        with self.assertRaisesRegex(ValueError, "different source commit"):
            llama_runtime.verify_llama(node)

    def test_llama_build_must_match_selected_cuda(self):
        node, _ = self.llama_node()
        with self.assertRaisesRegex(ValueError, "CUDA build"):
            llama_runtime.verify_llama(node, "12.8")

    def test_model_integrity_requires_publisher_checksum_and_format(self):
        path = self.base / "model.gguf"
        path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            preflight.validate_model(path, {})
        path.write_bytes(b"GGUF"+(3).to_bytes(4,"little")+bytes(28))
        with self.assertRaisesRegex(ValueError, "trusted SHA256"):
            preflight.validate_model(path, {})
        expected = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": 36}
        preflight.validate_model(path, expected)
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            preflight.validate_model(path, {"sha256": "f"*64})
        path.write_bytes(b"not a gguf"*4)
        with self.assertRaisesRegex(ValueError, "invalid GGUF"):
            preflight.validate_model(path, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def test_safetensors_payload_must_exist(self):
        path = self.base / "model.safetensors"
        header=json.dumps({"tensor":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}).encode()
        path.write_bytes(len(header).to_bytes(8,"little")+header+bytes(4))
        preflight.validate_model(path, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        path.write_bytes(path.read_bytes()[:-4])
        with self.assertRaisesRegex(ValueError, "invalid safetensors payload"):
            preflight.validate_model(path, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def test_preflight_uses_configurable_urls_and_ports(self):
        self.assertEqual(preflight.service_urls({"RENDER_PORT":"9188","PROMPT_PORT":"9189","H3_PANEL_PORT":"9860"}), {
            "render":"http://127.0.0.1:9188","prompt":"http://127.0.0.1:9189","panel":"http://127.0.0.1:9860"})
        self.assertEqual(preflight.service_urls({"PROMPT_COMFY_URL":"http://localhost:9999"})["prompt"], "http://localhost:9999")

    def test_gpu_readiness_rejects_wrong_runtime_hardware_and_memory(self):
        info={"cuda":"13.0","capability":[12,0],"name":"NVIDIA RTX PRO 6000 Blackwell","memory":96*1024**3}
        preflight.validate_gpu(info,"13.0")
        for key,value in [("cuda","12.6"),("capability",[8,9]),("name","GPU"),("memory",48*1024**3)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight.validate_gpu({**info,key:value},"13.0")

    def test_preflight_checks_all_prerequisites_without_real_service_requests(self):
        panel=self.base/'panel';comfy=self.base/'ComfyUI'
        (panel/'config').mkdir(parents=True)
        model=comfy/'models/LLM/model.gguf';model.parent.mkdir(parents=True)
        model.write_bytes(b'GGUF'+(3).to_bytes(4,'little')+bytes(28))
        manifest={'required_primary':[{'dir':'LLM','file':model.name}], 'loras_default_required':[]}
        (panel/'config/models_manifest.json').write_text(json.dumps(manifest))
        checksums=self.base/'checksums.json'
        checksums.write_text(json.dumps({'models/LLM/model.gguf':{'sha256':hashlib.sha256(model.read_bytes()).hexdigest()}}))
        environment={'PANEL_ROOT':str(panel),'COMFY_ROOT':str(comfy),'COMFY_PYTHON':sys.executable,
                     'H3_MODEL_CHECKSUMS_FILE':str(checksums),'RENDER_PORT':'9188','PROMPT_PORT':'9189','H3_PANEL_PORT':'9860'}
        def command(arguments, **kwargs):
            if arguments[0]=='git':return runtime_config.COMFY_COMMIT+'\n'
            if arguments[0]=='nvcc':return 'release 13.0, V13.0'
            if 'importlib.metadata' in arguments[-1]:return '1.21.0\n'
            self.assertIn("torch.ones(1, device='cuda')",arguments[-1])
            return json.dumps({'cuda':'13.0','name':'RTX PRO 6000 Blackwell','capability':[12,0],'memory':96*1024**3})
        info={name:{} for name in ['LLMTextProcessor','BunnyH3ConditioningBridge','MinimaxH3LatentUpscaler3D',
                                  'MergeImageBatchAndAudioList','Power Lora Loader (rgthree)','Seed (rgthree)']}
        info['batch']={}
        with patch.object(preflight.subprocess,'check_output',side_effect=command), \
             patch.object(preflight,'verify_llama') as verify, \
             patch.object(preflight,'get_json',return_value=info) as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preflight.run_checks(environment),[])
            verify.assert_called_once_with(comfy/'custom_nodes/ComfyUI-LLM-text-processor','13.0')
            self.assertEqual([call.args[0] for call in fetch.call_args_list],[
                'http://127.0.0.1:9188/object_info','http://127.0.0.1:9189/object_info','http://127.0.0.1:9860/api/config'])
            checksums.write_text('{}')
            self.assertTrue(any('no trusted SHA256' in error for error in preflight.run_checks(environment)))

    def inert_tools(self, package: Path, comfy: Path) -> dict[str,str]:
        """Stub pip, comfy-cli, git, nvcc, cmake; all writes stay in this tempfile."""
        prefix=self.base/"selected-env"
        fake_python=prefix/"bin/python"
        wrapper = f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
def record():
 with open(os.environ['TEST_COMMAND_LOG'],'a') as h:h.write(json.dumps(a)+'\\n')
if a[:2]==['-m','pip']:record();sys.exit(0)
if a[:2]==['-m','comfy_cli']:
 record()
 workspace=next(x.split('=',1)[1] for x in a if x.startswith('--workspace='))
 if 'install' in a:
  root=Path(workspace);root.mkdir(parents=True,exist_ok=True);(root/'main.py').write_text('# inert ComfyUI')
 if 'install-deps' in a and os.environ.get('TEST_DEPENDENCY_FAIL')=='1':sys.exit(7)
 sys.exit(0)
if a and a[0]=='-c' and 'print(sys.prefix)' in a[1]:print({str(prefix)!r});sys.exit(0)
if a and a[0]=='-c' and 'import torch' in a[1]:sys.exit(0)
if a[:2]==['-m','uvicorn']:
 import time
 Path(os.environ['TEST_RESTART_LOG']).write_text(json.dumps([os.environ['RENDER_RESTART_CMD'],os.environ['PROMPT_RESTART_CMD']]))
 time.sleep(15);sys.exit(0)
os.execv({sys.executable!r},[{sys.executable!r},*a])
'''
        executable(fake_python, wrapper)
        tools=self.base/"inert-bin";tools.mkdir(exist_ok=True)
        executable(tools/"git", f'''#!{sys.executable}
import os,sys
a=sys.argv[1:]
if 'rev-parse' in a:
 path=a[a.index('-C')+1]
 if path.endswith('ComfyUI-LLM-text-processor'):print({runtime_config.LLM_NODE_COMMIT!r})
 elif 'llama.cpp-' in path:print({runtime_config.LLAMA_COMMIT!r})
 else:print(os.environ.get('TEST_COMFY_COMMIT',{runtime_config.COMFY_COMMIT!r}))
''')
        executable(tools/"nvcc", "#!/bin/sh\nprintf '%s\\n' 'Cuda compilation tools, release 13.0, V13.0.0'\n")
        executable(tools/"curl", "#!/bin/sh\nprintf '401'\n")
        executable(tools/"cmake", f'''#!{sys.executable}
from pathlib import Path
import json,os,sys
a=sys.argv[1:]
with open(os.environ['TEST_CMAKE_LOG'],'a') as h:h.write(json.dumps(a)+'\\n')
if '--build' in a:
 binary=Path(a[a.index('--build')+1])/'bin/llama-cli';binary.parent.mkdir(parents=True,exist_ok=True)
 binary.write_text("#!/bin/sh\\nprintf '%s\\\\n' 'version: 10472 ({runtime_config.LLAMA_COMMIT[:7]})'\\n");binary.chmod(0o700)
''')
        return dict(os.environ, PATH=str(tools)+":"+os.environ["PATH"], COMFY_PYTHON=str(fake_python),
                    VIRTUAL_ENV="/stale/venv", CONDA_PREFIX="/stale/conda", PACKAGE_DIR=str(package),
                    PANEL_ROOT=str(package), COMFY_ROOT=str(comfy), WORKSPACE=str(self.base/"data"),
                    H3_SKIP_SYSTEM_PACKAGES="1", H3_INSTALL_VAST_CLI="0", H3_ONSTART_PATH=str(self.base/"onstart.sh"),
                    TEST_COMMAND_LOG=str(self.base/"commands.jsonl"), TEST_CMAKE_LOG=str(self.base/"cmake.jsonl"))

    def provision_fixture(self):
        package=self.base/"panel with spaces"
        shutil.copytree(ROOT/"h3",package,ignore=shutil.ignore_patterns('state','__pycache__','runtime.env'))
        for name in ('install_fallback_nodes.sh','setup_linux_llama.sh'):
            (package/"scripts"/name).write_text('#!/bin/sh\nexit 0\n')
        (package/"scripts/service_ctl.sh").write_text('#!/bin/sh\ntouch "$PANEL_ROOT/service-started"\n')
        comfy=self.base/"data/ComfyUI"
        return package,comfy,self.inert_tools(package,comfy)

    def test_provision_targets_comfy_root_one_python_and_retains_credentials(self):
        package,comfy,environment=self.provision_fixture()
        environment['H3_PANEL_PASSWORD']="test 'password' $HOME with spaces"
        command=['bash',str(package/'scripts/provision.sh')]
        first=subprocess.run(command,env=environment,text=True,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertNotIn(environment['H3_PANEL_PASSWORD'],first.stdout+first.stderr)
        commands=[json.loads(line) for line in (self.base/'commands.jsonl').read_text().splitlines()]
        cli=[command for command in commands if command[:2]==['-m','comfy_cli']]
        self.assertTrue(cli)
        self.assertTrue(all('--workspace='+str(comfy) in command for command in cli))
        self.assertNotIn('--restore',next(command for command in cli if 'install' in command))
        configured=runtime_config.read_env(package/'runtime.env')
        self.assertEqual(configured['COMFY_PYTHON'],str(self.base/'selected-env/bin/python'))
        self.assertEqual(configured['COMFY_PYTHON'],configured['PYTHON_BIN'])
        del environment['H3_PANEL_PASSWORD']
        second=subprocess.run(command,env=environment,text=True,capture_output=True)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(runtime_config.read_env(package/'runtime.env')['H3_PANEL_PASSWORD'],configured['H3_PANEL_PASSWORD'])
        commands=[json.loads(line) for line in (self.base/'commands.jsonl').read_text().splitlines()]
        self.assertTrue(any('--restore' in command for command in commands))

    def test_dependency_failure_aborts_before_startup(self):
        package,_,environment=self.provision_fixture()
        environment['TEST_DEPENDENCY_FAIL']='1'
        result=subprocess.run(['bash',str(package/'scripts/provision.sh')],env=environment,text=True,capture_output=True)
        self.assertEqual(result.returncode,7)
        self.assertFalse((package/'service-started').exists())

    def test_service_restart_commands_preserve_paths_with_spaces(self):
        package=self.base/'panel with spaces'
        shutil.copytree(ROOT/'h3',package,ignore=shutil.ignore_patterns('state','__pycache__','runtime.env'))
        comfy=self.base/'ComfyUI';comfy.mkdir()
        environment=self.inert_tools(package,comfy)
        environment['PYTHON_BIN']=environment['COMFY_PYTHON']
        environment['H3_PANEL_PASSWORD']='test-password'
        environment['TEST_RESTART_LOG']=str(self.base/'restart.json')
        command=['bash',str(package/'scripts/service_ctl.sh')]
        try:
            result=subprocess.run(command+['start','panel'],env=environment,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            restarts=json.loads(Path(environment['TEST_RESTART_LOG']).read_text())
            for service,restart in zip(('render','prompt'),restarts):
                self.assertEqual(shlex.split(restart),['bash',str(package/'scripts/service_ctl.sh'),'restart',service])
        finally:
            subprocess.run(command+['stop','panel'],env=environment,text=True,capture_output=True,timeout=25)
        self.assertFalse((package/'runtime.env').exists())

    def test_existing_wrong_comfy_commit_is_not_overwritten(self):
        package,comfy,environment=self.provision_fixture()
        comfy.mkdir(parents=True);(comfy/'main.py').write_text('user file')
        environment['TEST_COMFY_COMMIT']='wrong'
        result=subprocess.run(['bash',str(package/'scripts/provision.sh')],env=environment,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((comfy/'main.py').read_text(),'user file')
        self.assertFalse((package/'service-started').exists())

    def test_llama_setup_verifies_pinned_binary_and_reuses_bounded_build(self):
        package=self.base/'panel'
        shutil.copytree(ROOT/'h3',package,ignore=shutil.ignore_patterns('state','__pycache__','runtime.env'))
        comfy=self.base/'data/ComfyUI'
        environment=self.inert_tools(package,comfy)
        node=comfy/'custom_nodes/ComfyUI-LLM-text-processor';(node/'.git').mkdir(parents=True)
        (node/'llama_binary.py').write_text(LLAMA_SOURCE)
        build=self.base/'build'
        (build/f'llama.cpp-{runtime_config.LLAMA_TAG}/.git').mkdir(parents=True)
        environment.update(H3_BUILD_ROOT=str(build),H3_BUILD_JOBS='999')
        command=['bash',str(package/'scripts/setup_linux_llama.sh')]
        first=subprocess.run(command,env=environment,text=True,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        calls=[json.loads(line) for line in (self.base/'cmake.jsonl').read_text().splitlines()]
        compilation=next(call for call in calls if '--build' in call)
        self.assertLessEqual(int(compilation[compilation.index('--parallel')+1]),8)
        binary=llama_runtime.verify_llama(node)
        self.assertIn('/b10472/',str(binary))
        second=subprocess.run(command,env=environment,text=True,capture_output=True)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertIn('Reusing verified',second.stdout)
        self.assertEqual(len((self.base/'cmake.jsonl').read_text().splitlines()),len(calls))


if __name__ == '__main__':
    unittest.main()
