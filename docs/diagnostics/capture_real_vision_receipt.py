"""Single real multimodal completion receipt probe; all text/images stay on instance."""
import ast,collections,json,os,pathlib,subprocess,time,uuid,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=pathlib.Path('/workspace/.aj-diagnostics')
native=pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/compiler.py').resolve(strict=True)
source=native.read_text()
needle="    choices = body.get('choices')\n"
assert source.count(needle)==1 and "def natural_response(body):" in source,'BLOCKED_UNEXPECTED_NATIVE'
queue=json.loads((root/'state/queue.json').read_text())
jobs=queue if isinstance(queue,list) else queue.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
assert all(j.get('status') in terminal for j in jobs),'BLOCKED_AJ_BUSY'
base='http://127.0.0.1:8189'
def get(route):
    with urllib.request.urlopen(base+route,timeout=15) as r:return json.load(r)
check=get('/queue')
assert not check.get('queue_running') and not check.get('queue_pending'),'BLOCKED_COMFY_BUSY'
pid=(state/'last-targeted-prompt-id.txt').read_text().strip()
history=get('/history/'+pid).get(pid,{})
assert history.get('status',{}).get('status_str')=='error','BLOCKED_NO_REPLAY_ERROR'
prompt=history.get('prompt',[])
assert isinstance(prompt,list) and len(prompt)>2 and isinstance(prompt[2],dict),'BLOCKED_NO_GRAPH'
graph=prompt[2]
for nid in ('2551:2640:2441','2551:2640:2447'):
    i=graph[nid]['inputs']
    assert (i['ctx_size'],i['max_tokens'])==(65536,8192),'BLOCKED_WRONG_REPLAY_SETTINGS'
log=state/'real-vision-receipts.jsonl'
log.unlink(missing_ok=True)
instrument="""    try:
        _d = detail if isinstance(detail, dict) else {}
        _chs = choices if isinstance(choices, list) else []
        _c = _chs[0] if _chs and isinstance(_chs[0], dict) else {}
        _m = _c.get('message', {}) if isinstance(_c.get('message', {}), dict) else {}
        _usage = body.get('usage', {}) if isinstance(body, dict) else {}
        _record = {'stop': _d.get('stop'), 'stop_type': _d.get('stop_type'),
                   'truncated': _d.get('truncated'), 'finish_reason': _c.get('finish_reason'),
                   'completion_tokens': _usage.get('completion_tokens') if isinstance(_usage, dict) else None,
                   'tokens_predicted': _d.get('tokens_predicted'),
                   'content_chars': len(str(_m.get('content') or '')),
                   'reasoning_chars': len(str(_m.get('reasoning_content') or '')),
                   'verbose_present': isinstance(detail, dict)}
        import os as _os
        _fd = _os.open('/workspace/.aj-diagnostics/real-vision-receipts.jsonl',
                       _os.O_WRONLY | _os.O_APPEND | _os.O_CREAT, 0o600)
        with _os.fdopen(_fd, 'a', encoding='utf-8') as _file:
            _file.write(json.dumps(_record, sort_keys=True) + '\\n')
    except Exception:
        pass
"""
patched=source.replace(needle,needle+instrument,1)
ast.parse(patched)
backup=state/'native-before-receipt-probe.py'
backup.write_text(source)
os.chmod(backup,0o600)
ctl=['bash',str(root/'scripts/service_ctl.sh'),'restart','prompt']
def restart():
    r=subprocess.run(ctl,capture_output=True,timeout=260)
    if r.returncode:raise RuntimeError('PROMPT_RESTART_FAILED')
def install(body):
    p=native.with_name(native.name+'.aj-diagnostic-temp')
    try:
        p.write_text(body);os.chmod(p,0o644);p.replace(native)
    finally:p.unlink(missing_ok=True)
try:
    install(patched)
    restart()
    with urllib.request.urlopen(base+'/system_stats',timeout=15) as r:assert r.status==200
    print('TEMPORARY_RECEIPT_PROBE_ACTIVE',flush=True)
    req=urllib.request.Request(base+'/prompt',
        data=json.dumps({'prompt':graph,'client_id':uuid.uuid4().hex}).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=65) as r:submitted=json.load(r)
    new_id=submitted['prompt_id']
    print('ONE_REPLAY_ACCEPTED',flush=True)
    deadline=time.monotonic()+550
    while time.monotonic()<deadline:
        record=get('/history/'+new_id).get(new_id)
        if record and record.get('status',{}).get('completed') is not None:
            st=record['status']
            print('REPLAY_STATUS',st.get('status_str'),st.get('completed'),flush=True)
            for entry in st.get('messages',[]):
                if isinstance(entry,(list,tuple)) and len(entry)==2 and entry[0]=='execution_error':
                    err=entry[1]
                    print('FAILED_NODE',err.get('node_id'),err.get('node_type'),err.get('exception_type'),flush=True)
            break
        time.sleep(8)
    else:print('REPLAY_OBSERVATION_TIMEOUT',flush=True)
finally:
    install(source)
    restart()
    print('ORIGINAL_NATIVE_RESTORED',flush=True)
if log.exists():
    lines=log.read_text().splitlines()
    print('RECEIPT_COUNT',len(lines),flush=True)
    for line in lines[-8:]:print('RECEIPT',line,flush=True)
else:
    print('NO_RECEIPTS_COLLECTED',flush=True)
