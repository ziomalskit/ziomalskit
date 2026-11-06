"""Apply exact Mistral analysis 32K context fix without resubmitting failed jobs."""
import ast,collections,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=pathlib.Path('/workspace/.aj-diagnostics')
queue=json.loads((root/'state/queue.json').read_text())
jobs=queue if isinstance(queue,list) else queue.get('queue',[])
counts=collections.Counter(str(j.get('status')) for j in jobs)
terminal={'prompt_failed','render_failed','completed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
if any(status not in terminal for status in counts):
 raise SystemExit('BLOCKED: active or queued jobs, no files changed')
print('HISTORICAL_TERMINAL_JOBS',len(jobs),flush=True)
assert 'H3_ALLOW_SUBMISSIONS=1\n' in (root/'runtime.env').read_text()
commit='f40b7820fac536370ece445c61e5db5e49be11dc'
url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/'+commit+'/h3/app/main.py'
with urllib.request.urlopen(url,timeout=30) as r:code=r.read().decode()
start=code.index('            # Real llama-server HTTP 400 on the first vision stage:')
end=code.index('        adapter.validate_final_graph(prepared, job["context"], phase=service)',start)
patch=code[start:end]
assert 'node["inputs"]["ctx_size"] = 32768' in patch and len(patch)<1400
old='        adapter.validate_final_graph(prepared, job["context"], phase=service)\n'
files=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),root/'app/main.py']
original={}
for p in files:
 body=p.read_text()
 if patch in body:
  print('ALREADY_PATCHED',str(p),flush=True)
  continue
 if body.count(old)!=1 or 'stage_node["inputs"]["index"] = 1' not in body:
  raise SystemExit('BLOCKED: unexpected existing controller')
 candidate=body.replace(old,patch+old)
 ast.parse(candidate)
 original[p]=(body,candidate)
for n,(p,(before,after)) in enumerate(original.items()):
 (state/('main-before-vision-32k-'+str(n)+'.py')).write_text(before)
 tmp=p.with_name(p.name+'.aj-visionctx-tmp')
 try:
  tmp.write_text(after)
  os.chmod(tmp,0o644)
  tmp.replace(p)
 finally:tmp.unlink(missing_ok=True)
 print('UPDATED_CONTROLLER',n,flush=True)
if original:
 r=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],
                  capture_output=True,timeout=240)
 if r.returncode:
  for p,(before,_) in original.items():
   tmp=p.with_name(p.name+'.aj-rollback-tmp')
   tmp.write_text(before);os.chmod(tmp,0o644);tmp.replace(p)
  subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],capture_output=True,timeout=240)
  raise SystemExit('PANEL_RESTART_FAILED_ROLLED_BACK')
 print('PANEL_RESTARTED',flush=True)
for port,route in ((7860,'/'),(8189,'/system_stats')):
 try:
  with urllib.request.urlopen('http://127.0.0.1:'+str(port)+route,timeout=12) as r:status=r.status
 except urllib.error.HTTPError as e:status=e.code
 assert (port,status) in ((7860,401),(8189,200)),(port,status)
 print('LIVE_SERVICE',port,status,flush=True)
print('VISION_CONTEXT_32768_ENABLED',flush=True)
print('NO_JOBS_SUBMITTED',flush=True)
