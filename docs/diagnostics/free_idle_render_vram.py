"""Safely free idle renderer's cached H3 weights; leave active Prompt Worker running."""
import json,time,urllib.request,urllib.error,subprocess,pathlib
base='http://127.0.0.1:8188'
def queue():
    with urllib.request.urlopen(base+'/queue',timeout=8) as resp:return json.load(resp)
q=queue()
assert not q.get('queue_running') and not q.get('queue_pending'),'RENDERER_HAS_ACTIVE_QUEUE'
phase_file=pathlib.Path('/workspace/ComfyUI/output/.aj-gpu/state.json')
assert phase_file.is_file(),'GPU_PHASE_FILE_MISSING'
phase=json.loads(phase_file.read_text())
assert phase.get('phase')=='idle','RENDERER_PHASE_NOT_IDLE'
assert 0 <= time.monotonic()-float(phase.get('at',0)) <= 3,'RENDERER_HEARTBEAT_STALE'
def memory():
  p=subprocess.run(['nvidia-smi','--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
  assert p.returncode==0,'GPU_READ_FAILURE'
  return [int(v.strip()) for v in p.stdout.strip().splitlines()[0].split(',')]
before=memory()
print('FREE_BEFORE_MIB',before[0],'USED_BEFORE_MIB',before[1],flush=True)
req=urllib.request.Request(base+'/free',data=json.dumps({'unload_models':True,'free_memory':True}).encode(),headers={'Content-Type':'application/json'},method='POST')
with urllib.request.urlopen(req,timeout=25) as resp:
  print('RENDER_FREE_HTTP',resp.status,flush=True)
for i in range(8):
  time.sleep(2)
  after=memory()
  if after[0]>=before[0]+4096:break
print('FREE_AFTER_MIB',after[0],'USED_AFTER_MIB',after[1],'RECOVERED_MIB',after[0]-before[0],flush=True)
newq=queue()
print('RENDER_QUEUE_REMAINS_IDLE',not newq.get('queue_running') and not newq.get('queue_pending'),flush=True)
