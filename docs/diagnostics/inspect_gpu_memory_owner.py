"""Read-only GPU process ownership and independent Comfy queues for safe VRAM recovery."""
import subprocess,json,urllib.request,pathlib
res=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=12)
print('GPU_PROCESSES',flush=True)
if res.returncode==0:
  for row in res.stdout.splitlines():
    parts=[x.strip() for x in row.split(',')]
    if len(parts)<2 or not parts[0].isdigit():continue
    pid=int(parts[0]);mem=parts[-1]
    try:
      raw=pathlib.Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
      cmd=' '.join(x.decode('utf-8','replace') for x in raw if x)
      kind='renderer' if '8188' in cmd or 'comfyui-render' in cmd else 'prompt' if '8189' in cmd or 'comfyui-prompt' in cmd else 'other'
    except Exception:kind='unavailable'
    print('PROC',kind,'PID',pid,'USED_MIB',mem,flush=True)
for port in (8188,8189):
  try:
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=8) as r:q=json.load(r)
    print('QUEUE',port,'running',len(q.get('queue_running',[])),'pending',len(q.get('queue_pending',[])),flush=True)
  except Exception as exc:print('QUEUE_ERR',port,type(exc).__name__,flush=True)
