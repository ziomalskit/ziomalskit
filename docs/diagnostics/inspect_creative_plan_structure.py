"""Read-only structural inspection of Step 3 output; never print creative prose."""
import collections,json,pathlib,re,urllib.request
q=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=q if isinstance(q,list) else q.get('queue',[])
jobs=[j for j in sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True) if j.get('prompt_prompt_id')][:3]
for j in jobs:
 pid=j['prompt_prompt_id']
 with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=10) as f: obj=json.load(f)[pid]
 outputs=obj.get('outputs',{})
 print('JOB',j.get('profile'),'OUTPUT_NODES',list(outputs.keys())[:30],'COUNT',len(outputs),flush=True)
 for key,output in outputs.items():
  if not isinstance(output,dict):continue
  for field,value in output.items():
   if field not in ('text','string','result') and field!='ui':continue
   strings=[]
   def gather(item):
    if isinstance(item,str):strings.append(item)
    elif isinstance(item,(list,tuple)):
     for x in item:gather(x)
    elif isinstance(item,dict):
     for name,sub in item.items():
      if name in ('text','string','result','value'):gather(sub)
   gather(value)
   for s in strings:
    if len(s)<100:continue
    canonical=re.findall(r'(?m)^\[Shot ([1-9][0-9]*)\]',s)
    rough=re.findall(r'(?im)^\s{0,5}(?:\#{1,4}\s*)?\[?shot\s*#?\s*(\d+)\]?',s)
    timeline=re.findall(r'(?m)^Timeline:',s)
    print('TEXT_SHAPE','node',key,'field',field,'chars',len(s),'canonical',canonical[:25],'rough',rough[:25],
          'timeline_count',len(timeline),'shot_directive_count',len(re.findall(r'(?i)\[shot',s)),
          'contains_fenced_code',bool(re.search(r'(?m)^\x60{3}',s)),
          'newline_count',s.count('\n'),flush=True)
