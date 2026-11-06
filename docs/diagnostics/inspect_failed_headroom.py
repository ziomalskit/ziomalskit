"""Inspect GPU headroom demanded by failed 10Eros nodes without showing prompt text."""
import json,pathlib,urllib.request,subprocess,collections,time
state=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text());jobs=state if isinstance(state,list) else state.get('queue',[])
recent=sorted((j for j in jobs if j.get('prompt_prompt_id')),key=lambda j:j.get('created_at',0),reverse=True)[:8]
for idx,j in enumerate(recent):
  pid=str(j['prompt_prompt_id'])
  try:
    with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=7) as r:h=json.load(r).get(pid,{})
  except Exception as e:print('HISTORY_NO',idx,type(e).__name__,flush=True);continue
  g=h.get('prompt',[None,None,{}]);graph=g[2] if isinstance(g,list) and len(g)>2 else {}
  if not graph:continue
  errors=[z[1] for z in h.get('status',{}).get('messages',[]) if z[0]=='execution_error']
  if not errors:continue
  for e in errors:
    nid=e.get('node_id')
    n=graph.get(nid,{}) if isinstance(nid,str) else {}
    inp=n.get('inputs',{})
    print('FAILED',idx,nid,e.get('exception_type'),{
      k:inp.get(k) for k in ('aj_headroom_mb','aj_allow_sampling','timeout_seconds','model','n_gpu_layers','ctx_size','max_tokens') if k in inp
    },flush=True)
q=subprocess.run(['nvidia-smi','--query-gpu=memory.free,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8)
print('GPU_NOW',q.stdout.strip().replace(', ', ' / ') if q.returncode==0 else 'not_available',flush=True)
