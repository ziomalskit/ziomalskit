"""Read-only compare CustomCombo selection metadata in UI and API. No user prompts."""
import json,pathlib,urllib.request
root=pathlib.Path('/workspace/H3_VAST_MOBILE/state')
doc=json.loads((root/'queue.json').read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
job=next(j for j in sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True) if j.get('prompt_prompt_id'))
pid=job['prompt_prompt_id']
with urllib.request.urlopen('http://127.0.0.1:8189/history/'+pid,timeout=10) as r:graph=json.load(r)[pid]['prompt'][2]
ui=json.loads((root/('job_'+str(job['id'])+'_prompt.json')).read_text())
interesting={5067,5068,5069,5070}
def graphs(g):
 yield g
 for s in g.get('definitions',{}).get('subgraphs',[]):yield from graphs(s)
for scope in graphs(ui):
 for node in scope.get('nodes',[]):
  if node.get('id') not in interesting or node.get('type')!='CustomCombo':continue
  nodeid=node['id']
  v=node.get('widgets_values_named',{})
  allowed=('choice','index','option1','option2','option3','option4','option5')
  print('UI_COMBO',nodeid,{k:str(v[k])[:120] for k in allowed if k in v},'SERIALIZED_COUNT',len(node.get('widgets_values',[])))
for key in ('2551:5422:5067','2551:5422:5068','2551:5422:5069','2551:5422:5070',
            '2551:4799:5296:5294','2551:4799:5296:5293','2551:4799:5296:5292'):
 n=graph.get(key,{})
 print('API_NODE',key,n.get('class_type'),'INPUTS',{k:(v if k in ('choice','index','option1','option2','option3','option4','option5') else '[linked]' if isinstance(v,list) else type(v).__name__) for k,v in n.get('inputs',{}).items()})
print('RUNTIME_UI_FROM',str(root/('job_'+str(job['id'])+'_prompt.json'))[-38:])
