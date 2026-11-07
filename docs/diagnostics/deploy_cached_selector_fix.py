"""Deploy pinned cached-analysis stage-selector correction, panel only, no GPU work."""
import ast,collections,hashlib,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
repo=pathlib.Path('/opt/aj/ziomalskit/h3')
q=root/'state/queue.json'
d=json.loads(q.read_text())
jobs=d if isinstance(d,list) else d.get('queue',[])
terminal={'completed','prompt_failed','render_failed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown',
          'stuck_skipped','failed'}
status=collections.Counter(str(x.get('status')) for x in jobs)
print('QUEUE_STATUS',dict(status),flush=True)
assert all(x.get('status') in terminal for x in jobs),'ACTIVE_JOBS_REFUSE_PANEL_RESTART'
for port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=10) as r: queue=json.load(r)
    assert not queue.get('queue_running') and not queue.get('queue_pending'),'COMFY_QUEUE_BUSY'
expected_old='582cb9c942ccdb7cefbf9a062b00a3798e4ff499c275a90dd7c662bacb079e55'
expected_new='181c5cba152cdf1fc13b1dcd7107a6341fa174fdb9fc5bca4ee1d74f4633094e'
url=('https://raw.githubusercontent.com/ziomalskit/ziomalskit/'
     '9f1ec54d1ff504202c6e42b8b452f3227f8d0703/h3/app/main.py')
with urllib.request.urlopen(url,timeout=30) as r:new=r.read()
assert hashlib.sha256(new).hexdigest()==expected_new,'UNEXPECTED_PINNED_SOURCE'
ast.parse(new.decode('utf-8'))
paths=[repo/'app/main.py',root/'app/main.py']
before={}
for p in paths:
    actual=p.read_bytes()
    digest=hashlib.sha256(actual).hexdigest()
    print('CONTROLLER_SOURCE',p.parent.parent.name,digest[:14],flush=True)
    assert digest in (expected_old,expected_new),'UNEXPECTED_LIVE_SOURCE_ABORT'
    before[p]=actual
diag=pathlib.Path('/workspace/.aj-diagnostics')
diag.mkdir(mode=0o700,exist_ok=True)
for i,(p,blob) in enumerate(before.items()):
    backup=diag/('before-cached-analysis-fix-'+str(i)+'.py')
    backup.write_bytes(blob)
    os.chmod(backup,0o600)
def put(path,blob):
    tmp=path.with_name(path.name+'.aj-selector-tmp')
    try:
        tmp.write_bytes(blob)
        os.chmod(tmp,0o644)
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)
def restart():
    r=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],
                     capture_output=True,text=True,timeout=250)
    if r.returncode:
        raise RuntimeError('PANEL_RESTART_FAILED '+str(r.returncode))
try:
    for p in before:put(p,new)
    restart()
    try:
        with urllib.request.urlopen('http://127.0.0.1:7860/',timeout=15) as r:code=r.status
    except urllib.error.HTTPError as e:code=e.code
    assert code==401,'PANEL_HEALTH_FAILED'
    for port in (8188,8189):
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/system_stats',timeout=12) as r:assert r.status==200
except Exception:
    for p,blob in before.items():put(p,blob)
    restart()
    print('ROLLBACK_COMPLETED',flush=True)
    raise
print('CACHE_SELECTOR_FIX_DEPLOYED','PANEL_ONLY_RESTART',flush=True)
print('NEW_BATCHES_USE_UPDATED_ADAPTER_IDENTITY',flush=True)
print('NO_JOBS_RETRIED','NO_INFERENCE',flush=True)
