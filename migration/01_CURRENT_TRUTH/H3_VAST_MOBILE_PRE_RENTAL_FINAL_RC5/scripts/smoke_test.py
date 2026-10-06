#!/usr/bin/env python3
import base64,json,os,subprocess,sys,urllib.request
from pathlib import Path
PANEL=Path(os.getenv("PANEL_ROOT","/workspace/H3_VAST_MOBILE")); COMFY=Path(os.getenv("COMFY_ROOT","/workspace/ComfyUI")); INPUT=COMFY/"input"
def get(url,auth=False):
 h={}
 if auth:
  u=os.getenv("H3_PANEL_USER","h3");p=os.getenv("H3_PANEL_PASSWORD","");h["Authorization"]="Basic "+base64.b64encode(f"{u}:{p}".encode()).decode()
 with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=30) as r:return json.load(r)
assert get("http://127.0.0.1:7860/api/config",True)["batch"]=={"prompts_per_batch":10,"auto_approved":5,"review":5}
sys.path.insert(0,str(PANEL)); import app.main as m
imgs=[f"__h3_preflight_{i}.png" for i in range(1,7)]
for x in imgs: assert (INPUT/x).exists(),x
fake={"id":"smoke","render_seed":12345,"prompt_seed":67890,"analysis_seed":24680,"context":{"prompt":"smoke test","model":"native_int8","soft_timeout_minutes":8,"hard_restart_after_seconds":90,"pictures":imgs,"audio":[],"loras":[]}}
prompt=m.patch_workflow(fake,m.PROMPT_ONLY); render=m.patch_workflow(fake,m.MASTER,approved_prompt="smoke final prompt")
for path in (prompt,render):
 w=json.loads(path.read_text()); n={x["id"]:x for x in w["nodes"]}; assert n[1854]["widgets_values"][0]==12345; assert n[6067]["widgets_values"][0]==12346; assert n[7360]["widgets_values"][0]==24680
pw=json.loads(prompt.read_text())
def all_nodes(doc):
 for x in doc.get("nodes",[]): yield x
 for sg in doc.get("definitions",{}).get("subgraphs",[]): yield from all_nodes(sg)
nodes={x["id"]:x for x in all_nodes(pw)}
ctrl=nodes[4022]
assert ctrl["widgets_values_named"]["value_4"]==24680
assert ctrl["widgets_values_named"]["value_4_1"]==24681
assert ctrl["widgets_values_named"]["value_4_2"]==67890
assert ctrl["widgets_values_named"]["value_4_3"]==67891
assert ctrl["widgets_values"][5]==24680 and ctrl["widgets_values"][9]==24681
assert ctrl["widgets_values"][13]==67890 and ctrl["widgets_values"][17]==67891
assert nodes[3423]["widgets_values_named"]["value_3"] is False
for name,url,path in [("prompt","http://127.0.0.1:8189",prompt),("render","http://127.0.0.1:8188",render)]:
 env=os.environ.copy(); env["COMFY_LOCAL_URL"]=url; env["COMFY_WHERE"]="local"
 a=subprocess.run(["comfy","--json","workflow","validate","--workflow",str(path)],env=env,text=True,capture_output=True); print(name,"validate",a.stdout[-2500:]);
 if a.returncode: print(a.stderr); raise SystemExit(f"{name} validate failed")
 try:
  envl=json.loads([x for x in a.stdout.splitlines() if x.strip()][-1])
  if not envl.get("ok"): raise RuntimeError(envl)
  data=envl.get("data") or {}
  if not data.get("converted_from_ui"): raise RuntimeError("workflow was not converted from UI format")
 except Exception as e:
  raise SystemExit(f"{name} validate envelope invalid: {e}")
 b=subprocess.run(["comfy","--json","run","--workflow",str(path),"--print-prompt"],env=env,text=True,capture_output=True); print(name,"convert",b.stdout[-2500:]);
 if b.returncode: print(b.stderr); raise SystemExit(f"{name} UI->API conversion failed")
print("SMOKE/CONVERSION PASSED — no real render submitted")
