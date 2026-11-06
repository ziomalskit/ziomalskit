"""Read-only comfy-cli validation on pre-existing synthetic smoke test files."""
import json,os,pathlib,subprocess
root=pathlib.Path('/workspace/H3_VAST_MOBILE/state')
files=sorted(root.glob('job_smoke-*_prompt.api.json'),key=lambda p:p.stat().st_mtime,reverse=True)
print('SYNTHETIC_PROMPT_API_FILES',len(files))
if not files:raise SystemExit(0)
for f in files[:2]:
 catalog=f.with_name(f.name.replace('.api.json','.catalog.json'))
 print('CATALOG_EXISTS',catalog.exists())
 if not catalog.exists():continue
 env={**os.environ,'COMFY_LOCAL_URL':'http://127.0.0.1:8189','COMFY_WHERE':'local','PATH':'/opt/aj/venv/bin:/usr/local/bin:/usr/bin:/bin'}
 cmd=['/opt/aj/venv/bin/python','-m','comfy_cli','--workspace','/workspace/ComfyUI','--json','workflow','validate','--workflow',str(f),'--input',str(catalog)]
 try:r=subprocess.run(cmd,capture_output=True,env=env,timeout=90)
 except subprocess.TimeoutExpired:print('CLI_TIMEOUT');continue
 print('VALIDATION_EXIT_CODE',r.returncode,'STDOUT_LEN',len(r.stdout),'STDERR_LEN',len(r.stderr))
 for name,data in (('stdout',r.stdout),('stderr',r.stderr)):
  s=data.decode('utf-8','replace')
  lines=s.splitlines()
  for line in lines[-5:]:
   try:
    d=json.loads(line)
    if isinstance(d,dict):
     print(name+'_JSON_FIELDS',list(d.keys()))
     print(name+'_OK',d.get('ok'),'TYPE',d.get('type'))
     payload=d.get('error',{})
     if isinstance(payload,dict):
      print(name+'_ERROR_CODE',str(payload.get('code',''))[:120])
      print(name+'_ERROR_MESSAGE',str(payload.get('message',''))[:700]);print(name+'_ERROR_DETAILS',json.dumps(payload,ensure_ascii=True)[:4800])
     else:print(name+'_ERROR_MESSAGE',str(payload)[:700])
     continue
   except (ValueError,TypeError):pass
   if len(line)<900 and not any(token in line.lower() for token in ('http','token','password','secret','base64','key=')):
    print(name+'_LINE',line[:600])
