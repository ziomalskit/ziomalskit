#!/usr/bin/env python3
from __future__ import annotations
import base64, importlib.util, json, os, py_compile, subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ["H3_PANEL_PASSWORD"]="selftest-pass"
spec=importlib.util.spec_from_file_location("h3main",ROOT/"app/main.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def all_nodes(doc):
    for n in doc.get("nodes",[]): yield n
    for sg in doc.get("definitions",{}).get("subgraphs",[]): yield from all_nodes(sg)

# Syntax
for p in [ROOT/"app/main.py",ROOT/"scripts/preflight.py",ROOT/"scripts/smoke_test.py",ROOT/"scripts/patch_llama_binary.py"]:
    py_compile.compile(str(p),doraise=True)
for p in [ROOT/"INSTALL_ON_VAST.sh",ROOT/"scripts/provision.sh",ROOT/"scripts/service_ctl.sh",ROOT/"scripts/setup_linux_llama.sh",ROOT/"scripts/install_fallback_nodes.sh",ROOT/"scripts/preflight.sh",ROOT/"scripts/smoke_test.sh"]:
    subprocess.run(["bash","-n",str(p)],check=True)

master=json.loads((ROOT/"workflows/VAST_H3_MASTER_NATIVE_INT8_96GB.json").read_text())
prompt=json.loads((ROOT/"workflows/VAST_H3_PROMPT_ONLY_STAGE2.json").read_text())

# Recursive link integrity for top graph + every nested subgraph.
def assert_graph_integrity(doc):
    nodes=doc.get("nodes",[]) or []; links=doc.get("links",[]) or []
    ids={n["id"] for n in nodes}
    interface_ids={doc.get("inputNode",{}).get("id"), doc.get("outputNode",{}).get("id"), -10, -20}
    interface_ids.discard(None)
    lids=set()
    for l in links:
        if isinstance(l,dict):
            lid=l["id"]; origin=l["origin_id"]; target=l["target_id"]
        else:
            lid=l[0]; origin=l[1]; target=l[3]
        lids.add(lid)
        assert origin in ids or origin in interface_ids, (doc.get("name"),"bad origin",origin,lid)
        assert target in ids or target in interface_ids, (doc.get("name"),"bad target",target,lid)
    for n in nodes:
        for i in n.get("inputs",[]) or []:
            if i.get("link") is not None: assert i["link"] in lids
        for o in n.get("outputs",[]) or []:
            for lid in o.get("links") or []: assert lid in lids
    for sg in doc.get("definitions",{}).get("subgraphs",[]):
        assert_graph_integrity(sg)
for doc in (master,prompt): assert_graph_integrity(doc)

# No explicit unload
assert not [(n.get("id"),n.get("type")) for n in all_nodes(master) if "unload" in str(n.get("type","")).lower()]

# Release state clean
assert {p.name for p in (ROOT/"state").iterdir()} <= {".gitkeep"}

# Auth
tok=base64.b64encode(b"h3:selftest-pass").decode()
assert m._authorized("Basic "+tok)
assert not m._authorized("Basic "+base64.b64encode(b"h3:bad").decode())

# Build technical input dir and patch contracts
with tempfile.TemporaryDirectory() as td:
    old=m.COMFY_INPUT_DIR
    m.COMFY_INPUT_DIR=Path(td)
    imgs=[]
    for i in range(6):
        q=Path(td)/f"i{i}.png"; q.write_bytes(b"x"); imgs.append(q.name)
    silence=Path(td)/"__h3_silence_1s.wav"; silence.write_bytes(b"x")

    job={"id":"selftest","render_seed":123,"analysis_seed":456,"prompt_seed":789,
         "context":{"prompt":"x","model":"native_int8","soft_timeout_minutes":8,
                    "hard_restart_after_seconds":90,"pictures":imgs,"audio":[],"loras":[]}}
    pp=m.patch_workflow(job,m.PROMPT_ONLY)
    pw=json.loads(pp.read_text())
    nodes={n["id"]:n for n in all_nodes(pw)}
    assert nodes[1854]["widgets_values"][0]==123
    assert nodes[6067]["widgets_values"][0]==124
    assert nodes[7360]["widgets_values"][0]==456
    c=nodes[4022]
    assert [c["widgets_values"][x] for x in (5,9,13,17)]==[456,457,789,790]
    assert nodes[2441]["widgets_values_named"]["seed"]==456
    assert nodes[2447]["widgets_values_named"]["seed"]==457
    assert nodes[2445]["widgets_values_named"]["seed"]==789
    assert nodes[4275]["widgets_values_named"]["seed"]==790
    assert nodes[3423]["widgets_values_named"]["value_3"] is False
    assert nodes[2249]["widgets_values_named"]["choice_1"]=="LLM does not see audio"

    a=Path(td)/"a.wav"; a.write_bytes(b"x")
    job["id"]="selftest_audio"; job["context"]["audio"]=[a.name]
    ap=m.patch_workflow(job,m.PROMPT_ONLY)
    aw=json.loads(ap.read_text()); an={n["id"]:n for n in all_nodes(aw)}
    assert an[3423]["widgets_values_named"]["value_3"] is True
    assert an[2249]["widgets_values_named"]["choice_1"]=="LLM sees Audio 0"
    m.COMFY_INPUT_DIR=old

# patch_workflow intentionally writes technical job JSONs into STATE; selftest must clean them.
for p in (ROOT/"state").glob("job_selftest*_*.json"):
    p.unlink()

# Scheduler
m.vast_control["plan"]="none"
m.queue[:]=[
 {"id":"review","status":"prompt_queued","review_required":True,"batch_seq":1,"candidate_index":6},
 {"id":"auto","status":"prompt_queued","review_required":False,"batch_seq":2,"candidate_index":1},
]
assert m.pick_next_prompt_job()["id"]=="auto"
m.queue[:]=[
 {"id":"auto","status":"render_queued_auto","created_at":1,"batch_seq":1,"candidate_index":1},
 {"id":"review","status":"render_queued_review","created_at":2,"approved_at":3,"batch_seq":2,"candidate_index":6},
]
assert m.pick_next_render_job()["id"]=="review"
m.vast_control["plan"]="stop_after_current"
assert m.pick_next_prompt_job() is None
assert m.pick_next_render_job() is None

# Frontend/backend release contract: exactly five supporting refs + optional single audio.
html=(ROOT/"static/index.html").read_text(encoding="utf-8")
assert "refs.length!==5" in html
assert "Audio reference — optional, max 1" in html

# Manifest must cover the active native model stack and all enabled default LoRAs.
manifest=json.loads((ROOT/"config/models_manifest.json").read_text())
required={x["file"] for x in manifest["required_primary"]}
for f in [
 "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
 "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
 "minimax_h3_video_vae_int8_convrot.safetensors",
 "minimax_h3_audio_vae_fp32.safetensors",
 "minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors",
 "Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf",
 "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
 "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.i1-Q6_K.gguf",
 "MN-Violet-Lotus-12B.Q5_K_M.gguf",
]: assert f in required, f
required_loras={x["file"] for x in manifest["loras_default_required"]}
assert required_loras=={"HMBreastsV2.safetensors","MysticXXX_MMH3-V4-ref2va.safetensors","movement_h3_lora_v1_500.safetensors"}

print("OFFLINE SELFTEST PASS")
