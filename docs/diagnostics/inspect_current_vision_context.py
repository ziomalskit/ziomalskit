"""Inspect actual submitted vision context without disclosing user content."""
import collections,json,pathlib,urllib.request
q=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
print('QUEUE',len(jobs),dict(collections.Counter(str(j.get('status')) for j in jobs)),flush=True)
recent=sorted([j for j in jobs if j.get('prompt_prompt_id')],key=lambda j:str(j.get('created_at','')),reverse=True)[:9]
for idx,j in enumerate(recent):
    pid=str(j['prompt_prompt_id'])
    try:
        with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=8) as r:
            obj=json.load(r).get(pid,{})
    except Exception as e:
        print('HISTORY_READ_ERROR',idx,type(e).__name__,flush=True)
        continue
    graph=obj.get('prompt',[None,None,{}])
    graph=graph[2] if isinstance(graph,(list,tuple)) and len(graph)>2 else {}
    errors=[entry[1] for entry in obj.get('status',{}).get('messages',[])
            if isinstance(entry,(list,tuple)) and len(entry)==2 and entry[0]=='execution_error' and isinstance(entry[1],dict)]
    print('JOB',idx,j.get('profile'),j.get('status'),'ERRORS',
          [(x.get('node_id'),x.get('node_type'),x.get('exception_type'),
            str(x.get('exception_message','')).splitlines()[0][:115]) for x in errors],flush=True)
    if not isinstance(graph,dict):continue
    for nid in ('2551:2640:2441','2551:2640:2447'):
        n=graph.get(nid,{})
        if not isinstance(n,dict):continue
        v=n.get('inputs',{})
        if not isinstance(v,dict):continue
        print('VISION_STAGE',idx,nid,'ctx_size',v.get('ctx_size'),'max_tokens',v.get('max_tokens'),
              'reasoning',v.get('reasoning'),'image_input',('image' in v),
              'extra_args_length',len(str(v.get('extra_args',''))),'node_type',n.get('class_type'),flush=True)
