"""One 10Eros prompt-only replay; short budget, existing references, no render."""
import json,time,uuid,urllib.request
base='http://127.0.0.1:8189'
def get(x):
    with urllib.request.urlopen(base+x,timeout=15) as r:return json.load(r)
q=get('/queue')
assert not q['queue_running'] and not q['queue_pending'],'COMFY_BUSY'
histor=get('/history?max_items=5')
g=None
for v in histor.values():
    errors=[e[1] for e in v.get('status',{}).get('messages',[]) if e[0]=='execution_error']
    if any(e.get('node_id')=='2551:2640:2445' for e in errors):
        g=v['prompt'][2]
        break
assert isinstance(g,dict),'NO_STEP3_HISTORY'
writers=[]
for nid,n in g.items():
    if n.get('class_type') in ('AJPromptTextProcessor','AJCompilerTextProcessor','AJGatedCompilerTextProcessor') and n.get('inputs',{}).get('model')=='Qwen3.5-27B-heretic.Q5_K_M.gguf':
        assert n['inputs'].get('n_gpu_layers')==50
        n['inputs']['n_gpu_layers']=100
        writers.append(nid)
assert 1<=len(writers)<=3,'WRITER_COUNT'
print('WRITER_OFFLOAD_100',writers,flush=True)
req=urllib.request.Request(base+'/prompt',data=json.dumps({'prompt':g,'client_id':uuid.uuid4().hex}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=60) as r:pid=json.load(r)['prompt_id']
print('ONE_PROMPT_ONLY_SUBMITTED',flush=True)
start=time.monotonic()
while time.monotonic()-start<560:
    obj=get('/history/'+pid).get(pid)
    if obj and 'completed' in obj.get('status',{}):
        s=obj['status'];print('RESULT',s.get('status_str'),s.get('completed'),'SECONDS',int(time.monotonic()-start),flush=True)
        for typ,err in s.get('messages',[]):
            if typ=='execution_error':print('ERROR_NODE',err.get('node_id'),err.get('node_type'),err.get('exception_type'),flush=True)
        if s.get('completed') is True and s.get('status_str')=='success':print('AUTOPROMPT_COMPLETE_PASS',flush=True)
        break
    time.sleep(8)
else:
    req=urllib.request.Request(base+'/interrupt',data=b'{}',headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as r:r.read()
    print('TIME_LIMIT_INTERRUPT',flush=True)
