"""Apply exact v20 production stage selection fix, validate non-submitting, restart only panel."""
import ast,collections,json,os,pathlib,subprocess,urllib.request
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
panel=pathlib.Path('/workspace/H3_VAST_MOBILE')
q=json.loads((panel/'state/queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
states=collections.Counter(str(j.get('status')) for j in jobs)
if any(s not in terminal for s in states):
 raise SystemExit('BLOCKED: active or queued work; existing jobs left alone')
print('HISTORICAL_TERMINAL_JOBS',sum(states.values()),flush=True)
runtime=(panel/'runtime.env').read_text()
assert 'H3_ALLOW_SUBMISSIONS=1\n' in runtime, 'Live production gate unexpectedly disabled'
url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/712eb4afa110e1074727b83c8d76f80e791ed355/h3/app/main.py'
with urllib.request.urlopen(url,timeout=25) as response:source=response.read().decode()
start=source.index('        if service == "prompt":\n            # ComfyUI\'s frontend-owned CustomCombo')
end=source.index('        adapter.validate_final_graph(prepared, job["context"], phase=service)',start)
patch=source[start:end]
assert 'stage_node["inputs"]["index"] = 1' in patch and len(patch)<1700
old='        adapter.validate_final_graph(prepared, job["context"], phase=service)\n'
paths=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),panel/'app/main.py']
originals={}
for i,p in enumerate(paths):
 s=p.read_text()
 assert patch not in s and s.count(old)==1,'Unexpected runtime controller source'
 candidate=s.replace(old,patch+old)
 ast.parse(candidate)
 originals[p]=s
 (state/('main-before-active-combos-'+str(i)+'.py')).write_text(s)
def replace(p,s):
 tmp=p.with_name(p.name+'.aj-prod-tmp')
 tmp.write_text(s)
 os.chmod(tmp,0o644)
 tmp.replace(p)
for p,s in originals.items():replace(p,s.replace(old,patch+old))
print('DEPLOYED_STAGES_INDEX_1',4,flush=True)
env={**os.environ,'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
     'PANEL_ROOT':str(panel),'COMFY_ROOT':'/workspace/ComfyUI',
     'CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'}
smoke_log=state/'combos-live-release-smoke.log'
with smoke_log.open('wb') as output:
 result=subprocess.run(['bash',str(panel/'scripts/smoke_test.sh')],env=env,stdout=output,stderr=subprocess.STDOUT,timeout=360)
if result.returncode:
 for p,s in originals.items():replace(p,s)
 print('ROLLED_BACK: smoke validation failed, production process not restarted',flush=True)
 log=smoke_log.read_text(errors='replace')
 for line in log.splitlines()[-30:]:
  if any(part in line for part in ('Error','Traceback','Exception','validated','SMOKE')):
   print('SMOKE_LOG',line[:250],flush=True)
 raise SystemExit(result.returncode)
print('ALL_FOUR_SMOKE_PROFILES_PASS',flush=True)
import glob
a=sorted(glob.glob('/workspace/H3_VAST_MOBILE/state/job_smoke-*_prompt.api.json'),key=os.path.getmtime,reverse=True)
assert a,'Smoke output missing'
graph=json.loads(pathlib.Path(a[0]).read_text())
ids=('2551:5422:5067','2551:5422:5068','2551:5422:5069','2551:5422:5070')
assert all(graph[k]['inputs'].get('index')==1 for k in ids)
print('CONFIRMED_API_STAGE_INDEX_1',flush=True)
command=['bash',str(panel/'scripts/service_ctl.sh'),'restart','panel']
proc=subprocess.run(command,capture_output=True,timeout=220)
if proc.returncode:
 print('PANEL_RESTART_FAILED',proc.returncode,flush=True)
 raise SystemExit(proc.returncode)
print('LIVE_PANEL_RESTART_PASS',flush=True)
# All prior failures remain terminal. No batch/inference is submitted.
print('NO_JOBS_RESUBMITTED',flush=True)
