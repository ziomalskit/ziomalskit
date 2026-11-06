"""Read only GPU gate lease state and current worker queues without changing jobs."""
import json,time,pathlib,urllib.request,os,collections
root=pathlib.Path('/workspace/ComfyUI/output/.aj-gpu')
p=root/'state.json'
if p.is_file():
  try:
    data=json.loads(p.read_text())
    print('GPU_GATE',{'phase':data.get('phase'),'age_s':round(time.monotonic()-float(data.get('at',0)),2),
       'owner_pid_alive':pathlib.Path('/proc/'+str(data.get('pid'))).exists()},flush=True)
  except Exception as exc:print('GATE_STATE_READ_ERROR',type(exc).__name__,flush=True)
else:print('NO_GPU_STATE_FILE',str(p),flush=True)
for port in (8188,8189):
  try:
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=7) as rsp:q=json.load(rsp)
    print('QUEUE',port,'running',len(q.get('queue_running',[])),'pending',len(q.get('queue_pending',[])),flush=True)
  except Exception as exc:print('QUEUE_FAILED',port,type(exc).__name__,flush=True)
j=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text());jobs=j if isinstance(j,list) else j.get('queue',[])
print('CURRENT_STATUS',dict(collections.Counter(x.get('status') for x in jobs[-30:])),flush=True)
