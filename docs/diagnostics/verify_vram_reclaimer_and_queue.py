"""Read-only snapshot of active AJ 10Eros queue and bounded VRAM cleaner."""
import json,pathlib,collections,subprocess,urllib.request
root=pathlib.Path('/workspace/.aj-diagnostics')
p=root/'aj_idle_vram_reclaimer.pid'
if p.is_file():
    pid=p.read_text().strip()
    try:
        cmd=(pathlib.Path('/proc')/pid/'cmdline').read_bytes()
        print('RECLAIMER_ALIVE',b'aj_idle_vram_reclaimer.py' in cmd,flush=True)
    except Exception:print('RECLAIMER_ALIVE',False,flush=True)
log=root/'aj_idle_vram_reclaimer.log'
if log.exists():
    lines=log.read_text(errors='replace').splitlines()
    print('RECLAIMER_EVENTS',lines[-5:],flush=True)
state=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=state if isinstance(state,list) else state.get('queue',[])
recent=sorted(jobs,key=lambda x:x.get('created_at',0),reverse=True)
print('LATEST_30',dict(collections.Counter(str(j.get('status')) for j in recent[:30])),flush=True)
for i,j in enumerate(recent[:12]):
    print('JOB',i,j.get('status'),'has_mp4',bool(j.get('video_outputs')),'prompt_id',bool(j.get('prompt_prompt_id')),flush=True)
for port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=8) as r:queue=json.load(r)
    print('COMFY_QUEUE',port,len(queue.get('queue_running',[])),len(queue.get('queue_pending',[])),flush=True)
p=subprocess.run(['nvidia-smi','--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8)
if p.returncode==0:print('GPU_FREE_USED',p.stdout.strip(),flush=True)
