"""No-inference synthetic render-graph comparison; inspect one CustomCombo only."""
import asyncio,importlib,json,os,pathlib,sys
os.environ.update({'PANEL_ROOT':'/workspace/H3_VAST_MOBILE','COMFY_ROOT':'/workspace/ComfyUI',
                   'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin',
                   'CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'})
sys.path.insert(0,'/workspace/H3_VAST_MOBILE')
module=importlib.import_module('app.main')
smoke=importlib.import_module('scripts.smoke_test')
hooks=importlib.import_module('app.adapters.v20')
key='6036:6962:6950:6899'
def inspect(self,prompt):
 expected=self.adapter.descriptor['api']['output_chains']['nodes'][key]
 actual=prompt.get(key)
 print('EXPECTED',json.dumps(expected,ensure_ascii=True),flush=True)
 print('ACTUAL',json.dumps(actual,ensure_ascii=True),flush=True)
 raise RuntimeError('STOP_AFTER_COMBO_DIFF')
hooks.V20Hooks.validate_output_chains=inspect
async def check():
 old=list(module.queue)
 try:
  workflow=smoke.patched_workflows(module)[('h3_full','render')]
  await module._prepare_workflow_api(workflow,'render')
 except RuntimeError as e:
  if str(e)!='STOP_AFTER_COMBO_DIFF':raise
 finally:module.queue[:]=old
asyncio.run(check())
