"""Read-only correlate failed step-selector jobs with reused analysis mode."""
import json,pathlib,collections
root=pathlib.Path("/workspace/H3_VAST_MOBILE/state")
d=json.loads((root/"queue.json").read_text())
jobs=d if isinstance(d,list) else d.get("queue",[])
jobs=sorted(jobs,key=lambda j:j.get("created_at",0),reverse=True)
for i,j in enumerate(jobs[:42]):
    if j.get("status") not in ("prompt_failed","prompt_running","prompt_queued","completed"):
        continue
    err=str(j.get("error",""))
    if j.get("status") not in ("prompt_failed","completed","prompt_running"):
        continue
    path=root/("job_"+str(j.get("id"))+"_prompt.api.json")
    print("JOB",i,"STATUS",j.get("status"),"KIND",j.get("prompt_kind"),
          "CACHE_MODE",bool(j.get("analysis_cache_key")),
          "SELECTOR_ERROR","Pinned production step selector" in err,
          "HAS_API",path.is_file(),flush=True)
    if path.is_file():
        try:
            g=json.loads(path.read_text())
            ids=["2551:5422:5067","2551:5422:5068","2551:5422:5069","2551:5422:5070"]
            print(" GRAPH_SELECTOR_PRESENCE",[int(n in g) for n in ids],
                  "VISION_STAGES",[int(n in g) for n in ("2551:2640:2441","2551:2640:2447")],
                  "DURABLE_NODES",sum(k.startswith("aj_analysis_") for k in g),
                  flush=True)
        except Exception as e:
            print(" GRAPH_READ_FAILED",type(e).__name__,flush=True)
