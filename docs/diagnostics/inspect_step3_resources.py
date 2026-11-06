"""Read-only resource and output-size evidence for Step3 (no private text)."""
import collections,json,os,pathlib,subprocess,time,urllib.request
base='http://127.0.0.1:8189'
with urllib.request.urlopen(base+'/history?max_items=2',timeout=20) as r: history=json.load(r)
for i,(pid,o) in enumerate(history.items()):
    s=o.get('status',{})
    print('ENTRY',i,'STATUS',s.get('status_str'),'MESSAGES',[(x[0],list(x[1].keys()) if isinstance(x[1],dict) else None) for x in s.get('messages',[])],flush=True)
    g=o.get('prompt',[None,None,{}]);g=g[2] if isinstance(g,(list,tuple)) and len(g)>2 else {}
    for id in ('2551:2640:2441','2551:2640:2447','2551:2640:2445','2551:2640:7450'):
        node=g.get(id,{})
        a=node.get('inputs',{})
        print('MODEL_STAGE',id,'model_basename',a.get('model'),'mmproj',bool(a.get('mmproj') and a.get('mmproj')!='none'),
              'timeout',a.get('timeout_seconds'),'n_gpu_layers',a.get('n_gpu_layers'),'ctx_size',a.get('ctx_size'),
              'max_tokens',a.get('max_tokens'),'prompt_input_type',type(a.get('prompt')).__name__,
              'prompt_input_str_len',len(a['prompt']) if isinstance(a.get('prompt'),str) else None,flush=True)
    outputs=o.get('outputs') or {}
    print('OUTPUT_NODES',len(outputs),flush=True)
    out_lengths=[]
    def lengths(v):
        if isinstance(v,str):return [len(v)]
        if isinstance(v,list):return sum((lengths(z) for z in v),[])
        if isinstance(v,dict):return sum((lengths(z) for k,z in v.items() if k not in ('filename','type','subfolder','url')),[])
        return []
    for nid,val in outputs.items():
        ls=lengths(val)
        if any(n>1000 for n in ls):
            out_lengths.append({'node_id':nid,'count':len(ls),'largest_lengths':sorted(ls,reverse=True)[:5]})
    print('LARGE_OUTPUTS',json.dumps(sorted(out_lengths,key=lambda x:-max(x['largest_lengths']))[:20]),flush=True)
try:
    c=subprocess.run(['nvidia-smi','--query-compute-apps=pid,process_name,used_gpu_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8)
    if c.returncode==0:
        print('GPU_PROCESS_USAGE_MIB',[line.strip().split(',')[-1].strip() for line in c.stdout.splitlines() if line.strip()],flush=True)
except Exception as e:print('GPU_READ_ERROR',type(e).__name__,flush=True)
for p in (pathlib.Path('/workspace/comfyui-prompt.log'),):
    if p.is_file():
        ls=p.read_text(errors='replace').splitlines()
        filtered=[line.strip() for line in ls[-180:] if any(x in line.lower() for x in ('timed out','finished in','prompt executed','model loaded','torch.cuda','model_type','received prompt'))]
        print('RECENT_LOG_EVENT_COUNT',len(filtered),flush=True)
        for line in filtered[-12:]:
            if len(line)<270 and not any(k in line.lower() for k in ('token','password','secret','authorization','prompt:')):
                print('LOG_EVENT',line,flush=True)
