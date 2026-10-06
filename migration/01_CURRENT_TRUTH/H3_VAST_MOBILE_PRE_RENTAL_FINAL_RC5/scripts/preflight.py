#!/usr/bin/env python3
from __future__ import annotations
import base64,json,os,re,subprocess,sys,urllib.request
from pathlib import Path
PANEL=Path(os.getenv("PANEL_ROOT","/workspace/H3_VAST_MOBILE")); COMFY=Path(os.getenv("COMFY_ROOT","/workspace/ComfyUI"))
manifest=json.loads((PANEL/"config/models_manifest.json").read_text()); errors=[]; warnings=[]
def auth_headers():
 u=os.getenv("H3_PANEL_USER","h3"); pw=os.getenv("H3_PANEL_PASSWORD","");
 return {"Authorization":"Basic "+base64.b64encode(f"{u}:{pw}".encode()).decode()} if pw else {}
def get_json(url,auth=False):
 req=urllib.request.Request(url,headers=auth_headers() if auth else {})
 with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
def exists_any(paths): return any((COMFY/x).exists() for x in paths)
try:
 tag=subprocess.check_output(["git","-C",str(COMFY),"describe","--tags","--exact-match"],text=True,stderr=subprocess.STDOUT).strip(); print("ComfyUI",tag)
 if tag not in {"v0.38.0","0.38.0"}: errors.append(f"expected ComfyUI 0.38.0, got {tag}")
except Exception as e: errors.append(f"cannot verify ComfyUI version: {e}")
try:
 out=subprocess.check_output(["comfy","--version"],text=True,stderr=subprocess.STDOUT); print(out.strip())
 if "1.21.0" not in out: warnings.append("comfy-cli differs from tested 1.21.0")
except Exception as e: errors.append(f"comfy-cli unavailable: {e}")
try:
 out=subprocess.check_output(["nvcc","--version"],text=True); m=re.search(r"release\s+(\d+)\.(\d+)",out); v=tuple(map(int,m.groups())) if m else (0,0); print("CUDA toolkit",v)
 if v < (12,8): errors.append(f"CUDA toolkit >=12.8 required, got {v}")
except Exception as e: errors.append(f"nvcc missing: {e}")
for item in manifest["required_primary"]:
 q=COMFY/"models"/item["dir"]/item["file"];
 if not q.exists(): errors.append(f"missing model: {q}")
for item in manifest["loras_default_required"]:
 q=COMFY/"models"/"loras"/item["file"];
 if not q.exists(): errors.append(f"missing default LoRA: {q}")
for item in manifest.get("special_required_models",[]):
 if not exists_any(item["accepted_locations"]): errors.append(f"missing special model: {item['file']}")
llama=COMFY/"custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b8840/linux-x64-cuda/llama-cli"
if not llama.exists(): errors.append(f"Linux llama.cpp missing: {llama}")
info={}
for name,url,auth in [("render","http://127.0.0.1:8188/object_info",False),("prompt","http://127.0.0.1:8189/object_info",False),("panel","http://127.0.0.1:7860/api/config",True)]:
 try: info[name]=get_json(url,auth); print(name,"OK")
 except Exception as e: errors.append(f"{name} service failed: {e}")
critical=["LLMTextProcessor","BunnyH3ConditioningBridge","MinimaxH3LatentUpscaler3D","MergeImageBatchAndAudioList","Power Lora Loader (rgthree)","Seed (rgthree)"]
for svcname in ("render","prompt"):
 if svcname in info:
  for cls in critical:
   if cls not in info[svcname]: errors.append(f"{svcname}: missing node {cls}")
subprocess.run(["nvidia-smi","--query-gpu=name,memory.total,memory.used,driver_version","--format=csv,noheader"],check=False)
if warnings:
 print("WARNINGS:"); [print(" -",x) for x in warnings]
if errors:
 print("ERRORS:"); [print(" -",x) for x in errors]
print("RESULT:","NOT READY" if errors else "READY FOR CONVERSION TEST")
sys.exit(1 if errors else 0)
