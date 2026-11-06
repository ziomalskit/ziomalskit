"""Deploy bounded Mistral vision analysis instruction and verify one prompt-only replay."""
import ast,collections,json,os,pathlib,subprocess,time,uuid,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=root/'state'
diag=pathlib.Path('/workspace/.aj-diagnostics')
queue=json.loads((state/'queue.json').read_text())
jobs=queue if isinstance(queue,list) else queue.get('queue',[])
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
assert all(j.get('status') in terminal for j in jobs),'BLOCKED_ACTIVE_AJ_JOB'
base='http://127.0.0.1:8189'
def get(route):
    with urllib.request.urlopen(base+route,timeout=15) as rsp:return json.load(rsp)
q=get('/queue')
assert not q.get('queue_running') and not q.get('queue_pending'),'BLOCKED_ACTIVE_COMFY_PROMPT'
candidate=None
for j in sorted(jobs,key=lambda x:str(x.get('created_at','')),reverse=True):
    path=state/('job_'+str(j.get('id'))+'_prompt.api.json')
    if j.get('status')=='prompt_failed' and path.is_file():
        candidate=path
        break
assert candidate is not None,'BLOCKED_NO_PERSISTED_GRAPH'
graph=json.loads(candidate.read_text())
assert isinstance(graph,dict) and len(graph)>200,'BLOCKED_NO_COMPLETE_GRAPH'
for nid in ('2551:2640:2441','2551:2640:2447'):
    n=graph[nid]
    assert n['class_type']=='AJPromptTextProcessor','BLOCKED_WRONG_NODE_TYPE'
    v=n['inputs']
    assert (v['ctx_size'],v['max_tokens']) in ((32768,2048),(65536,8192)),'BLOCKED_UNEXPECTED_CONTEXT'
    v['ctx_size']=65536
    v['max_tokens']=8192
paths=[root/'custom_nodes/aj_production/prompt_completion.py',
       pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/prompt_completion.py').resolve(strict=True)]
old="        text = Path(command[command.index('-f') + 1]).read_bytes().decode('utf-8')\n        content = text\n"
additional="""        text = Path(command[command.index('-f') + 1]).read_bytes().decode('utf-8')
        if str(values['model']).startswith('Mistral-Small-3.2-24B-Instruct-2506-'):
            text += (
                "\\n\\n[Vision analysis completion guidance]\\n"
                "Retain every required heading, reference, observed detail, and uncertainty. "
                "Write a complete, information-rich but concise analysis, aiming for 1200-1800 tokens "
                "and never more than 2400 tokens. Avoid repetition or additional commentary. "
                "End the answer immediately after its final required section.\\n"
            )
        content = text
"""
original={}
for p in paths:
    rp=p.resolve(strict=True)
    if rp in original:continue
    src=rp.read_text()
    assert src.count(old)==1 and 'from .compiler import natural_response, run_completion' in src,'BLOCKED_UNEXPECTED_PROMPT_NATIVE'
    candidate_src=src.replace(old,additional,1)
    ast.parse(candidate_src)
    original[rp]=(src,candidate_src)
assert len(original)>=1
for i,(p,(before,after)) in enumerate(original.items()):
    backup=diag/('prompt-completion-before-concise-'+str(i)+'.py')
    backup.write_text(before);os.chmod(backup,0o600)
def replace(p,text):
    tmp=p.with_name(p.name+'.aj-concise-stage-temp')
    try:
        tmp.write_text(text);os.chmod(tmp,0o644);tmp.replace(p)
    finally:tmp.unlink(missing_ok=True)
ctl=['bash',str(root/'scripts/service_ctl.sh'),'restart']
def restart(what):
    res=subprocess.run([*ctl,what],capture_output=True,timeout=270)
    if res.returncode:raise RuntimeError('RESTART_FAILED_'+what)
def health():
    for port,route,expected in ((8189,'/system_stats',200),(7860,'/',401)):
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(port)+route,timeout=15) as r:code=r.status
        except urllib.error.HTTPError as e:code=e.code
        if code!=expected:raise RuntimeError('HEALTH_'+str(port)+'_'+str(code))
def rollback():
    for p,(before,after) in original.items():replace(p,before)
    for service in ('prompt','panel'):
        try:restart(service)
        except Exception:pass
    print('CONCISE_GUIDANCE_ROLLED_BACK',flush=True)
try:
    for p,(before,after) in original.items():replace(p,after)
    restart('prompt')
    restart('panel')
    health()
except Exception:
    rollback()
    raise
print('CONCISE_MISTRAL_INSTRUCTION_DEPLOYED','TARGETS',len(original),flush=True)
req=urllib.request.Request(base+'/prompt',data=json.dumps({'prompt':graph,'client_id':uuid.uuid4().hex}).encode(),
                           headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=65) as r:submitted=json.load(r)
except Exception:
    rollback()
    raise
pid=submitted['prompt_id']
print('ONE_PROMPT_ONLY_REPLAY_ACCEPTED',flush=True)
deadline=time.monotonic()+720
while time.monotonic()<deadline:
    h=get('/history/'+pid).get(pid)
    if h and h.get('status',{}).get('completed') is not None:
        status=h['status']
        print('REPLAY_STATUS',status.get('status_str'),status.get('completed'),flush=True)
        errors=[x[1] for x in status.get('messages',[])
                if isinstance(x,(list,tuple)) and len(x)==2 and x[0]=='execution_error' and isinstance(x[1],dict)]
        if errors:
            e=errors[-1]; msg=str(e.get('exception_message',''))
            label='NON_NATURAL_COMPLETION' if 'natural, untruncated' in msg else 'CONTEXT_OVERFLOW' if 'context_overflow' in msg else 'OTHER'
            print('ERROR_NODE',e.get('node_id'),e.get('node_type'),e.get('exception_type'),label,flush=True)
            if e.get('node_id')=='2551:2640:2441':
                rollback()
        if status.get('completed') is True and status.get('status_str')=='success':
            print('PROMPT_ONLY_E2E_PASS','OUTPUT_COUNT',len(h.get('outputs',{})),flush=True)
        break
    time.sleep(8)
else:print('REPLAY_RUNNING_AFTER_OBSERVATION_WINDOW',flush=True)
