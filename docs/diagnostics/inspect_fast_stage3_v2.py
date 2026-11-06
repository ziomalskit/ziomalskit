"""Read-only short diagnostics for the latest prompt-only Step 3 failure."""
import collections,json,pathlib,subprocess,urllib.request
base='http://127.0.0.1:8189'
def get(route):
    with urllib.request.urlopen(base+route,timeout=12) as response: return json.load(response)
running=get('/queue')
print('COMFY_QUEUE',len(running.get('queue_running',[])),len(running.get('queue_pending',[])),flush=True)
j=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=j if isinstance(j,list) else j.get('queue',[])
print('AJ_STATUS',dict(collections.Counter(str(x.get('status')) for x in jobs)),flush=True)
try:
    history=get('/history?max_items=5')
except Exception as error:
    print('HISTORY_READ_ERROR',type(error).__name__,flush=True)
    history={}
for i,(pid,obj) in enumerate(list(history.items())[:5]):
    st=obj.get('status',{})
    print('ENTRY',i,st.get('status_str'),st.get('completed'),flush=True)
    graph=(obj.get('prompt') or [None,None,{}])
    graph=graph[2] if isinstance(graph,(list,tuple)) and len(graph)>=3 else {}
    for nid in ('2551:2640:2441','2551:2640:2447','2551:2640:2445','2551:2640:7450','2551:5422:5070'):
        node=graph.get(nid,{})
        if not isinstance(node,dict) or not node:continue
        inp=node.get('inputs') or {}
        allowed=('timeout_seconds','ctx_size','max_tokens','reasoning','extra_args','memory_mode','n_gpu_layers','n_cpu_moe_layers','enable_processing')
        print('NODE',i,nid,node.get('class_type'),{k:inp[k] for k in allowed if k in inp},flush=True)
    for item in st.get('messages',[]):
        if not isinstance(item,(list,tuple)) or len(item)!=2 or item[0]!='execution_error':continue
        er=item[1]
        msg=str(er.get('exception_message') or '')
        safe='TIMEOUT' if 'timed out' in msg.lower() or str(er.get('exception_type'))=='TimeoutError' else 'NON_NATURAL_COMPLETION' if 'natural, untruncated' in msg else 'OTHER'
        print('FAILURE',i,er.get('node_id'),er.get('node_type'),er.get('exception_type'),safe,flush=True)
try:
    cmd=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'],text=True,capture_output=True,timeout=10)
    print('GPU_PROCESSES',len([x for x in cmd.stdout.splitlines() if x.strip()]) if cmd.returncode==0 else 'UNKNOWN',flush=True)
except Exception as error:print('GPU_READ_ERROR',type(error).__name__,flush=True)
