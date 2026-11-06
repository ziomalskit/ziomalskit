"""Read-only latest AJ prompt failures; no user prompt or reference text."""
import json,pathlib,re,urllib.request,collections
state=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=state if isinstance(state,list) else state.get('queue',[])
jobs=sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True)
print('TOTAL_JOBS',len(jobs))
print('LATEST_STATUSES',dict(collections.Counter(str(j.get('status')) for j in jobs[:30])))
def scrub(text):
    text=str(text or '').splitlines()[0][:350]
    text=re.sub(r"""(["']).*?\1""",'[quoted]',text)
    text=re.sub(r'\b[0-9a-fA-F]{32,}\b','[id]',text)
    return text
for idx,j in enumerate(jobs[:8]):
    print('JOB',idx,'status',j.get('status'),'profile',j.get('profile'),
          'error',scrub(j.get('error')),'prompt_accepted',bool(j.get('prompt_prompt_id')))
    pid=j.get('prompt_prompt_id')
    if not pid:continue
    try:
        with urllib.request.urlopen('http://127.0.0.1:8189/history/'+str(pid),timeout=6) as r:
            hist=json.load(r).get(str(pid),{})
        st=hist.get('status',{})
        print('  HISTORY',st.get('status_str'),'completed',st.get('completed'))
        for typ,detail in st.get('messages',[]):
            if typ=='execution_error' and isinstance(detail,dict):
                print('  ERROR',detail.get('node_id'),detail.get('node_type'),
                      detail.get('exception_type'),scrub(detail.get('exception_message')))
    except Exception as exc:
        print('  HISTORY_UNAVAILABLE',type(exc).__name__)
for port in (8188,8189):
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=7) as r:q=json.load(r)
        print('COMFY_QUEUE',port,'running',len(q.get('queue_running',[])),'pending',len(q.get('queue_pending',[])))
    except Exception as exc:print('COMFY_UNAVAILABLE',port,type(exc).__name__)
