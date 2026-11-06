import json,pathlib,urllib.request,urllib.error,shlex,re
state=pathlib.Path('/workspace/.aj-diagnostics')
f=state/'models-transfer.json'
if f.exists():
 d=json.loads(f.read_text())
 print('MODELS',json.dumps({k:d.get(k) for k in ('state','exit_code','present_bytes','total_bytes','elapsed_seconds')}))
else: print('MODELS status missing')
for port in (8188,8189,7860):
 try:
  with urllib.request.urlopen('http://127.0.0.1:%d/'%port,timeout=3) as r:print('PORT',port,r.status)
 except urllib.error.HTTPError as e:print('PORT',port,'HTTP',e.code)
 except Exception as e:print('PORT',port,'unavailable',type(e).__name__)
secrets=[]
runtime=pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env')
if runtime.exists():
 for token in shlex.split(runtime.read_text()):
  k,_,v=token.partition('=')
  if any(part in k for part in ('PASSWORD','TOKEN','SECRET','KEY')) and v:secrets.append(v)
 print('RUNTIME_ENV exists')
else:print('RUNTIME_ENV missing')
def safe(x):
 for secret in secrets:x=x.replace(secret,'[REDACTED]')
 return x
for name in ('widget-conversion.log','final-start.log','final-start-retry.log','soundfile-fix-2.log'):
 f=state/name
 if not f.exists():continue
 lines=f.read_text(errors='replace').splitlines()
 relevant=[(i,line) for i,line in enumerate(lines) if re.search('error|fail|traceback|exception|comfy-cli|widget|validation|unsupported|missing|node|invalid',line,re.I)]
 print('LOG',name,'lines',len(lines),'matches',len(relevant))
 for i,line in relevant[-16:]:print('LOG_LINE',i,safe(line[:500]))
