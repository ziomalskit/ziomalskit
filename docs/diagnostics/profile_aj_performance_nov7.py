"""Read-only AJ performance profile. Never print prompt text, paths, or credentials."""
import collections,json,os,pathlib,subprocess,time,urllib.request
ROOT=pathlib.Path('/workspace/H3_VAST_MOBILE')
def run(command,timeout=12):
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
        return p.stdout.strip() if p.returncode==0 else 'COMMAND_FAILED'
    except Exception as e:return 'ERROR_'+type(e).__name__
def state():
    data=json.loads((ROOT/'state/queue.json').read_text())
    return data if isinstance(data,list) else data.get('queue',[])
jobs=state()
jobs=sorted(jobs,key=lambda j:j.get('created_at',0),reverse=True)
print('TIME_UNIX',int(time.time()),flush=True)
print('LATEST_40_STATUSES',dict(collections.Counter(str(j.get('status')) for j in jobs[:40])),flush=True)
print('TOTAL_STATUS',dict(collections.Counter(str(j.get('status')) for j in jobs)),flush=True)
for index,j in enumerate(jobs[:18]):
    fields=('created_at','prompt_started_at','prompt_ready_at','prompt_finished_at',
            'render_started_at','render_finished_at','finished_at',
            'prompt_watchdog_last_progress_at','render_watchdog_last_progress_at')
    dates={f:round(float(j[f])) for f in fields if isinstance(j.get(f),(int,float)) and j[f]>1e9}
    times={}
    for label,start,end in (('to_prompt','created_at','prompt_started_at'),
                            ('prompt_seconds','prompt_started_at','prompt_ready_at'),
                            ('render_seconds','render_started_at','render_finished_at'),
                            ('end_to_end','created_at','finished_at')):
        if dates.get(start) and dates.get(end) and dates[end]>=dates[start]:
            times[label]=dates[end]-dates[start]
    print('JOB',index,'status',j.get('status'),'profile',j.get('profile'),
          'kind',j.get('prompt_kind'),'durations',times,
          'prompt_substate',j.get('prompt_submission_state'),
          'render_substate',j.get('render_submission_state'),
          'age_since_progress',int(time.time()-max(dates.get('prompt_watchdog_last_progress_at',0),dates.get('render_watchdog_last_progress_at',0))) if max(dates.get('prompt_watchdog_last_progress_at',0),dates.get('render_watchdog_last_progress_at',0)) else 'unknown',
          flush=True)
gpu=run(['nvidia-smi','--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.free,temperature.gpu,power.draw,clocks.sm,clocks.mem,clocks_throttle_reasons.active','--format=csv,noheader,nounits'])
print('GPU_TELEMETRY',gpu[:600],flush=True)
proc=run(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'])
print('GPU_PROCESSES',proc[:600],flush=True)
for port in (8188,8189):
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/queue',timeout=9) as r:q=json.load(r)
        print('COMFY_QUEUE',port,'running',len(q.get('queue_running',[])),
              'pending',len(q.get('queue_pending',[])),flush=True)
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/history?max_items=12',timeout=12) as r:hist=json.load(r)
        complete=[]
        for item in hist.values():
            messages=item.get('status',{}).get('messages',[])
            stamps={}
            for msg in messages:
                if not isinstance(msg,(list,tuple)) or len(msg)!=2 or not isinstance(msg[1],dict):continue
                kind,data=msg
                if kind in ('execution_start','execution_success','execution_error','execution_interrupted'):
                    ts=data.get('timestamp')
                    if type(ts) in (int,float):stamps[kind]=ts
            if stamps.get('execution_start') and len(stamps)>1:
                ends=[v for k,v in stamps.items() if k!='execution_start' and v>=stamps['execution_start']]
                if ends:
                    complete.append({'elapsed_seconds':round((min(ends)-stamps['execution_start'])/1000,1),
                                     'state':item.get('status',{}).get('status_str')})
        print('RECENT_COMFY_EXECUTIONS',port,complete[:10],flush=True)
    except Exception as e:print('COMFY_DIAG_ERROR',port,type(e).__name__,flush=True)
print('LOADAVG',run(['cat','/proc/loadavg']),flush=True)
print('CPU_PSI',run(['cat','/proc/pressure/cpu'])[:320],flush=True)
print('IO_PSI',run(['cat','/proc/pressure/io'])[:320],flush=True)
print('MEMORY_FREE_GB',round(os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')/(1024**3),1),flush=True)
print('DISK',run(['df','-h','/workspace']).splitlines()[-1:],flush=True)
