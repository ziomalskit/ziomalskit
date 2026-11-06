"""Read-only inspect last ComfyUI failed prompt graph routing. Never print input text."""
import json,pathlib,urllib.request
f=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
doc=json.loads(f.read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
item=next(j for j in sorted(jobs,key=lambda x:x.get('created_at',0),reverse=True) if j.get('prompt_prompt_id'))
pid=item['prompt_prompt_id']
with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=15) as response:
 data=json.load(response)[pid]
prompt=data.get('prompt')
print('HISTORY_PROMPT_KIND',type(prompt).__name__,len(prompt) if isinstance(prompt,list) else 'NA')
graph=prompt[2] if isinstance(prompt,list) and len(prompt)>2 else {}
print('NODE_COUNT',len(graph))
for key in list(data.get('outputs',{})):
 node=graph.get(key,{})
 print('OUTPUT_NODE',key,'CLASS',node.get('class_type'),'TITLE',str(node.get('_meta',{}).get('title',''))[:100])
for key in ('aj_creative_timeline','2551:2640:2445','aj_final_timeline'):
 node=graph.get(key,{})
 print('NODE',key,'CLASS',node.get('class_type'),'TITLE',str(node.get('_meta',{}).get('title',''))[:100])
 for name,val in node.get('inputs',{}).items():
  if isinstance(val,list) and len(val)==2 and isinstance(val[0],str):
   print(' EDGE',name, 'FROM',val[0],'SLOT',val[1])
  elif name in ('system_prompt','model','mmproj','reasoning','max_tokens'):
   print(' NONCONTENT_FIELD',name,str(val)[:150])
