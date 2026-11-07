"""Read-only numeric render graph structure and current clock/latency evidence."""
import json, urllib.request, collections, os, pathlib, subprocess, time
def get(port,route):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+route,timeout=14) as r:return json.load(r)
with urllib.request.urlopen('http://127.0.0.1:8188/history?max_items=1',timeout=15) as r:
    h=json.load(r)
if h:
    obj=next(iter(h.values()))
    graph=obj.get('prompt',[None,None,{}])[2]
    found=[]
    for nid,node in graph.items():
        typ=node.get('class_type','')
        inp=node.get('inputs',{})
        safe={k:v for k,v in inp.items()
              if isinstance(v,(int,float)) and any(x in k.lower() for x in (
              'step','frame','height','width','fps','sample','video','cfg','resolution','time','size','length'))}
        if safe:
            found.append((typ,safe))
    print('NUMERIC_RENDER_FIELDS',found[:90],flush=True)
    counts=collections.Counter(x['class_type'] for x in graph.values())
    print('SAMPLING_RELEVANT',[(k,v) for k,v in counts.items() if any(y in k.lower() for y in (
          'scheduler','noise','sigmas','sampler','sagem','attention','compile','model','frame','latent'))][:60],flush=True)
for port in (8188,8189):
    q=get(port,'/queue')
    print('QUEUE',port,len(q.get('queue_running',[])),len(q.get('queue_pending',[])),flush=True)
j=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=j if isinstance(j,list) else j.get('queue',[])
counter=collections.Counter(x.get('status') for x in jobs)
print('NOW',dict(counter),flush=True)
p=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.free,memory.used,power.draw,clocks_throttle_reasons.active','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
print('GPU',p.stdout.strip()[:300],flush=True)
