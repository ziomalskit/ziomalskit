"""Complete AJ startup/preflight without model downloads or inference. Submission gate stays OFF."""
import collections,json,os,pathlib,subprocess,urllib.request,urllib.error
state=pathlib.Path('/workspace/.aj-diagnostics')
qpath=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
if qpath.exists():
 doc=json.loads(qpath.read_text())
 jobs=doc if isinstance(doc,list) else doc.get('queue',[])
 active={'prompt_queued','prompt_running','render_queued_auto','render_queued_review','render_running',
         'prompt_preparing','render_preparing','prompt_submitting','render_submitting',
         'recovery_prompt','recovery_render','prompt_submission_uncertain','render_submission_uncertain'}
 states=collections.Counter(j.get('status') for j in jobs)
 print('QUEUE_STATUS_COUNTS',dict(states),flush=True)
 assert not any(s in active for s in states),'Active jobs found: refusing service restart'
m=json.loads((state/'models-transfer.json').read_text())
assert m.get('state')=='verified' and m.get('present_bytes')==278009016136,'Models not fully verified'
with (state/'post-fix-start.log').open('wb') as output:
 r=subprocess.run(['aj-docker','start'],stdout=output,stderr=subprocess.STDOUT,timeout=1200)
log=(state/'post-fix-start.log').read_text(errors='replace')
print('AJ_START_EXIT',r.returncode,flush=True)
for line in log.splitlines()[-65:]:
 if any(s in line for s in ('PASS','FAIL','AJ Docker blocked','Error','Traceback','preflight','smoke','validated','submissions OFF','service')):
  print('START_LOG',line[:650],flush=True)
if r.returncode:raise SystemExit(r.returncode)
for port in (8188,8189,7860):
 try:
  with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/',timeout=10) as response:
   print('LISTENER',port,response.status,flush=True)
 except urllib.error.HTTPError as error:
  if port==7860 and error.code==401:print('LISTENER',port,'AUTH_REQUIRED',flush=True)
  else:raise
runtime=pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env').read_text()
assert 'H3_ALLOW_SUBMISSIONS=0' in runtime, 'Submissions gate unexpectedly changed'
print('FULL_READINESS_PASS_SUBMISSIONS_OFF',flush=True)
