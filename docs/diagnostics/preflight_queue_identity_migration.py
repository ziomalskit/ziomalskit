"""Read-only inspect queue identity relationships and snapshot integrity before a safe panel-only patch."""
import json,pathlib,collections,hashlib
p=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
q=json.loads(p.read_text());jobs=q.get('queue',[]) if isinstance(q,dict) else q
batches=q.get('batches',[]) if isinstance(q,dict) else []
print('QUEUE_COUNT',len(jobs),'BATCH_COUNT',len(batches),'STATUS_COUNTS',dict(collections.Counter(x.get('status') for x in jobs)),flush=True)
for name,records in (('job',jobs),('batch',batches)):
  for x in sorted(records,key=lambda z:z.get('created_at',0),reverse=True):
    ctx=x.get('context')
    if not isinstance(ctx,dict) or x.get('profile')!='10eros_full':continue
    print('CONTEXT_EXAMPLE',name,'status',x.get('status'),'keys',sorted(ctx.keys()),
          'workflow_id',ctx.get('workflow_id'),'adapter_id_prefix',str(ctx.get('workflow_adapter_identity'))[:10],
          'render_id_prefix',str(ctx.get('workflow_render_identity'))[:10],flush=True)
    break
print('PUBLICATION_FILE',(p.parent/'queue.publication.json').exists(),flush=True)
print('JOBS_ACTIVE',sum(x.get('status') in {'prompt_running','render_running','prompt_preparing','render_preparing','prompt_submitting','render_submitting'} for x in jobs),flush=True)
