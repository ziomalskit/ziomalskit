"""Read-only inspect creative guard dependency chain. No text values."""
import json,pathlib,urllib.request,collections
doc=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
pid=next(j['prompt_prompt_id'] for j in sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True) if j.get('prompt_prompt_id'))
with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=10) as r:h=json.load(r)[pid]
g=h['prompt'][2]
start='aj_creative_timeline'
seen=set()
def walk(key,depth=0):
 if key in seen or depth>11:return
 seen.add(key)
 node=g.get(key,{})
 print('NODE',depth,key,node.get('class_type'),str(node.get('_meta',{}).get('title',''))[:85])
 for name,value in node.get('inputs',{}).items():
  if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in g:
   print(' LINK',depth,name,'FROM',value[0],value[1])
   walk(value[0],depth+1)
  elif name in ('model','mmproj','reasoning','enable_processing','max_tokens','extra_args'):
   if name!='extra_args':print(' PARAM',depth,name,str(value)[:85])
walk(start)
print('CHAIN_NODES',len(seen))
