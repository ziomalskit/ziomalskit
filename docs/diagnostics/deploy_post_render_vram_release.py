"""Persist renderer VRAM eviction after completed MP4; panel-only restart while idle."""
import ast,collections,hashlib,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
q=json.loads((root/'state/queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
terminal={'completed','prompt_failed','render_failed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown',
          'stuck_skipped','failed'}
assert all(j.get('status') in terminal for j in jobs),'ACTIVE_JOB_NO_PATCH'
for port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=8) as r:queue=json.load(r)
    assert not queue.get('queue_running') and not queue.get('queue_pending'),'COMFY_BUSY'
old='''    await _finish_watched_job(job, "render", res)


def _handle_worker_error'''
new='''    await _finish_watched_job(job, "render", res)
    # Release H3 weights retained by the otherwise idle ComfyUI renderer.
    # Without this boundary, cached video weights can consume ~39 GiB
    # after the MP4 is saved and block the next vision model's headroom lease.
    # Failure to free caching never invalidates an already completed MP4.
    if job.get("status") == "completed":
        try:
            await prepare_render_model_boundary()
        except Exception as error:
            try:
                job["render_cleanup_warning"] = (
                    "idle renderer VRAM cleanup failed: " + type(error).__name__
                )
                save_state()
            except Exception:
                pass


def _handle_worker_error'''
files=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),root/'app/main.py']
original={}
for f in files:
    src=f.read_text()
    assert hashlib.sha256(src.encode()).hexdigest()=='133955720a2c8b5aa6828aade44ba4142d856ec3162e860a080a4c5780bba506','LIVE_SOURCE_CHANGED'
    assert src.count(old)==1 and 'fully_cached_analysis = set(analysis_texts or {})' in src,'UNEXPECTED_SOURCE_STRUCTURE'
    dst=src.replace(old,new,1)
    ast.parse(dst)
    original[f]=(src,dst)
backups=pathlib.Path('/workspace/.aj-diagnostics')
backups.mkdir(mode=0o700,exist_ok=True)
for i,(path,(src,dst)) in enumerate(original.items()):
    p=backups/('before-post-render-free-'+str(i)+'.py')
    p.write_text(src);os.chmod(p,0o600)
def put(p,s):
    t=p.with_name(p.name+'.aj-vram-release-tmp')
    try:t.write_text(s);os.chmod(t,0o644);t.replace(p)
    finally:t.unlink(missing_ok=True)
def restart():
    r=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],capture_output=True,text=True,timeout=260)
    if r.returncode:raise RuntimeError('PANEL_RESTART_FAILED')
try:
    for p,(src,dst) in original.items():put(p,dst)
    restart()
    try:
        with urllib.request.urlopen('http://127.0.0.1:7860/',timeout=15) as r:status=r.status
    except urllib.error.HTTPError as e:status=e.code
    assert status==401,'PANEL_HEALTH_FAIL'
    for port in (8188,8189):
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/system_stats',timeout=12) as r:assert r.status==200
except Exception:
    for p,(src,dst) in original.items():put(p,src)
    restart()
    print('ROLLED_BACK',flush=True)
    raise
print('POST_RENDER_VRAM_RELEASE_DEPLOYED','FILES',len(files),flush=True)
print('CACHED_STAGE_FIX_RETAINED','PANEL_HEALTHY',flush=True)
print('NO_NEW_GENERATION_SUBMITTED',flush=True)
