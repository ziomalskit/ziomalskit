"""No-inference synthetic render-graph comparison; inspect one CustomCombo only."""
import asyncio,importlib,json,os,pathlib,sys,site
site.addsitedir('/opt/aj/venv/lib/python3.12/site-packages')
os.environ.update({'PANEL_ROOT':'/workspace/H3_VAST_MOBILE','COMFY_ROOT':'/workspace/ComfyUI','COMFY_PYTHON':'/opt/aj/venv/bin/python',
                   'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin',
                   'CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'})
sys.path.insert(0,'/workspace/H3_VAST_MOBILE')
module=importlib.import_module('app.main')
smoke=importlib.import_module('scripts.smoke_test')
hooks=importlib.import_module('app.adapters.v20')
def inspect(self,prompt):
 nodes=self.adapter.descriptor['api']['output_chains']['nodes']
 mismatches=[]
 for key,expected in nodes.items():
  actual=prompt.get(key)
  if not isinstance(actual,dict) or actual.get('class_type')!=expected.get('class_type') or actual.get('inputs')!=expected.get('inputs'):
   mismatches.append(key)
   # Synthetic workflow only. Limit output to fixed per-node information.
   print('DIFF',key,'CLASS',expected.get('class_type'),'EXPECTED',json.dumps(expected.get('inputs'),ensure_ascii=True)[:750],
         'ACTUAL',json.dumps(actual.get('inputs') if isinstance(actual,dict) else None,ensure_ascii=True)[:750],flush=True)
 print('TOTAL_CHAIN_NODES',len(nodes),'MISMATCHES',len(mismatches),'IDS',mismatches,flush=True)
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
