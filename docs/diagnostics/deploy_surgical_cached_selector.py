"""Surgical cached_text handling in the live panel; keep all other source unchanged."""
import ast, collections, hashlib, json, os, pathlib, subprocess, urllib.request, urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
p=root/'state/queue.json'
d=json.loads(p.read_text())
jobs=d if isinstance(d,list) else d['queue']
terminal={'completed','prompt_failed','render_failed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown',
          'stuck_skipped','failed'}
assert all(j.get('status') in terminal for j in jobs),'ACTIVE_JOBS_BLOCK_PATCH'
for port in (8188,8189):
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=10) as response:
        q=json.load(response)
    assert not q.get('queue_running') and not q.get('queue_pending'),'ACTIVE_COMFY_BLOCK_PATCH'
a='''            for node_id, expected_choice in stage_choices.items():
                stage_node = prepared.get(node_id)
                if (not isinstance(stage_node, dict) or stage_node.get("class_type") != "CustomCombo"
                        or stage_node.get("inputs") != {"choice": expected_choice}):
                    raise RuntimeError("Pinned production step selector changed; refusing implicit Disabled stage")
                stage_node["inputs"]["index"] = 1
'''
b='''            # In cached_text mode the durable Step 0-2 analysis has already
            # replaced the original writer branches. Pruned selectors do not
            # need fixing; surviving Step 3/4 selectors must still be pinned.
            fully_cached_analysis = set(analysis_texts or {}) == {
                "visual_facts", "expanded_intent", "reference_map"
            }
            for node_id, expected_choice in stage_choices.items():
                stage_node = prepared.get(node_id)
                if stage_node is None and fully_cached_analysis and node_id in {
                    "2551:5422:5067", "2551:5422:5068"
                }:
                    continue
                if (not isinstance(stage_node, dict) or stage_node.get("class_type") != "CustomCombo"
                        or stage_node.get("inputs") != {"choice": expected_choice}):
                    raise RuntimeError("Pinned production step selector changed; refusing implicit Disabled stage")
                stage_node["inputs"]["index"] = 1
'''
c='''            for node_id in ("2551:2640:2441", "2551:2640:2447"):
                node = prepared.get(node_id)
                if (not isinstance(node, dict) or node.get("class_type") != "AJPromptTextProcessor"
'''
e='''            for node_id in ("2551:2640:2441", "2551:2640:2447"):
                node = prepared.get(node_id)
                if node is None and fully_cached_analysis:
                    continue
                if (not isinstance(node, dict) or node.get("class_type") != "AJPromptTextProcessor"
'''
paths=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),root/'app/main.py']
original={}
for path in paths:
    source=path.read_text()
    digest=hashlib.sha256(source.encode()).hexdigest()
    assert digest=='789c4c35a36df7a22a7c5d94003cea0ab97321d6bbc4d611b510cf16bc478a9e','UNEXPECTED_LIVE_CHANGE'
    assert source.count(a)==1 and source.count(c)==1,'UNEXPECTED_GUARD_STRUCTURE'
    revised=source.replace(a,b,1).replace(c,e,1)
    ast.parse(revised)
    original[path]=(source,revised)
diag=pathlib.Path('/workspace/.aj-diagnostics')
diag.mkdir(mode=0o700,exist_ok=True)
for i,(path,(before,after)) in enumerate(original.items()):
    backup=diag/('before-cached-only-fix-'+str(i)+'.py')
    backup.write_text(before)
    os.chmod(backup,0o600)
def put(path,data):
    tmp=path.with_name(path.name+'.aj-cached-guard-tmp')
    try:
        tmp.write_text(data);os.chmod(tmp,0o644);tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)
def restart_panel():
    completed=subprocess.run(['bash',str(root/'scripts/service_ctl.sh'),'restart','panel'],
          capture_output=True,text=True,timeout=240)
    if completed.returncode:raise RuntimeError('PANEL_RESTART_FAILED')
try:
    for path,(before,after) in original.items():put(path,after)
    restart_panel()
    try:
        with urllib.request.urlopen('http://127.0.0.1:7860/',timeout=15) as r:code=r.status
    except urllib.error.HTTPError as error:code=error.code
    assert code==401,'PANEL_UNHEALTHY'
    for port in (8188,8189):
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/system_stats',timeout=10) as r:assert r.status==200
except Exception:
    for path,(before,after) in original.items():put(path,before)
    restart_panel()
    print('ROLLED_BACK',flush=True)
    raise
print('CACHED_SELECTOR_FIX_LIVE',len(paths),'FILES',flush=True)
print('ORIGINAL_LIVE_MODIFICATIONS_PRESERVED',flush=True)
print('PANEL_AND_COMFY_HEALTHY',flush=True)
print('NO_NEW_JOB_NO_GPU_INFERENCE',flush=True)
