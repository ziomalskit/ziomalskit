import hashlib,json,os,pathlib,subprocess,time
ROOT=pathlib.Path('/workspace');ROOT.mkdir(exist_ok=True)
STATE=ROOT/'.aj-diagnostics';STATE.mkdir(mode=0o700,exist_ok=True)
os.umask(0o077)
supervisor=r'''
import fcntl,json,os,pathlib,signal,subprocess,time,sys
root=pathlib.Path('/workspace');state=root/'.aj-diagnostics'
lock=(state/'models-transfer.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit(0)
package=pathlib.Path('/opt/aj/ziomalskit/h3')
sys.path[:0]=[str(package/'scripts'),str(package)]
from provision_models import disk_plan,default_lora_destinations
from app.artifacts import validate_manifest
from model_downloads import partial_bytes
manifest=json.loads((package/'config/models_manifest.json').read_text())
optional=default_lora_destinations(package/'config/lora_registry.json')
items={key:item for key,item in validate_manifest(manifest).items() if item['required'] or item['destination'] in optional}
models=root/'ComfyUI/models';models.mkdir(parents=True,exist_ok=True)
plan=disk_plan(manifest,models,optional_destinations=optional)
assert len(items)==19 and plan['known_stack_bytes']==278009016136
assert all(item['provider']=='huggingface' for item in items.values())
# Bound the plan to the 400 GB provider allocation, even when statvfs exposes host free space.
assert plan['minimum_free_bytes']+32*1024**3+16050426172 < 400_000_000_000
assert plan['free_bytes']>plan['minimum_free_bytes']+32*1024**3
status=state/'models-transfer.json'
def save(obj):
 temp=status.with_suffix('.tmp');temp.write_text(json.dumps(obj,indent=2));os.chmod(temp,0o600);temp.replace(status)
def observed():
 total=0
 for item in items.values():
  path=models/item['destination']
  present=path.stat().st_size if path.is_file() else partial_bytes(models,item)
  total+=min(present,item['size_bytes'])
 return total
env={'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PATH':'/opt/aj/venv/bin:/usr/local/bin:/usr/bin:/bin','COMFY_PYTHON':'/opt/aj/venv/bin/python','HF_HUB_DISABLE_PROGRESS_BARS':'1','PYTHONUNBUFFERED':'1'}
for key in ('SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE','HTTP_PROXY','HTTPS_PROXY','NO_PROXY'):
 if key in os.environ:env[key]=os.environ[key]
command=['/opt/aj/seed/.aj-download-venv/bin/python',str(package/'scripts/provision_models.py'),'--manifest',str(package/'config/models_manifest.json'),'--models-root',str(models),'--toolchain','/opt/aj/build/llama.cpp-b10472']
with (state/'models-transfer.log').open('ab',buffering=0) as log:
 child=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
 def stop(signum,frame):
  if child.poll() is None:child.terminate()
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGHUP,stop)
 started=time.monotonic();last_progress=started;last_bytes=-1
 while child.poll() is None:
  n=observed()
  if n!=last_bytes:last_progress=time.monotonic();last_bytes=n
  save({'state':'running','pid':os.getpid(),'download_pid':child.pid,'present_bytes':n,'total_bytes':plan['known_stack_bytes'],'elapsed_seconds':round(time.monotonic()-started)})
  if time.monotonic()-started>7200 or time.monotonic()-last_progress>900:
   child.terminate();break
  time.sleep(15)
 try:code=child.wait(timeout=60)
 except subprocess.TimeoutExpired:child.kill();code=child.wait()
 save({'state':'verified' if code==0 else 'failed','exit_code':code,'present_bytes':observed(),'total_bytes':plan['known_stack_bytes'],'elapsed_seconds':round(time.monotonic()-started)})
'''
p=STATE/'download_supervisor.py';p.write_text(supervisor);os.chmod(p,0o700)
with (STATE/'download-supervisor.log').open('ab') as log:
 child=subprocess.Popen(['/opt/aj/venv/bin/python',str(p)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
for _ in range(20):
 status=STATE/'models-transfer.json'
 if status.is_file():print(status.read_text());break
 if child.poll() is not None:
  print('SUPERVISOR_EXIT',child.returncode)
  print((STATE/'download-supervisor.log').read_text()[-5000:]);break
 time.sleep(1)
else:print('Transfer supervisor started; status pending')
