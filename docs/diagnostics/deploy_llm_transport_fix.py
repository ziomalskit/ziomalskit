"""Deploy actual MIME and sanitized llama-server response categories, without inference."""
import collections,hashlib,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=pathlib.Path('/workspace/.aj-diagnostics');state.mkdir(exist_ok=True)
doc=json.loads((root/'state/queue.json').read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
counts=collections.Counter(str(j.get('status')) for j in jobs)
print('CURRENT_QUEUE',len(jobs),dict(counts),flush=True)
if any(s not in terminal for s in counts):raise SystemExit('BLOCKED: running or pending jobs')
assert 'H3_ALLOW_SUBMISSIONS=1\n' in (root/'runtime.env').read_text()
source=root/'custom_nodes/aj_production'
deployed=pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production').resolve(strict=True)
from sys import path as syspath
syspath.insert(0,str(root))
from app.native_identity import native_manifest
before=native_manifest(source)
assert native_manifest(deployed,deployed=True)==before,'BLOCKED: native packages differ before update'
commit='8e2456f8c6e17ac2dc3926b8ff5be30a944186d8'
changes={}
for name in ('compiler.py','prompt_completion.py'):
 url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/'+commit+'/h3/custom_nodes/aj_production/'+name
 with urllib.request.urlopen(url,timeout=25) as response:body=response.read()
 compile(body,str(source/name),'exec')
 if name=='compiler.py':
  assert b'HTTP_{error.code}_{cause}' in body and b'import urllib.error' in body
 else:
  assert b'unsupported native reference image encoding' in body and b'data:image/unknown' not in body
 changes[name]=body
 print('PINNED_FILE',name,hashlib.sha256(body).hexdigest()[:12],flush=True)
targets={}
for name,body in changes.items():
 for pkg in (source,deployed):
  p=pkg/name
  real=p.resolve(strict=True)
  old=real.read_bytes()
  assert hashlib.sha256(old).hexdigest()==before[name],'BLOCKED: unexpected deployed '+name
  targets[str(real)]=(real,old,body)
for i,(_,old,_) in enumerate(targets.values()):
 (state/('backup-native-transport-'+str(i)+'.py')).write_bytes(old)
def replace(path,body):
 tmp=path.with_name(path.name+'.aj-transport-temp')
 try:
  tmp.write_bytes(body);os.chmod(tmp,0o644);tmp.replace(path)
 finally:tmp.unlink(missing_ok=True)
try:
 for p,_,body in targets.values():replace(p,body)
 after=native_manifest(source)
 assert native_manifest(deployed,deployed=True)==after
 assert all(after[name]==hashlib.sha256(changes[name]).hexdigest() for name in changes)
 print('TWO_NATIVE_PACKAGES_MATCH',len(after),flush=True)
except Exception:
 for p,old,_ in targets.values():replace(p,old)
 raise
service=['bash',str(root/'scripts/service_ctl.sh'),'restart']
for which in ('prompt','panel'):
 result=subprocess.run([*service,which],capture_output=True,timeout=260)
 if result.returncode:
  print('RESTART_FAILED',which,result.returncode,flush=True)
  raise SystemExit(result.returncode)
 print('RESTARTED',which,flush=True)
for port,route in ((8189,'/system_stats'),(7860,'/')):
 try:
  with urllib.request.urlopen('http://127.0.0.1:'+str(port)+route,timeout=10) as r:code=r.status
 except urllib.error.HTTPError as e:code=e.code
 assert (port,code) in ((8189,200),(7860,401)),(port,code)
 print('LISTENER',port,code,flush=True)
print('NO_USER_BATCH_REPLAYED',flush=True)
