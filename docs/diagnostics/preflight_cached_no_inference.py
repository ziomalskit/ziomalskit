"""One read-only-to-GPU offline cached-text conversion preflight; never POST /prompt."""
import os,pathlib,subprocess,sys
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
script=r'''
import asyncio,copy,json,pathlib,sys,uuid
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
sys.path.insert(0,str(root))
from app import main as m
d=json.loads((root/'state/queue.json').read_text())
source=sorted((x for x in d['queue'] if x.get('profile')=='10eros_full'
        and x.get('prompt_kind') in ('cached_text','durable_text')
        and x.get('status')=='prompt_failed'),key=lambda x:x.get('created_at',0),reverse=True)
assert source,'NO_SAVED_CACHED_JOB'
j=copy.deepcopy(source[0])
j['id']='offline_cache_preflight_'+uuid.uuid4().hex[:10]
j['active_service']='prompt'
ctx=j['context']
adapter=m.get_adapter(ctx['workflow_id'])
ctx.update(adapter.identity())
settings=adapter.resolve_render_settings(ctx['render_settings'],frozen=True,profile=j['profile'])
ctx['workflow_render_identity']=adapter.render_identity(ctx,settings)
m.queue=[j]
m.batches=d['batches']
m.job_profile(j)
path=None
try:
    path=m.patch_workflow(j,m.production_workflow(j))
    prepared=asyncio.run(m._prepare_workflow_api(path,'prompt'))
    present=[int(k in prepared) for k in ('2551:5422:5067','2551:5422:5068',
             '2551:5422:5069','2551:5422:5070')]
    visions=[int(k in prepared) for k in ('2551:2640:2441','2551:2640:2447')]
    print('OFFLINE_CACHED_MODE',j.get('prompt_kind'),flush=True)
    print('CONVERTED_NODES',len(prepared),'SELECTORS',present,
          'VISION_ANALYSIS',visions,'DURABLE_NODES',
          sum(k.startswith('aj_analysis_') for k in prepared),flush=True)
    print('PROMPT_API_CONVERSION_PASS',flush=True)
finally:
    if path is not None:
        for f in (path,path.with_suffix('.api.json'),path.with_suffix('.catalog.json')):
            f.unlink(missing_ok=True)
'''
env=os.environ.copy()
env['PYTHONPATH']=str(root)
env['COMFY_PYTHON']='/opt/aj/venv/bin/python'
env['COMFY_ROOT']='/workspace/ComfyUI'
env['RENDER_COMFY_URL']='http://127.0.0.1:8188'
env['PROMPT_COMFY_URL']='http://127.0.0.1:8189'
run=subprocess.run(['/opt/aj/venv/bin/python','-c',script],env=env,capture_output=True,text=True,timeout=220)
print('EXIT',run.returncode,flush=True)
if run.returncode:
    clean_lines=[x[:350] for x in run.stderr.splitlines()
                 if 'Traceback' in x or 'Error:' in x or 'Exception:' in x
                 or x.strip().startswith('File ')][-12:]
    print('SAFE_ERROR',clean_lines,flush=True)
print(run.stdout[-1500:],flush=True)
assert run.returncode==0,'OFFLINE_CONVERSION_FAILED'
