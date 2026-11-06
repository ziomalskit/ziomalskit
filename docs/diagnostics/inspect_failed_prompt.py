"""Read-only failure classification; never publish user prompts or raw job/error bodies."""
import collections,hashlib,json,pathlib,re,time
p=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
if not p.is_file():
 print('QUEUE_MISSING');raise SystemExit(0)
doc=json.loads(p.read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
print('JOBS_TOTAL',len(jobs))
print('STATUS_COUNTS',dict(collections.Counter(str(j.get('status')) for j in jobs)))
patterns=[
 ('widget_schema',r'Incompatible catalog widget layout(?: at [0-9]+)?'),
 ('missing_node',r'Unregistered UI node class:\s*[A-Za-z0-9_]+'),
 ('comfy_cli',r'comfy-cli exited [0-9]+'),
 ('workflow_failure',r'WorkflowPreparationError'),
 ('capture_failure',r'Prompt capture failed'),
 ('no_final_prompt',r'successful prompt execution did not capture a final H3 prompt'),
 ('submission_rejected',r'SubmissionRejected'),
 ('validation',r'(?:validation|invalid|missing|required)'),
 ('network',r'(?:connection|timeout|refused|http error|503|502)'),
 ('comfy_execution',r'ComfyUI execution failed or was interrupted'),
]
for j in sorted(jobs,key=lambda x:x.get('created_at') or 0)[-15:]:
 err=str(j.get('error') or '')
 found=[key for key,rx in patterns if re.search(rx,err,re.I)]
 statuses={k:j.get(k) for k in ('status','profile','prompt_mode','active_service','prompt_submission_state','render_submission_state') if isinstance(j.get(k),(str,int,bool))}
 data={'state':statuses,'error_len':len(err),'error_fingerprint':hashlib.sha256(err.encode()).hexdigest()[:12] if err else None,'error_tags':found,
       'has_prompt_id':bool(j.get('prompt_prompt_id') or j.get('prompt_id')),
       'has_final_prompt':bool(j.get('final_h3_prompt'))}
 for key in ('created_at','finished_at','prompt_started_at'):
  value=j.get(key)
  if isinstance(value,(float,int)):data[key+'_ago_seconds']=round(time.time()-value,1)
 print('JOB',json.dumps(data,ensure_ascii=True))
for name in ('comfyui-prompt.log','comfyui-render.log'):
 f=pathlib.Path('/workspace')/name
 if f.exists():
  lines=f.read_text(errors='replace').splitlines()
  types=collections.Counter()
  for line in lines[-1800:]:
   for label,rx in patterns:
    if re.search(rx,line,re.I):types[label]+=1
  print('COMFY_LOG',name,'total_lines',len(lines),'recent_error_types',dict(types))
