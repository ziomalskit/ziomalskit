"""Read-only live native/file identity comparison; never print prompts, assets or credentials."""
import collections,hashlib,json,pathlib,sys,site
site.addsitedir('/opt/aj/venv/lib/python3.12/site-packages')
sys.path.insert(0,'/workspace/H3_VAST_MOBILE')
from app.workflow_adapters import get_adapter
from app.native_identity import native_manifest
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
comfy=pathlib.Path('/workspace/ComfyUI')
data=json.loads((root/'state/queue.json').read_text())
jobs=data if isinstance(data,list) else data.get('queue',[])
print('QUEUE',len(jobs),dict(collections.Counter(j.get('status') for j in jobs)),flush=True)
adapter=get_adapter('h3_v20',root=root)
try:
 source=adapter.native_manifest()
 print('NATIVE_SOURCE',len(source),'names',sorted(source),flush=True)
except Exception as error:print('SOURCE_NATIVE_ERROR',type(error).__name__,str(error)[:300]);raise
try:
 deployed=native_manifest(comfy/'custom_nodes/aj_production',deployed=True)
 print('DEPLOYED_NATIVE',len(deployed),'MATCH',source==deployed,flush=True)
 print('NATIVE_DIFF_KEYS',[key for key in sorted(set(source)|set(deployed)) if source.get(key)!=deployed.get(key)],flush=True)
except Exception as error:print('DEPLOYED_NATIVE_ERROR',type(error).__name__,str(error)[:300])
identity=adapter._implementation_identity(source)
for j in sorted(jobs,key=lambda x:x.get('created_at') or 0)[-5:]:
 ctx=j.get('context') or {}
 mismatch=[key for key,value in identity.items() if ctx.get(key)!=value]
 print('LATEST_JOB',j.get('status'),j.get('profile'),'MISMATCH_FIELDS',mismatch,
       'FROZEN_ID_PREFIX',str(ctx.get('workflow_adapter_identity'))[:12],
       'CURRENT_ID_PREFIX',str(identity.get('workflow_adapter_identity'))[:12],flush=True)
paths=['main.py','adapters/v20.py','workflow_adapters.py']
for name in paths:
 p=root/'app'/name
 print('FILE_SHA',name,hashlib.sha256(p.read_bytes()).hexdigest()[:12],flush=True)
