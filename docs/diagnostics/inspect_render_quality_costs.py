"""Read-only performance contract: render dimensions/steps, output sizes, stage event timing."""
import json,urllib.request,collections,pathlib,subprocess,time
def load(port,route):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}'+route,timeout=12) as r:return json.load(r)
state=json.loads(pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json').read_text())
jobs=state if isinstance(state,list) else state.get('queue',[])
print('STATE_COUNTS',dict(collections.Counter(str(j.get('status')) for j in jobs)),flush=True)
a=sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True)
for idx,j in enumerate(a[:13]):
    if idx>3 and j.get('status') not in ('completed','render_queued_auto'):continue
    print('JOB',idx,'STATUS',j.get('status'),'PROFILE',j.get('profile'),
          'KIND',j.get('prompt_kind'),'WARNING',str(j.get('render_cleanup_warning') or '')[:80],
          'TIME_TO_READY',round(j['prompt_ready_at']-j['created_at']) if j.get('prompt_ready_at') else None,
          'RENDER_DURATION',round(j['finished_at']-j['render_started_at']) if j.get('finished_at') and j.get('render_started_at') else None,flush=True)
for port in (8188,8189):
    print('SERVICE',port,flush=True)
    q=load(port,'/queue')
    print('RUNNING',len(q.get('queue_running',[])),'PENDING',len(q.get('queue_pending',[])),flush=True)
    h=load(port,'/history?max_items=3')
    for index,obj in enumerate(h.values()):
        graph=obj.get('prompt')
        graph=graph[2] if isinstance(graph,list) and len(graph)>2 else {}
        stamps=[]
        for msg in obj.get('status',{}).get('messages',[]):
            if isinstance(msg,(list,tuple)) and len(msg)==2 and isinstance(msg[1],dict):
                kind,d=msg
                if 'timestamp' in d:
                    stamps.append((kind,int(d['timestamp'])))
        print('EXEC',index,'STATUS',obj.get('status',{}).get('status_str'),
              'EVENT_TYPES',list(dict.fromkeys(x[0] for x in stamps)),
              'TOTAL_TIME_SEC',round((max(t for _,t in stamps)-min(t for _,t in stamps))/1000,1) if len(stamps)>=2 else None,
              flush=True)
        if port==8188 and index==0:
            for nid,n in graph.items():
                typ=n.get('class_type','')
                if typ in ('SamplerCustomAdvanced','KSampler','EmptyLatentVideo','CreateVideo','MiniMaxH3ReferenceToVideo','H3VideoSampler','H3FrameSampler'):
                    inp=n.get('inputs',{})
                    safe={k:inp.get(k) for k in ('steps','width','height','length','num_frames','frame_count','fps','frame_rate','cfg','denoise','skip_frames','batch_size','num_steps','total_frames','resolution') if isinstance(inp.get(k),(int,float,str))}
                    print('RENDER_NODE',typ,nid,safe,flush=True)
        if port==8189 and index==0:
            for nid,n in graph.items():
                if n.get('class_type') in ('AJPromptTextProcessor','AJGatedCompilerTextProcessor'):
                    if nid in ('2551:2640:2445','2551:2640:4275'):
                        print('WRITER',nid,'MAX_TOKENS',n['inputs'].get('max_tokens'),'REASONING',n['inputs'].get('reasoning'),flush=True)
config=pathlib.Path('/proc')
for p in config.iterdir():
    if not p.name.isdigit():continue
    try:
        cmd=(p/'cmdline').read_bytes()
        if b'uvicorn' in cmd or (b'app.main:app' in cmd):
            env=(p/'environ').read_bytes().split(b'\0')
            opts=[x for x in env if x.startswith(b'H3_PROMPT_PREFETCH=')]
            print('PREFETCH_SETTING',opts[0].decode() if opts else 'DEFAULT',flush=True)
            break
    except Exception:pass
