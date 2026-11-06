"""Resolve the image-provided custom_nodes symlink before strict native hash validation; no inference."""
import ast,collections,json,os,pathlib,subprocess,sys,site
site.addsitedir('/opt/aj/venv/lib/python3.12/site-packages')
sys.path.insert(0,'/workspace/H3_VAST_MOBILE')
from app.native_identity import native_manifest
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
source=root/'custom_nodes/aj_production'
deployed=pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production')
actual=deployed.resolve(strict=True)
expected=native_manifest(source)
observed=native_manifest(actual,deployed=True)
print('NATIVE_MANIFEST_MATCH',expected==observed,'FILES',len(expected),flush=True)
print('SYMLINK_PARENT',deployed.parent.is_symlink(),'SYMLINK_PACKAGE',deployed.is_symlink(),flush=True)
if observed!=expected:raise SystemExit('BLOCKED: actual deployed native hashes differ from source')
queue_path=root/'state/queue.json'
data=json.loads(queue_path.read_text())
jobs=data if isinstance(data,list) else data.get('queue',[])
states=collections.Counter(str(j.get('status')) for j in jobs)
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
print('QUEUED_JOB_COUNTS',dict(states),flush=True)
if any(key not in terminal for key in states):raise SystemExit('BLOCKED: running or queued jobs exist; no changes')
runtime=(root/'runtime.env').read_text()
if 'H3_ALLOW_SUBMISSIONS=1\n' not in runtime:raise SystemExit('BLOCKED: submissions not enabled')
old='''def verify_deployed_native(package: Path, expected: dict[str, str]) -> None:
    validate_manifest_paths(expected)
    if native_manifest(package, deployed=True) != expected:
        raise ValueError("deployed native implementation differs from frozen repository implementation")
'''
new='''def verify_deployed_native(package: Path, expected: dict[str, str]) -> None:
    validate_manifest_paths(expected)
    # The Docker seed uses a symlinked custom_nodes parent. Resolve it first;
    # read_native_sources still opens every canonical directory/file with O_NOFOLLOW.
    canonical = package.resolve(strict=True)
    if native_manifest(canonical, deployed=True) != expected:
        raise ValueError("deployed native implementation differs from frozen repository implementation")
'''
paths=[pathlib.Path('/opt/aj/ziomalskit/h3/app/native_identity.py'),root/'app/native_identity.py']
for path in paths:
 body=path.read_text()
 if body.count(old)!=1 and body.count(new)!=1:raise SystemExit('BLOCKED: unexpected native identity code in '+str(path))
 ast.parse(body.replace(old,new))
backup=pathlib.Path('/workspace/.aj-diagnostics')
backup.mkdir(exist_ok=True)
for index,path in enumerate(paths):
 body=path.read_text()
 if new in body:continue
 (backup/('native-identity-before-canonical-'+str(index)+'.py')).write_text(body)
 temp=path.with_name(path.name+'.aj-temp')
 temp.write_text(body.replace(old,new))
 os.chmod(temp,0o644)
 temp.replace(path)
 print('PATCHED',index,flush=True)
# Prove the patched actual module validates the identical 7-file payload.
import importlib
ni=importlib.reload(sys.modules['app.native_identity'])
ni.verify_deployed_native(deployed,expected)
print('NATIVE_VERIFY_PASS',flush=True)
# Restarts are only permitted without active jobs; keep the previously selected live gate.
status=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],capture_output=True,timeout=180)
if status.returncode:
 print('PANEL_RESTART_EXIT',status.returncode,flush=True)
 raise SystemExit(status.returncode)
print('PANEL_RESTART_PASS',flush=True)
