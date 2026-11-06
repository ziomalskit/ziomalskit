"""Correct the 27B GPU-layer cap from 50 to 100 on 10Eros prompt stages."""
import ast,json,os,pathlib,subprocess,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
diag=pathlib.Path('/workspace/.aj-diagnostics')
state=json.loads((root/'state/queue.json').read_text())
jobs=state if isinstance(state,list) else state.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
assert all(j.get('status') in terminal for j in jobs),'BLOCKED_RUNNING_JOBS'
for service_port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:%d/queue'%service_port,timeout=12) as response:q=json.load(response)
    assert not q.get('queue_running') and not q.get('queue_pending'),'BLOCKED_COMFY_BUSY'
old='                node["inputs"]["max_tokens"] = 8192\n        adapter.validate_final_graph(prepared, job["context"], phase=service)\n'
new='''                node["inputs"]["max_tokens"] = 8192
            if job.get("profile") == "10eros_full":
                # Qwen3.5-27B has 64 blocks; 50 GPU layers left 14 on CPU.
                writer_nodes = [
                    (identifier, candidate) for identifier, candidate in prepared.items()
                    if isinstance(candidate, dict) and candidate.get("class_type") in {
                        "AJPromptTextProcessor", "AJCompilerTextProcessor", "AJGatedCompilerTextProcessor"
                    } and candidate.get("inputs", {}).get("model") == "Qwen3.5-27B-heretic.Q5_K_M.gguf"
                ]
                if not writer_nodes or len(writer_nodes) > 3:
                    raise RuntimeError("Unexpected Qwen3.5-27B writer nodes; refusing GPU override")
                for identifier, candidate in writer_nodes:
                    if candidate["inputs"].get("n_gpu_layers") != 50:
                        raise RuntimeError("Unexpected 27B GPU-layer policy; refusing override")
                    candidate["inputs"]["n_gpu_layers"] = 100
        adapter.validate_final_graph(prepared, job["context"], phase=service)
'''
files=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),root/'app/main.py']
original={}
for i,p in enumerate(files):
    body=p.read_text()
    assert body.count(old)==1,'BLOCKED_UNEXPECTED_CONTROLLER_'+str(i)
    patched=body.replace(old,new,1)
    ast.parse(patched)
    original[p]=(body,patched)
for i,(p,(before,after)) in enumerate(original.items()):
    backup=diag/('main-before-qwen27-gpu100-'+str(i)+'.py')
    backup.write_text(before);os.chmod(backup,0o600)
def put(path,src):
    tmp=path.with_name(path.name+'.aj-gpuoffload-tmp')
    try:
        tmp.write_text(src);os.chmod(tmp,0o644);tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)
def restart():
    p=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],capture_output=True,timeout=240)
    if p.returncode:raise RuntimeError('PANEL_RESTART_FAILED')
try:
    for p,(old_src,new_src) in original.items():put(p,new_src)
    restart()
    try:
        with urllib.request.urlopen('http://127.0.0.1:7860/',timeout=15) as r:status=r.status
    except urllib.error.HTTPError as e:status=e.code
    assert status==401,'PANEL_HEALTH_FAILED'
    with urllib.request.urlopen('http://127.0.0.1:8189/system_stats',timeout=15) as r:assert r.status==200
except Exception:
    for p,(old_src,_) in original.items():put(p,old_src)
    restart()
    print('ROLLED_BACK',flush=True)
    raise
print('GPU_OFFLOAD_PATCHED',len(files),'QWEN35_27B_BLOCKS',64,'GPU_LAYER_CAP',100,flush=True)
print('NO_GENERATION_SUBMITTED',flush=True)
