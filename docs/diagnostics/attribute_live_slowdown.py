"""Read-only workload attribution, no prompt text or user images."""
import json, pathlib, subprocess, urllib.request, collections, os, time
R=pathlib.Path("/workspace/H3_VAST_MOBILE")
def get(url):
    with urllib.request.urlopen(url,timeout=12) as r:return json.load(r)
def command(c):
    p=subprocess.run(c,capture_output=True,text=True,timeout=10)
    return p.stdout.strip() if p.returncode==0 else ""
q=get("http://127.0.0.1:8189/queue")
print("ACTIVE_PROMPT",len(q.get("queue_running",[])),len(q.get("queue_pending",[])),flush=True)
j=json.loads((R/"state/queue.json").read_text())
jobs=j if isinstance(j,list) else j.get("queue",[])
recent=sorted(jobs,key=lambda x:x.get("created_at",0),reverse=True)[:25]
for i,x in enumerate(recent):
    if x.get("status") not in ("completed","prompt_running","render_queued_auto","prompt_failed"):continue
    v={k:x.get(k) for k in ("status","prompt_kind","render_cleanup_warning","prompt_started_at",
       "prompt_ready_at","render_started_at","render_finished_at","created_at","finished_at",
       "prompt_watchdog_last_progress_at","render_watchdog_last_progress_at") if k in x}
    for key in list(v):
        if key.endswith("_at") and isinstance(v[key],(float,int)):v[key]=round(time.time()-v[key])
    if "render_cleanup_warning" in v:
        v["render_cleanup_warning"]=str(v["render_cleanup_warning"])[:110]
    print("AJ_JOB",i,v,flush=True)
for port in (8188,8189):
    try:
        h=get("http://127.0.0.1:"+str(port)+"/history?max_items=5")
        for idx,obj in enumerate(h.values()):
            graph=obj.get("prompt")
            graph=graph[2] if isinstance(graph,(list,tuple)) and len(graph)>2 else {}
            counts={}
            for node in graph.values():
                typ=node.get("class_type","")
                if any(k in typ.lower() for k in ("prompttext","compilertext","creative","timeline","joycaption","video","sampler")):
                    counts[typ]=counts.get(typ,0)+1
            select={}
            for nid in ("2551:2640:2441","2551:2640:2447","2551:2640:2445","2551:2640:4275"):
                node=graph.get(nid,{})
                if node:
                    ins=node.get("inputs",{})
                    select[nid]={k:ins.get(k) for k in ("model","max_tokens","reasoning","ctx_size","n_gpu_layers","timeout_seconds") if k in ins}
            print("HISTORY",port,idx,obj.get("status",{}).get("status_str"),"selected_nodes",select,"classes",counts,flush=True)
    except Exception as e:print("HISTORY_ERROR",port,type(e).__name__,flush=True)
gpus=command(["nvidia-smi","--query-compute-apps=pid,used_gpu_memory","--format=csv,noheader,nounits"])
for row in gpus.splitlines():
    parts=[x.strip() for x in row.split(",")]
    if len(parts)<2 or not parts[0].isdigit():continue
    pid=int(parts[0])
    try:
        cmd=(pathlib.Path("/proc")/str(pid)/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace")
    except Exception:cmd=""
    labels=[]
    for t in ("llama-server","ComfyUI","8188","8189","python","aj","torch"):
        if t.lower() in cmd.lower():labels.append(t)
    stat=pathlib.Path("/proc")/str(pid)/"stat"
    try:
        values=stat.read_text().split()
        pp=int(values[3])
    except Exception:pp=-1
    print("GPU_PROC",pid,"vram_mib",parts[-1],"ppid",pp,"tags",labels,flush=True)
print("TOP_CPU",flush=True)
for line in command(["ps","-eo","pid,ppid,pcpu,pmem,etime,comm","--sort=-pcpu"]).splitlines()[:17]:
    print("CPU",line.strip(),flush=True)
print("NCPU",os.cpu_count(),flush=True)
p=pathlib.Path("/workspace/.aj-diagnostics/aj_idle_vram_reclaimer.log")
if p.exists():print("RECLAIMER_LOG",p.read_text(errors="replace").splitlines()[-12:],flush=True)
