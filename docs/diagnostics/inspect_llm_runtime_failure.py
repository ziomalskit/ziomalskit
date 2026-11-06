"""Read-only LLM failure characteristics, no image/prompt/raw API body disclosure."""
import collections, json, pathlib, subprocess, time, urllib.request
root=pathlib.Path('/workspace/H3_VAST_MOBILE/state')
q=json.loads((root/'queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
print('QUEUE',len(jobs),dict(collections.Counter(str(j.get('status')) for j in jobs)),flush=True)
recent=sorted((j for j in jobs if j.get('prompt_prompt_id')),key=lambda j:j.get('created_at',0),reverse=True)[:8]
for j in recent:
 pid=j['prompt_prompt_id']
 try:
  with urllib.request.urlopen('http://127.0.0.1:8189/history/'+str(pid),timeout=6) as rsp:h=json.load(rsp)[str(pid)]
 except Exception as ex:
  print('HISTORY_READ_ERROR',type(ex).__name__,flush=True);continue
 graph=h.get('prompt',[None,None,{}])[2]
 if not isinstance(graph,dict):continue
 status=h.get('status',{})
 errors=[x[1] for x in status.get('messages',[]) if x[0]=='execution_error' and isinstance(x[1],dict)]
 print('JOB',j.get('profile'),j.get('status'),'SUBMISSION',j.get('prompt_submission_state'),
   'HISTORY',status.get('status_str'),'ERROR_NODES',[(x.get('node_id'),x.get('node_type'),x.get('exception_type'),str(x.get('exception_message'))[:100]) for x in errors],flush=True)
 for nodeid in ('2551:2640:2441','2551:2640:2447','2551:2640:2445','2551:2640:7450'):
  n=graph.get(nodeid,{});inputs=n.get('inputs',{})
  if not n:continue
  allowed=('model','mmproj','max_tokens','ctx_size','n_gpu_layers','n_cpu_moe_layers','reasoning','enable_processing','memory_mode','system_prompt','temperature','top_p','top_k')
  vals={k:inputs[k] for k in allowed if k in inputs and isinstance(inputs[k],(int,float,str,bool))}
  vals['has_image_input']='image' in inputs
  print(' STAGE',nodeid,str(n.get('_meta',{}).get('title',''))[:90],vals,flush=True)
try:
 r=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
 print('NVIDIA_COMPUTE_PROCESSES',r.stdout.strip()[:1400] if r.returncode==0 else 'unavailable',flush=True)
except Exception as e:print('NVIDIA_ERROR',type(e).__name__,flush=True)
try:
 log=pathlib.Path('/workspace/comfyui-prompt.log').read_text(errors='replace').splitlines()
 print('PROMPT_LOG_TAIL_ERRORS',len([x for x in log[-200:] if 'Error' in x or 'ERROR' in x or 'Exception' in x]),flush=True)
except Exception as e:print('LOG_ERROR',type(e).__name__,flush=True)
