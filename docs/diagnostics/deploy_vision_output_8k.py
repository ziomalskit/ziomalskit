"""Increase Mistral Step1/2 completion budget with restore-on-failure safeguards."""
import ast,collections,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=pathlib.Path('/workspace/.aj-diagnostics')
state.mkdir(exist_ok=True)
doc=json.loads((root/'state/queue.json').read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
counts=collections.Counter(str(j.get('status')) for j in jobs)
terminal={'prompt_failed','render_failed','completed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
if any(s not in terminal for s in counts):
    raise SystemExit('BLOCKED_ACTIVE_JOBS')
print('TERMINAL_JOBS',len(jobs),flush=True)
files=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),root/'app/main.py']
old='                node["inputs"]["ctx_size"] = 32768\n'
new='                node["inputs"]["ctx_size"] = 65536\n                node["inputs"]["max_tokens"] = 8192\n'
original={}
for i,p in enumerate(files):
    body=p.read_text()
    if (body.count(old)!=1 or
        body.count('for node_id in ("2551:2640:2441", "2551:2640:2447")')!=1 or
        body.count('node["inputs"].get("max_tokens") != 2048')!=1):
        raise SystemExit('BLOCKED_UNEXPECTED_CONTROLLER_'+str(i))
    patched=body.replace(old,new,1)
    ast.parse(patched)
    original[p]=(body,patched)
for i,(p,(before,after)) in enumerate(original.items()):
    backup=state/('controller-before-vision-output-8k-'+str(i)+'.py')
    backup.write_text(before)
    os.chmod(backup,0o600)
    tmp=p.with_name(p.name+'.aj-visionoutput-temp')
    try:
        tmp.write_text(after)
        os.chmod(tmp,0o644)
        tmp.replace(p)
    finally:tmp.unlink(missing_ok=True)
    print('PATCHED',i,flush=True)
def restart_panel():
    return subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],
                          capture_output=True,timeout=260)
try:
    r=restart_panel()
    if r.returncode:raise RuntimeError('PANEL_RESTART_EXIT_'+str(r.returncode))
    for port,route,expected in ((7860,'/',401),(8189,'/system_stats',200)):
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(port)+route,timeout=15) as resp:
                status=resp.status
        except urllib.error.HTTPError as error:status=error.code
        if status!=expected:raise RuntimeError('HEALTH_FAILED_'+str(port)+'_'+str(status))
        print('HEALTH',port,status,flush=True)
except Exception:
    for p,(before,after) in original.items():
        tmp=p.with_name(p.name+'.aj-rollback-temp')
        tmp.write_text(before)
        os.chmod(tmp,0o644)
        tmp.replace(p)
    restart_panel()
    print('ROLLED_BACK',flush=True)
    raise
print('VISION_STEP1_STEP2_CTX',65536,'MAX_TOKENS',8192,flush=True)
print('FAILED_JOBS_NOT_RETRIED',flush=True)
