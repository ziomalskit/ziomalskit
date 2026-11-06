import importlib.util,json,os,pathlib,pty,select,subprocess,time
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
status=state/'models-transfer.json'
limit=time.monotonic()+5400
while True:
 report=json.loads(status.read_text())
 if report['state']=='verified':break
 if report['state']=='failed':raise SystemExit('Background model transfer failed; inspect its private log')
 if time.monotonic()>limit:raise SystemExit('Background model transfer deadline reached')
 time.sleep(15)
print('Background model size/SHA256 verification PASS',flush=True)
def stage(action):
 pid,fd=pty.fork()
 if pid==0:os.execvp('aj-docker',['aj-docker',action])
 pending=b'';recent=b'';sent=False;status=None
 with (state/('final-'+action+'.log')).open('ab',buffering=0) as log:
  while True:
   ready,_,_=select.select([fd],[],[],1)
   if ready:
    try:chunk=os.read(fd,65536)
    except OSError:break
    if not chunk:break
    log.write(chunk);recent=(recent+chunk)[-4096:]
    if b'One-time HF read token' in recent and not sent:
     os.write(fd,b'\n');sent=True
    pending+=chunk
    while b'\n' in pending:
     raw,pending=pending.split(b'\n',1);line=raw.decode('utf-8','replace').replace('\r','')
     if any(term in line for term in ['PASS','FAIL','AJ Docker blocked','Error','Traceback','Model size/SHA256','preflight','smoke','starting','started','ready']):print(line[:2200],flush=True)
   done,code=os.waitpid(pid,os.WNOHANG)
   if done:status=code;break
  if status is None:_,status=os.waitpid(pid,0)
 os.close(fd)
 code=os.waitstatus_to_exitcode(status)
 print('AJ_'+action.upper()+'_EXIT='+str(code),flush=True)
 if code:raise SystemExit(code)
stage('models')
stage('start')
# Verify the actual private listeners and authentication before exposing access.
import urllib.request,urllib.error,base64,shlex
runtime={}
for line in pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env').read_text().splitlines():
 if line and not line.startswith('#') and '=' in line:
  key,value=line.split('=',1);runtime[key]=shlex.split(value)[0] if shlex.split(value) else ''
password=runtime['H3_PANEL_PASSWORD'];user=runtime.get('H3_PANEL_USER','h3')
headers={'Authorization':'Basic '+base64.b64encode((user+':'+password).encode()).decode()}
for port,path in [(8188,'/system_stats'),(8189,'/system_stats'),(7860,'/api/config')]:
 with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}{path}',headers=headers if port==7860 else {}),timeout=30) as response:
  data=json.load(response);print('SERVICE_READY',port,response.status,flush=True)
  if port==7860:print('PANEL_PROFILES',json.dumps(data.get('profiles',data.get('production_profiles',{}))),flush=True)
print('PRIVATE_APP_START_PASS',flush=True)
