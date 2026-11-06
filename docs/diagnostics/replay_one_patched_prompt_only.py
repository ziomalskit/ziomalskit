"""Run one existing prompt-only graph with updated vision budgets; never emit user content."""
import collections,json,os,pathlib,re,time,uuid,urllib.request,urllib.error
os.umask(0o077)
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
state=pathlib.Path('/workspace/.aj-diagnostics')
q=json.loads((root/'state/queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
counts=collections.Counter(str(j.get('status')) for j in jobs)
terminal={'prompt_failed','render_failed','completed','cancelled','rejected','prompt_stuck_skipped',
          'render_stuck_skipped','review_skipped_shutdown'}
assert not any(x not in terminal for x in counts),'BLOCKED_NONTERMINAL_QUEUE'
assert jobs,'BLOCKED_NO_PREVIOUS_JOBS'
base='http://127.0.0.1:8189'
def get(route):
    with urllib.request.urlopen(base+route,timeout=12) as resp:return json.load(resp)
current=get('/queue')
assert not current.get('queue_running') and not current.get('queue_pending'),'BLOCKED_COMFY_BUSY'
recent=sorted((j for j in jobs if j.get('prompt_prompt_id')),key=lambda j:str(j.get('created_at','')),reverse=True)
assert recent,'BLOCKED_NO_HISTORY_JOB'
job=recent[0]
pid=str(job['prompt_prompt_id'])
old=get('/history/'+pid).get(pid,{})
history_prompt=old.get('prompt')
assert isinstance(history_prompt,list) and len(history_prompt)>2,'BLOCKED_NO_EXECUTABLE_GRAPH'
graph=history_prompt[2]
assert isinstance(graph,dict),'BLOCKED_BAD_GRAPH'
errs=[x[1] for x in old.get('status',{}).get('messages',[])
      if isinstance(x,(list,tuple)) and len(x)==2 and x[0]=='execution_error']
assert any(x.get('node_id')=='2551:2640:2441' for x in errs),'BLOCKED_NOT_KNOWN_STEP1_FAILURE'
for nid in ('2551:2640:2441','2551:2640:2447'):
    n=graph[nid]
    assert n['class_type']=='AJPromptTextProcessor','BLOCKED_NODE_TYPE'
    inputs=n['inputs']
    assert (inputs['ctx_size'],inputs['max_tokens'])==(32768,2048),'BLOCKED_UNEXPECTED_OLD_LIMITS'
    inputs['ctx_size']=65536
    inputs['max_tokens']=8192
print('PREVIOUS_FAILURE_REPLAY_PROMPT_ONLY',len(graph),'nodes','NEW_CONTEXT',65536,'OUTPUT',8192,flush=True)
req=urllib.request.Request(base+'/prompt',data=json.dumps({'prompt':graph,'client_id':uuid.uuid4().hex}).encode(),
                          headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=60) as rsp:submitted=json.load(rsp)
except urllib.error.HTTPError as e:
    print('SUBMISSION_HTTP_ERROR',e.code,flush=True)
    raise
assert isinstance(submitted.get('prompt_id'),str),'BLOCKED_SUBMISSION_NO_ID'
newid=submitted['prompt_id']
(state/'last-targeted-prompt-id.txt').write_text(newid+'\n')
print('ONE_PROMPT_ACCEPTED',flush=True)
deadline=time.monotonic()+900
start=time.monotonic()
while time.monotonic()<deadline:
    row=get('/history/'+newid).get(newid)
    if row and row.get('status',{}).get('completed') is not None:
        st=row['status']
        print('RESULT_STATUS',st.get('status_str'),'COMPLETED',st.get('completed'),'ELAPSED_S',int(time.monotonic()-start),flush=True)
        errors=[entry[1] for entry in st.get('messages',[]) if isinstance(entry,(list,tuple))
                and len(entry)==2 and entry[0]=='execution_error' and isinstance(entry[1],dict)]
        for err in errors:
            raw=str(err.get('exception_message',''))
            if 'lacks natural, untruncated completion evidence' in raw:label='NON_NATURAL_COMPLETION'
            elif 'context_overflow' in raw:label='CONTEXT_OVERFLOW'
            elif 'HTTP_400' in raw:label='LLAMA_HTTP_400'
            elif 'out of memory' in raw.lower():label='GPU_OOM'
            else:label='OTHER_ERROR'
            print('ERROR',err.get('node_id'),err.get('node_type'),err.get('exception_type'),label,flush=True)
        if st.get('completed') is True and st.get('status_str')=='success':
            print('PROMPT_ONLY_E2E_PASS','OUTPUT_NODE_COUNT',len(row.get('outputs',{})),flush=True)
        break
    time.sleep(8)
else:
    print('PROMPT_ONLY_INCOMPLETE_AFTER_900_SECONDS',flush=True)
