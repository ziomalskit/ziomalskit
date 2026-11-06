"""Read-only identify failed jobs alongside an active run. No personal content."""
import json,pathlib,collections,time,urllib.request,re
p=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
root=json.loads(p.read_text())
jobs=root if isinstance(root,list) else root.get('queue',[])
jobs=sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True)
print('TOTAL',len(jobs),'STATUSES_LAST_50',dict(collections.Counter(str(j.get('status')) for j in jobs[:50])),flush=True)
def safe(s):
  msg=(str(s or '').splitlines() or [''])[0][:220]
  return re.sub(r'["\x27].*?["\x27]','[value]',msg)
for i,j in enumerate(jobs[:35]):
  status=j.get('status')
  if status not in ('prompt_failed','render_failed','completed','prompt_running','render_running'):
    continue
  print('JOB',i,'STATUS',status,'PROFILE',j.get('profile'),
        'HAS_FINAL',bool(j.get('final_h3_prompt')),
        'HAS_RENDER',bool(j.get('video_outputs')),
        'ERROR',safe(j.get('error')),
        'PROMPT_ID',bool(j.get('prompt_prompt_id')),
        'RENDER_ID',bool(j.get('render_prompt_id')),flush=True)
  if status not in ('prompt_failed','render_failed') or not j.get('prompt_prompt_id'):continue
  try:
    pid=str(j['prompt_prompt_id'])
    with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=5) as r:obj=json.load(r).get(pid,{})
    for kind,x in obj.get('status',{}).get('messages',[]):
      if kind=='execution_error':print('ERR',i,x.get('node_id'),x.get('node_type'),x.get('exception_type'),safe(x.get('exception_message')),flush=True)
  except Exception as exc:print('ERR_HISTORY_MISSING',i,type(exc).__name__,flush=True)
