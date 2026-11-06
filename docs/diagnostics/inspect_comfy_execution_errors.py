"""Inspect recent ComfyUI execution exceptions without exposing prompts or user assets."""
import collections,json,pathlib,re,urllib.request,urllib.error
root=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
d=json.loads(root.read_text())
jobs=d if isinstance(d,list) else d.get('queue',[])
latest=sorted(jobs,key=lambda j:j.get('created_at',0))[-12:]
ids=[]
for j in latest:
 if j.get('prompt_prompt_id') and j.get('profile') in ('h3_full','10eros_full'):
  ids.append(j['prompt_prompt_id'])
print('RECENT_ACCEPTED',len(ids),flush=True)
fingerprints=collections.Counter()
for pid in ids[:5]:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=8) as response:
   h=json.load(response)
  obj=h.get(pid,{})
  status=obj.get('status',{})
  messages=status.get('messages',[])
  print('HISTORY_STATUS',status.get('status_str'),'COMPLETED',status.get('completed'),'MESSAGES',len(messages))
  for typ,detail in messages:
   if typ!='execution_error' or not isinstance(detail,dict):continue
   safe={k:detail.get(k) for k in ('node_id','node_type','exception_type')}
   message=detail.get('exception_message','')
   # Normalize potentially user-generated strings, while retaining exception signature.
   if isinstance(message,str):
    text=message.splitlines()[0][:500]
    text=re.sub(r'(["\']).*?\1','[quoted-value]',text)
    text=re.sub(r'\b[0-9a-fA-F]{32,}\b','[hash]',text)
    safe['exception_signature']=text
   print('EXECUTION_ERROR',json.dumps(safe,ensure_ascii=False))
   trace=detail.get('traceback',[])
   if isinstance(trace,list):
    for line in trace[-5:]:
     if not isinstance(line,str):continue
     if re.search(r'(?:raise |Error:|Exception:|File ")',line) and not re.search(r'(?:password|token|Authorization|HF_)',line,re.I):
      print('TRACE_CLASS',re.sub(r'(["\']).*?\1','[quoted-value]',line.strip())[:340])
   fingerprints[(safe.get('node_type'),safe.get('exception_type'),safe.get('exception_signature'))]+=1
 except Exception as e:
  print('HISTORY_READ_ERROR',type(e).__name__)
print('ERROR_GROUPS',json.dumps([{'node_type':x[0],'exception_type':x[1],'signature':x[2],'count':v} for x,v in fingerprints.items()],ensure_ascii=False)[:5000])
p=pathlib.Path('/workspace/comfyui-prompt.log')
if p.exists():
 lines=p.read_text(errors='replace').splitlines()
 print('PROMPT_LOG_LINES',len(lines))
 for line in lines[-220:]:
  if re.search(r'(?:Traceback|Error:|Exception:|Failed to|ERROR|CUDA out of memory|llama|missing)',line,re.I) and not re.search(r'(?:password|Authorization|bearer|HF_TOKEN)',line,re.I):
   print('LOG_MATCH',re.sub(r'(["\']).*?\1','[quoted-value]',line.strip())[:350])
