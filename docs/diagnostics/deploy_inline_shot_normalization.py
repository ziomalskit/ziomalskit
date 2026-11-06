"""Install validated inline shot reference repair on existing AJ services."""
import ast,hashlib,importlib.util,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
diag=pathlib.Path('/workspace/.aj-diagnostics')
doc=json.loads((root/'state/queue.json').read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
assert all(j.get('status') in terminal for j in jobs),'BLOCKED_AJ_WORK_RUNNING'
for port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:%d/queue'%port,timeout=12) as r:q=json.load(r)
    assert not q.get('queue_pending') and not q.get('queue_running'),'BLOCKED_COMFY_BUSY'
commit='9e24c91cf4d0317f060c78aa76f88feb94aca8e9'
url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/'+commit+'/h3/custom_nodes/aj_production/temporal.py'
with urllib.request.urlopen(url,timeout=30) as response:replacement=response.read()
ast.parse(replacement.decode())
assert b'def _canonicalize_inline_shot_mentions' in replacement
assert b'if canonicalize:\n        text = _canonicalize_inline_shot_mentions(text, plan)' in replacement
targets={}
for p in (root/'custom_nodes/aj_production/temporal.py',
          pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/temporal.py')):
    path=p.resolve(strict=True)
    original=path.read_bytes()
    assert b'def final_timeline(text, plan, effective, *, canonicalize=False)' in original
    if b'def _canonicalize_inline_shot_mentions' in original:
        assert original==replacement,'UNEXPECTED_EXISTING_NORMALIZATION'
    targets[path]=original
for i,(p,before) in enumerate(targets.items()):
    backup=diag/('temporal-before-inline-normalization-'+str(i)+'.py')
    backup.write_bytes(before)
    os.chmod(backup,0o600)
def put(path,data):
    temp=path.with_name(path.name+'.aj-inline-normalizer-temp')
    try:
        temp.write_bytes(data);os.chmod(temp,0o644);temp.replace(path)
    finally:temp.unlink(missing_ok=True)
def restart(name):
    p=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart',name],capture_output=True,timeout=260)
    if p.returncode:raise RuntimeError('RESTART_FAILED_'+name)
try:
    for p,before in targets.items():put(p,replacement)
    for name in ('prompt','render','panel'):restart(name)
    for port,route in ((8188,'/system_stats'),(8189,'/system_stats')):
        with urllib.request.urlopen('http://127.0.0.1:%d%s'%(port,route),timeout=15) as r:assert r.status==200
    try:
        with urllib.request.urlopen('http://127.0.0.1:7860/',timeout=15) as r:code=r.status
    except urllib.error.HTTPError as e:code=e.code
    assert code==401
    spec=importlib.util.spec_from_file_location('aj_inline_temporal_live',list(targets)[0])
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    # Use an isolated, synthetic fixture: ComfyUI history is cleared on restart.
    plan = "[Shot 1]\\nTimeline: 00:00.000-00:01.000\\nOne action.\\n"
    final = (
        "subject_definitions:\\nObject\\n"
        "summary:\\nOne description references [Shot 1] inline.\\n"
        "retention_analysis:\\nThe object remains consistent.\\n"
        "detailed_description:\\n[Shot 1]\\nOne action.\\n"
        "overall_soundscape:\\nQuiet.\\n"
        "non_diegetic_music:\\nNone.\\n"
    )
    normalized = mod.final_timeline(final, plan, 1.0, canonicalize=True)
    assert normalized.count("[Shot 1]") == 1 and "references Shot 1 inline" in normalized, 'SYNTHETIC_GUARD_FIX_FAILED'
except Exception:
    for p,before in targets.items():put(p,before)
    for name in ('prompt','render','panel'):
        try:restart(name)
        except Exception:pass
    print('ROLLED_BACK',flush=True)
    raise
print('INLINE_SHOT_REFERENCES_REPAIRED',len(targets),'NATIVE_COPIES',flush=True)
print('SYNTHETIC_FINAL_PROMPT_VALIDATION_PASS',flush=True)
print('SERVICES_HEALTHY',8188,8189,7860,flush=True)
print('NO_NEW_GENERATION',flush=True)
