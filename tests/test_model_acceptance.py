"""Isolated native acceptance: real CPU/files/processes, zero model downloads/GPU."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from h3.app.artifacts import validate_manifest
from h3.scripts import model_acceptance as acceptance, check_model_completion as completion_check
from h3.scripts import provision_models, runtime_config
from h3.scripts.runtime_config import read_env
from tests.test_deployment import LLAMA_SOURCE

ROOT = Path(__file__).resolve().parents[1]
BODY = b'GGUF' + struct.pack('<IQQ', 3, 1, 1) + bytes(24)


class AcceptanceModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name); self.root = self.base / 'acceptance'
        self.manifest = json.loads((ROOT/'h3/config/models_manifest.json').read_text())
        self.approval = json.loads((ROOT/'h3/config/model_acceptance.json').read_text())
        self.profiles = json.loads((ROOT/'h3/config/production_profiles.json').read_text())
        self.items = acceptance.select_artifacts(self.manifest, self.approval, self.profiles)

    def synthetic(self, body=BODY):
        manifest, approval = copy.deepcopy(self.manifest), copy.deepcopy(self.approval)
        for item in manifest['artifacts'] + approval['approved_artifacts']:
            if item['id'] in acceptance.APPROVED:
                item.update(size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        return acceptance.select_artifacts(manifest, approval, self.profiles), approval

    def prepare(self, approval=None):
        return acceptance.require_store(self.root, approval or self.approval, create=True)

    def write_models(self, items, body=BODY):
        for item in items:
            path = self.root/'models'/item['destination']; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)

    def test_exact_eleven_approved_models_cover_both_profiles_and_exclude_render_and_davidau(self):
        self.assertEqual({item['id'] for item in self.items}, acceptance.APPROVED)
        self.assertEqual(len(self.items), 11)
        self.assertTrue(all(item['provider']=='huggingface' and item['required'] and
                            item['provenance_status']=='verified_source_metadata' for item in self.items))
        self.assertFalse(any('DavidAU' in item['repository'] or item['provider']=='generated' for item in self.items))
        self.assertTrue(all(item['destination'].startswith('LLM/') for item in self.items))
        writer = next(item for item in self.items if item['id']=='h3-writer-replacement')
        self.assertEqual((writer['revision'], writer['size_bytes'], writer['sha256']),
            ('88cac1a5c19ac015402ec94884fabad4e3cfeaf2',9001753952,'4d529e77f0f1755de42e6f81d7c62309d5f39a4968049d817862d8a9263c317f'))

    def test_selected_model_pending_or_mutated_source_never_installs(self):
        for identifier in ('h3-analysis', 'analysis-mmproj', 'h3-writer-replacement', '10eros-writer-replacement'):
            for field in ('revision', 'repository', 'repository_path', 'destination', 'size_bytes', 'sha256', 'provenance_status'):
                with self.subTest(artifact=identifier,field=field):
                    manifest = copy.deepcopy(self.manifest); item = next(a for a in manifest['artifacts'] if a['id']==identifier)
                    item[field] = 1 if field=='size_bytes' else 'changed'
                    with self.assertRaises(ValueError):acceptance.select_artifacts(manifest,self.approval,self.profiles)
            manifest = copy.deepcopy(self.manifest); item = next(a for a in manifest['artifacts'] if a['id']==identifier)
            manifest['artifacts'].remove(item); manifest['pending_artifacts'].append(item)
            with self.assertRaises(ValueError):acceptance.select_artifacts(manifest,self.approval,self.profiles)

    def test_approval_cannot_expand_ids_select_old_writer_or_arm_inference(self):
        for change in ('extra_artifact','old_writer','submissions','automatic_inference','runtime'):
            approval = copy.deepcopy(self.approval)
            if change=='extra_artifact':approval['approved_artifacts'].append(next(a for a in self.manifest['artifacts'] if a['id']=='h3-writer'))
            elif change=='old_writer':approval['approved_artifacts'][-1] = next(a for a in self.manifest['artifacts'] if a['id']=='h3-writer')
            elif change=='runtime':approval['runtime']['llama_cpp_revision']='1'*40
            else:approval[change]=True
            with self.subTest(change=change),self.assertRaises(ValueError):acceptance.select_artifacts(self.manifest,approval,self.profiles)

    def test_any_pending_or_unfrozen_manifest_and_duplicate_sources_fail_closed(self):
        for change in ('unknown_pending','unfrozen','retired_gate','duplicate_id','duplicate_destination'):
            manifest = copy.deepcopy(self.manifest)
            if change=='unknown_pending':manifest['pending_artifacts'].append(dict(id='unknown',required=True))
            elif change=='unfrozen':manifest['status']='blocked_source_provenance'
            elif change=='retired_gate':manifest['pending_artifacts']=[dict(id='10eros-writer',required=False)]
            elif change=='duplicate_id':manifest['artifacts'].append(copy.deepcopy(manifest['artifacts'][0]))
            else:manifest['artifacts'][1]['destination']=manifest['artifacts'][0]['destination']
            with self.subTest(change=change),self.assertRaises(ValueError):acceptance.select_artifacts(manifest,self.approval,self.profiles)

    def test_profile_routes_projector_budget_and_runtime_are_bound_to_approval(self):
        for name in self.profiles['profiles']:
            for field in ('writer','analysis_model','analysis_mmproj','compiler','writer_stages'):
                profiles = copy.deepcopy(self.profiles)
                if field=='writer_stages':profiles['profiles'][name][field]['step4']['reasoning_budget']-=1
                else:profiles['profiles'][name][field]='changed'
                with self.subTest(profile=name,field=field),self.assertRaises(ValueError):acceptance.select_artifacts(self.manifest,self.approval,profiles)

    def test_plan_is_read_only_and_never_downloads_generates_or_starts_runtime(self):
        with patch.object(acceptance,'ensure_download',side_effect=AssertionError('download forbidden')), \
             patch.object(acceptance.subprocess,'run',side_effect=AssertionError('runtime forbidden')):
            result = acceptance.plan(self.root,self.items,self.approval)
        self.assertFalse(self.root.exists())
        self.assertEqual(result['known_model_bytes'],sum(item['size_bytes'] for item in self.items))
        self.assertEqual(result['minimum_free_bytes'],result['missing_download_bytes']+result['download_staging_bytes'])
        self.assertIs(result['submissions'],False); self.assertNotIn('production_gate_retired',result)

    def test_production_and_inherited_paths_or_symlink_ancestors_are_refused(self):
        production = self.base/'production'; production.mkdir()
        for root, env in ((production,{'COMFY_ROOT':str(production)}),
                          (production/'nested',{'COMFY_MODELS_DIR':str(production)}),
                          (self.base,{'PANEL_ROOT':str(production)}),
                          (self.base/'data',{'WORKSPACE':str(self.base/'data')})):
            with self.subTest(root=root),self.assertRaises(ValueError):acceptance.isolated_root(root,env)
        link = self.base/'linked'; link.symlink_to(production,target_is_directory=True)
        with self.assertRaises(ValueError):acceptance.isolated_root(link/'models',{})
        for env in ({'COMFY_ROOT':str(link)}, {'COMFY_ROOT':str(self.base/'other/../production')}):
            with self.subTest(env=env),self.assertRaises(ValueError):acceptance.isolated_root(production/'acceptance',env)

    def test_unrelated_directory_marker_and_shared_environment_lock_are_preserved(self):
        self.root.mkdir(mode=0o700); foreign=self.root/'foreign';foreign.write_text('preserve')
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(foreign.read_text(),'preserve');foreign.unlink()
        self.prepare(); marker=self.root/acceptance.MARKER
        data=json.loads(marker.read_text());data['submissions']=True;marker.write_text(json.dumps(data))
        with self.assertRaises(ValueError):acceptance.require_store(self.root,self.approval)
        data['submissions']=False;marker.write_text(json.dumps(data))
        target=self.base/'protected';target.write_text('untouched')
        os.link(target,self.root/'.environment.lock')
        with self.assertRaises(ValueError):acceptance.require_store(self.root,self.approval)
        self.assertEqual(target.read_text(),'untouched')

    def test_model_symlinks_hardlinks_and_linked_store_never_reuse_production_weights(self):
        self.prepare();item=self.items[0];target=self.base/'outside';target.write_bytes(BODY)
        model=self.root/'models'/item['destination'];model.parent.mkdir(parents=True)
        for kind in ('symlink','hardlink'):
            if kind=='symlink':model.symlink_to(target)
            else:os.link(target,model)
            with self.subTest(kind=kind),self.assertRaises(ValueError):acceptance.check_model_path(self.root,item)
            model.unlink()
        shutil.rmtree(self.root/'models');(self.root/'models').symlink_to(self.base/'outside-directory',target_is_directory=True)
        with self.assertRaises(ValueError):acceptance.plan(self.root,self.items,self.approval)

    def test_curated_downloads_preserve_gpu_pending_and_use_existing_integrity_downloader(self):
        items, approval=self.synthetic();self.prepare(approval);seen=[]
        def downloader(root,item):
            seen.append(item['id'])
            from h3.scripts.model_downloads import ensure_download
            def sdk(item,stage):
                path=stage/Path(item['repository_path']).name;path.write_bytes(BODY);return path
            return ensure_download(root,item,downloader=sdk)
        result=acceptance.download_models(self.root,items,approval,downloader=downloader)
        self.assertEqual(set(seen),acceptance.APPROVED)
        self.assertNotIn('production_gate_retired',result)
        self.assertEqual(result['gpu_acceptance'],'not_run')
        def reuse(root,item):
            from h3.scripts.model_downloads import ensure_download
            return ensure_download(root,item,downloader=Mock(side_effect=AssertionError('verified reuse must be offline')))
        again=acceptance.download_models(self.root,items,approval,downloader=reuse)
        self.assertTrue(all(item['reused'] for item in again['results']))
        validate_manifest(self.manifest)

    def test_insufficient_disk_and_wrong_existing_bytes_abort_without_network(self):
        self.prepare();download=Mock()
        with patch.object(acceptance.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
            with self.assertRaises(ValueError):acceptance.download_models(self.root,self.items,self.approval,downloader=download)
        download.assert_not_called()
        items,approval=self.synthetic();marker=self.root/acceptance.MARKER;marker.unlink();self.prepare(approval)
        self.write_models(items,b'wrong bytes')
        with self.assertRaises(ValueError):acceptance.download_models(self.root,items,approval)

    def native(self):
        node=self.root/'native/custom_nodes/ComfyUI-LLM-text-processor';node.mkdir(parents=True)
        (node/'.git').mkdir();(node/'llama_binary.py').write_text(acceptance.patch_source(LLAMA_SOURCE))
        vendor=node/'vendor/llama.cpp'/runtime_config.LLAMA_TAG/'linux-x64-cuda';vendor.mkdir(parents=True)
        for name in ('llama-cli','llama-server'):
            binary=vendor/name;binary.write_text(f"#!/bin/sh\nprintf '%s\\n' 'version: {runtime_config.LLAMA_COMMIT[:7]}'\n");binary.chmod(0o700)
            binary.with_suffix('.build.json').write_text(json.dumps({'commit':runtime_config.LLAMA_COMMIT,'tag':runtime_config.LLAMA_TAG,
                'cuda':'13.0','arch':'120','sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}))
        return node,vendor

    def test_verify_hashes_format_and_binaries_does_not_claim_gpu_acceptance(self):
        items,approval=self.synthetic();self.prepare(approval);self.write_models(items);node,vendor=self.native()
        with patch.object(acceptance,'verify_native_source'):
            result=acceptance.verify_models(self.root,items,approval)
        self.assertEqual(len(result['model_files']),11)
        for item, verified in zip(items, result['model_files']):
            self.assertEqual(verified['signature'], acceptance.signature((self.root/'models'/item['destination']).stat()))
        self.assertEqual(result['gpu_acceptance'],'not_run');self.assertNotIn('production_gate_retired',result)
        self.assertTrue(result['unverified'])
        server=vendor/'llama-server';server.write_text('tampered executable')
        with patch.object(acceptance,'verify_native_source'),self.assertRaises(ValueError):acceptance.verify_models(self.root,items,approval)

    def test_invalid_gguf_or_linked_binary_fails_before_native_execution(self):
        for body in (b'HTML'+BODY[4:],b'GGUF'+struct.pack('<IQQ',3,0,0)+bytes(24)):
            items,approval=self.synthetic(body)
            with tempfile.TemporaryDirectory() as tmp:
                self.root=Path(tmp)/'acceptance';self.prepare(approval);self.write_models(items,body)
                with patch.object(acceptance,'verify_llama') as runtime,self.assertRaises(ValueError):acceptance.verify_models(self.root,items,approval)
                runtime.assert_not_called()
        items,approval=self.synthetic();self.root=self.base/'linked-binary';self.prepare(approval);self.write_models(items)
        node,vendor=self.native();binary=vendor/'llama-cli';outside=self.base/'binary';binary.rename(outside);binary.symlink_to(outside)
        with patch.object(acceptance,'verify_native_source'),patch.object(acceptance,'verify_llama') as runtime,self.assertRaises(ValueError):
            acceptance.verify_models(self.root,items,approval)
        runtime.assert_not_called()

    def test_native_source_verification_accepts_only_exact_pin_and_owned_linux_patch(self):
        self.prepare();node,_=self.native()
        def output(command,**kwargs):
            if 'rev-parse' in command:return runtime_config.LLM_NODE_COMMIT+'\n'
            if 'config' in command:return 'https://github.com/KingManiya/ComfyUI-LLM-text-processor.git\n'
            return LLAMA_SOURCE
        with patch.object(acceptance.subprocess,'check_output',side_effect=output),patch.object(acceptance.subprocess,'run'):
            acceptance.verify_native_source(node)
            (node/'llama_binary.py').write_text(acceptance.patch_source(LLAMA_SOURCE)+'\nraise RuntimeError("local edit")\n')
            with self.assertRaises(ValueError):acceptance.verify_native_source(node)

    def test_normal_provisioning_stays_strict_even_after_acceptance_store_preparation(self):
        self.prepare()
        downloader=Mock(side_effect=AssertionError('normal production may not download'))
        manifest=copy.deepcopy(self.manifest);manifest['pending_artifacts']=[dict(id='unknown',required=True)]
        with patch.object(provision_models,'ensure_download',downloader),self.assertRaises(ValueError):
            provision_models.provision(manifest,self.base/'production-models',self.base/'toolchain')
        downloader.assert_not_called()
        self.assertEqual(self.manifest['pending_artifacts'],[])


class AcceptanceEntrypointTests(unittest.TestCase):
    def installation_fixture(self, base):
        package=base/'package';shutil.copytree(ROOT/'h3',package,ignore=shutil.ignore_patterns('state','runtime.env','__pycache__'))
        manifest=json.loads((package/'config/models_manifest.json').read_text())
        approval=json.loads((package/'config/model_acceptance.json').read_text())
        for item in manifest['artifacts']+approval['approved_artifacts']:
            if item['id'] in acceptance.APPROVED:item.update(size_bytes=len(BODY),sha256=hashlib.sha256(BODY).hexdigest())
        (package/'config/models_manifest.json').write_text(json.dumps(manifest))
        (package/'config/model_acceptance.json').write_text(json.dumps(approval))
        tools=base/'tools';tools.mkdir();root=base/'acceptance'
        from tests.test_deployment import executable
        executable(tools/'bootstrap',f'''#!{sys.executable}
import importlib.util,json,os,sys
from pathlib import Path
a=sys.argv[1:]
prefix=Path(__file__).parent.parent
def record():
 with open(os.environ['TEST_ACCEPT_COMMANDS'],'a') as stream:
  stream.write(json.dumps({{'args':a,'submissions':os.environ.get('H3_ALLOW_SUBMISSIONS'),
                          'token_present':bool(os.environ.get('HF_TOKEN')),
                          'private_token_present':bool(os.environ.get('AJ_ACCEPTANCE_DOWNLOAD_TOKEN')),
                          'prefix':str(prefix)}})+'\\n')
if a[:2]==['-m','venv']:
 record();target=Path(a[2])/'bin/python';target.parent.mkdir(parents=True,exist_ok=True)
 target.write_text(Path(__file__).read_text());target.chmod(0o700);sys.exit(0)
sys.prefix=str(prefix)
if a[:2]==['-m','pip']:record();sys.exit(0)
if a and a[0]=='-':
 sys.argv=['-',*a[1:]];exec(compile(sys.stdin.read(),'<fixture stdin>','exec'));sys.exit(0)
if a and a[0]=='-c':
 exec(a[1]);sys.exit(0)
if a and a[0].endswith('model_acceptance.py'):
 record();sys.path.insert(0,str(Path(a[0]).parent));spec=importlib.util.spec_from_file_location('acceptance_fixture',a[0]);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 if '--download' in a:
  original=module.download_models
  def tiny(root,item):
   from model_downloads import ensure_download
   def sdk(item,stage):
    path=stage/Path(item['repository_path']).name;path.write_bytes({BODY!r});return path
   return ensure_download(root,item,downloader=sdk)
  module.download_models=lambda root,items,approval:original(root,items,approval,downloader=tiny)
 sys.exit(module.main(a[1:]))
sys.argv=[a[0],*a[1:]]
sys.path.insert(0,str(Path(a[0]).parent))
exec(compile(Path(a[0]).read_text(),a[0],'exec'),{{'__name__':'__main__','__file__':a[0]}})
''')
        executable(tools/'git',f'''#!{sys.executable}
import os,sys
from pathlib import Path
a=sys.argv[1:]
if a and a[0]=='clone':
 path=Path(a[-1]);(path/'.git').mkdir(parents=True)
 if any(value.endswith('/ComfyUI-LLM-text-processor.git') for value in a):
  (path/'llama_binary.py').write_text({LLAMA_SOURCE!r});(path/'.git/fixture-llm').touch()
elif 'rev-parse' in a:
 print({runtime_config.LLM_NODE_COMMIT!r} if (Path(a[a.index('-C')+1])/'.git/fixture-llm').exists() else {runtime_config.LLAMA_COMMIT!r})
elif 'config' in a:print('https://github.com/KingManiya/ComfyUI-LLM-text-processor.git')
elif 'show' in a:sys.stdout.write({LLAMA_SOURCE!r})
if os.environ.get('TEST_ACCEPT_NODE_FAIL')=='1' and 'fetch' in a:sys.exit(7)
''')
        executable(tools/'nvcc',"#!/bin/sh\nprintf '%s\\n' 'Cuda compilation tools, release 13.0, V13.0.0'\n")
        executable(tools/'cmake',f'''#!{sys.executable}
import sys
from pathlib import Path
a=sys.argv[1:]
if '--build' in a:
 for target in a[a.index('--target')+1:]:
  binary=Path(a[a.index('--build')+1])/'bin'/target;binary.parent.mkdir(parents=True,exist_ok=True)
  binary.write_text("#!/bin/sh\\n[ \\\"$1\\\" = --version ] || exit 91\\nprintf '%s\\\\n' 'version: {runtime_config.LLAMA_COMMIT[:7]}'\\n");binary.chmod(0o700)
''')
        for name in ('vastai','curl','apt-get','comfy'):
            executable(tools/name,'#!/bin/sh\nexit 99\n')
        onstart=base/'onstart.sh';onstart.write_text('preserve production startup')
        env=dict(os.environ,PATH=str(tools)+':'+os.environ['PATH'],PYTHON_BIN=str(tools/'bootstrap'),
                 H3_MODEL_ACCEPTANCE_ROOT=str(root),H3_ALLOW_SUBMISSIONS='1',H3_INSTALL_VAST_CLI='1',
                 H3_ONSTART_PATH=str(onstart),TEST_ACCEPT_COMMANDS=str(base/'commands.jsonl'))
        return package,root,env

    def test_full_isolated_install_uses_curated_models_private_venvs_and_no_service_or_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);package,root,env=self.installation_fixture(base)
            token='FAKE_DOWNLOAD_ACCEPTANCE_EPHEMERAL'
            env.update(HF_TOKEN=token,HF_HUB_TOKEN=token,HUGGING_FACE_HUB_TOKEN=token,VAST_API_KEY=token,
                       AJ_ACCEPTANCE_DOWNLOAD_TOKEN='INHERITED_EXPORT_ATTRIBUTE_CPU_FIXTURE')
            result=subprocess.run(['bash',str(package/'INSTALL_MODEL_ACCEPTANCE.sh'),'--install'],env=env,text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(token,result.stdout+result.stderr)
            self.assertEqual((base/'onstart.sh').read_text(),'preserve production startup')
            self.assertFalse((package/'runtime.env').exists());self.assertFalse((root/'runtime.env').exists())
            commands=[json.loads(line) for line in (base/'commands.jsonl').read_text().splitlines()]
            self.assertTrue(commands);self.assertTrue(all(c['submissions']=='0' for c in commands))
            self.assertTrue(all('--download' in c['args'] for c in commands if c['token_present']))
            self.assertTrue(all(not c['private_token_present'] for c in commands))
            for command in commands:
                if command['args'][:2]==['-m','pip']:self.assertTrue(Path(command['prefix']).is_relative_to(root))
            receipts={p.stem for p in (root/'models/.verification').glob('*.json')}
            self.assertEqual(receipts,acceptance.APPROVED)
            self.assertFalse((root/'native/main.py').exists())
            self.assertTrue(all(json.loads(p.read_text())['gpu_validation']=='pending' for p in (root/'models/.verification').glob('*.json')))
            for p in base.rglob('*'):
                if p.is_file():self.assertNotIn(token.encode(),p.read_bytes(),str(p))
            validate=Mock()
            manifest=json.loads((package/'config/models_manifest.json').read_text())
            manifest['pending_artifacts']=[dict(id='unknown',required=True)]
            with patch.object(provision_models,'ensure_download',validate),self.assertRaises(ValueError):
                provision_models.provision(manifest,base/'production-models',base/'toolchain')
            validate.assert_not_called()

    def test_failed_runtime_prepare_never_downloads_models_or_changes_production_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);package,root,env=self.installation_fixture(base)
            env['TEST_ACCEPT_NODE_FAIL']='1'
            result=subprocess.run(['bash',str(package/'INSTALL_MODEL_ACCEPTANCE.sh'),'--install'],env=env,text=True,capture_output=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'models').exists())
            self.assertEqual((base/'onstart.sh').read_text(),'preserve production startup')
            marker=json.loads((root/acceptance.MARKER).read_text())
            self.assertIs(marker['submissions'],False);self.assertNotIn('production_gate_retired',marker)

    def test_interrupted_native_clone_can_retry_without_relabeling_or_starting_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);package,root,env=self.installation_fixture(base)
            env['TEST_ACCEPT_NODE_FAIL']='1'
            first=subprocess.run(['bash',str(package/'INSTALL_MODEL_ACCEPTANCE.sh'),'--install'],env=env,text=True,capture_output=True,timeout=20)
            self.assertNotEqual(first.returncode,0)
            self.assertFalse((root/'native/custom_nodes/ComfyUI-LLM-text-processor').exists())
            self.assertFalse(list((root/'native/custom_nodes').glob('.acceptance-node-*')))
            del env['TEST_ACCEPT_NODE_FAIL']
            second=subprocess.run(['bash',str(package/'INSTALL_MODEL_ACCEPTANCE.sh'),'--install'],env=env,text=True,capture_output=True,timeout=20)
            self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual((base/'onstart.sh').read_text(),'preserve production startup')

    def test_default_shell_plan_is_readonly_with_submission_override_and_ephemeral_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'acceptance';sentinel='FAKE_ACCEPTANCE_TOKEN_CPU'
            env=dict(os.environ,H3_MODEL_ACCEPTANCE_ROOT=str(root),PYTHON_BIN=sys.executable,HF_TOKEN=sentinel,
                     HF_HUB_TOKEN=sentinel,HUGGING_FACE_HUB_TOKEN=sentinel,VAST_API_KEY=sentinel,H3_ALLOW_SUBMISSIONS='1')
            result=subprocess.run(['bash',str(ROOT/'h3/INSTALL_MODEL_ACCEPTANCE.sh')],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            plan=json.loads(result.stdout);self.assertIs(plan['submissions'],False)
            self.assertFalse(root.exists());self.assertNotIn(sentinel,result.stdout+result.stderr)

    def test_unsafe_cli_options_and_normal_production_pending_never_install_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'acceptance'
            result=subprocess.run([sys.executable,str(ROOT/'h3/scripts/model_acceptance.py'),'--root',str(root),'--allow-pending'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(root.exists())
            result=subprocess.run([sys.executable,str(ROOT/'h3/scripts/provision_models.py'),'--manifest',str(ROOT/'h3/config/models_manifest.json'),
                '--models-root',str(root),'--plan'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0);self.assertFalse(root.exists())
            self.assertEqual(json.loads(result.stdout)['pending_artifacts'],[])


class AcceptanceCompletionTests(unittest.TestCase):
    def body(self, final=None):
        from tests.test_temporal_contract import FINAL
        return {'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
            'content':FINAL if final is None else final,'reasoning_content':'PRIVATE_TRACE'}}],
            '__verbose':{'stop':True,'stop_type':'eos','truncated':False}}

    def test_offline_receipt_checker_preserves_native_contract_without_attesting_gpu_or_retiring_gate(self):
        from tests.test_temporal_contract import CANONICAL
        result=completion_check.check(self.body(),CANONICAL,20)
        self.assertEqual((result['legal_frame_count'],result['effective_duration_seconds'],result['final_sections']),(481,20.04,6))
        self.assertEqual(result['gpu_acceptance'],'not_attested');self.assertNotIn('production_gate_retired',result)
        self.assertNotIn('PRIVATE_TRACE',json.dumps(result))

    def test_truncated_empty_reasoning_only_or_wrong_timeline_receipts_are_rejected(self):
        from tests.test_temporal_contract import CANONICAL,FINAL
        wrong=copy.deepcopy(self.body());wrong['__verbose']['truncated']=True
        for body in (wrong,self.body(''),self.body('[Start thinking]\nUnfinished'),
                     self.body(FINAL.replace('00:06.680','01:30.000'))):
            with self.assertRaises(ValueError):completion_check.check(body,CANONICAL,20)
