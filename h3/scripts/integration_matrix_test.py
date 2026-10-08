#!/usr/bin/env python3
from __future__ import annotations
import asyncio, base64, json, os, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ['H3_PANEL_PASSWORD']='integration-pass'
sys.path.insert(0, str(ROOT))
from app import main as m

def all_nodes(doc):
    for n in doc.get('nodes',[]): yield n
    for sg in doc.get('definitions',{}).get('subgraphs',[]): yield from all_nodes(sg)

master=json.loads((ROOT/'workflows/VAST_H3_MASTER_NATIVE_INT8_96GB.json').read_text())
prompt=json.loads((ROOT/'workflows/VAST_H3_PROMPT_ONLY_STAGE2.json').read_text())
api_map=json.loads((ROOT/'config/VAST_H3_API_MAP.json').read_text())
manifest=json.loads((ROOT/'config/models_manifest.json').read_text())
html=(ROOT/'static/index.html').read_text()

# A. Workflow topology / release branch
assert any(n.get('id')==7331 for n in master['nodes']), 'master SaveVideo missing'
assert not any(n.get('id')==7331 for n in prompt['nodes']), 'prompt-only still contains SaveVideo'
assert not [(n.get('id'),n.get('type')) for n in all_nodes(master) if 'unload' in str(n.get('type','')).lower()]
ml=next(n for n in master['nodes'] if n.get('id')==4595)
assert ml['widgets_values_named']['unet_name']==api_map['nodes']['model_loader']['default']
assert ml['widgets_values'][0]==api_map['nodes']['model_loader']['default']

# B. Model / frontend / API contract
assert api_map['nodes']['model_loader']['alternatives']['native_int8']=='minimax_h3_ref2va_pruned_int8_convrot.safetensors'
assert api_map['nodes']['model_loader']['alternatives']['10eros_hybrid']=='10Eros_Max_h3_hybrid_beta5_int8.safetensors'
assert 'value="native_int8"' in html and 'value="10eros_hybrid"' in html
assert 'refs.length!==5' in html and 'max 1' in html
req={x['file'] for x in manifest['required_primary']}
for f in ['minimax_h3_ref2va_pruned_int8_convrot.safetensors','qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors','minimax_h3_video_vae_int8_convrot.safetensors','minimax_h3_audio_vae_fp32.safetensors','minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors','Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf','Qwen3VL-8B-Instruct-Q4_K_M.gguf','Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.i1-Q6_K.gguf','MN-Violet-Lotus-12B.Q5_K_M.gguf']:
    assert f in req,f

# C. Patching: fixed analysis seed, unique creative seed, real H3 seed sources, no-audio semantics
with tempfile.TemporaryDirectory() as td:
    td=Path(td); old_in=m.COMFY_INPUT_DIR; old_models=m.COMFY_MODELS_DIR
    m.COMFY_INPUT_DIR=td; m.COMFY_MODELS_DIR=td/'models'; (m.COMFY_MODELS_DIR/'diffusion_models').mkdir(parents=True); (m.COMFY_MODELS_DIR/'loras').mkdir(parents=True)
    (m.COMFY_MODELS_DIR/'diffusion_models'/'minimax_h3_ref2va_pruned_int8_convrot.safetensors').write_bytes(b'x')
    pics=[]
    for i in range(6):
        q=td/f'p{i}.png'; q.write_bytes(b'x'); pics.append(q.name)
    (td/'__h3_silence_1s.wav').write_bytes(b'x')
    ctx={'prompt':'scene','model':'native_int8','soft_timeout_minutes':8,'hard_restart_after_seconds':90,'pictures':pics,'audio':[],'loras':[]}
    j1={'id':'im1','render_seed':100,'analysis_seed':200,'prompt_seed':300,'context':ctx}
    j2={'id':'im2','render_seed':101,'analysis_seed':200,'prompt_seed':400,'context':ctx}
    w1=json.loads(m.patch_workflow(j1,m.PROMPT_ONLY).read_text()); w2=json.loads(m.patch_workflow(j2,m.PROMPT_ONLY).read_text())
    n1={n['id']:n for n in all_nodes(w1)}; n2={n['id']:n for n in all_nodes(w2)}
    assert n1[1854]['widgets_values'][0]==100 and n1[6067]['widgets_values'][0]==101
    assert n2[1854]['widgets_values'][0]==101 and n2[6067]['widgets_values'][0]==102
    assert n1[7360]['widgets_values'][0]==n2[7360]['widgets_values'][0]==200
    assert [n1[4022]['widgets_values'][x] for x in (5,9)]==[200,201]
    assert [n2[4022]['widgets_values'][x] for x in (5,9)]==[200,201]
    assert [n1[4022]['widgets_values'][x] for x in (13,17)]==[300,301]
    assert [n2[4022]['widgets_values'][x] for x in (13,17)]==[400,401]
    assert n1[3423]['widgets_values_named']['value_3'] is False
    assert n1[2249]['widgets_values_named']['choice_1']=='LLM does not see audio'
    for p in (ROOT/'state').glob('job_im*_*.json'): p.unlink()
    m.COMFY_INPUT_DIR=old_in; m.COMFY_MODELS_DIR=old_models

# D. Scheduler / shutdown contracts
m.vast_control['plan']='none'
m.queue[:]=[
 {'id':'r6','status':'prompt_queued','review_required':True,'batch_seq':1,'candidate_index':6},
 {'id':'a2','status':'prompt_queued','review_required':False,'batch_seq':2,'candidate_index':1},
]
assert m.pick_next_prompt_job()['id']=='a2'
m.queue[:]=[
 {'id':'auto','status':'render_queued_auto','created_at':1,'batch_seq':1,'candidate_index':1},
 {'id':'review','status':'render_queued_review','created_at':2,'approved_at':3,'batch_seq':2,'candidate_index':6},
]
assert m.pick_next_render_job()['id']=='review'
m.vast_control['plan']='stop_after_current'
assert m.pick_next_prompt_job() is None and m.pick_next_render_job() is None
m.vast_control['plan']='stop_after_queue'
m.queue[:]=[
 {'id':'a','status':'prompt_queued','review_required':False,'batch_seq':1,'candidate_index':1},
 {'id':'r','status':'prompt_queued','review_required':True,'batch_seq':1,'candidate_index':6},
]
assert m.pick_next_prompt_job()['id']=='a'
assert m.skip_unstarted_review_prompts_for_shutdown()==1
assert next(x for x in m.queue if x['id']=='r')['status']=='review_skipped_shutdown'

# E. Auth / persistence fail closed
hdr='Basic '+base64.b64encode(b'h3:integration-pass').decode()
assert m._authorized(hdr) and not m._authorized(None)
old_root,old_mode=m.H3_PERSISTENT_ROOT,m.H3_PERSISTENCE_MODE
m.H3_PERSISTENT_ROOT=''; m.H3_PERSISTENCE_MODE=''
assert m.persistent_storage_status()['safe_for_destroy_keep_data'] is False
m.H3_PERSISTENT_ROOT, m.H3_PERSISTENCE_MODE=old_root,old_mode

# F. Recovery no-id transitions are deterministic and do not mark completion.
async def rec():
    j={'id':'rp','status':'recovery_prompt','review_required':False,'context':{}}
    await m._recover_one_job(j,'prompt'); assert j['status']=='prompt_queued'
    j={'id':'rr','status':'recovery_render','approved_final_prompt':'x','context':{}}
    await m._recover_one_job(j,'render'); assert j['status']=='render_queued_review'
asyncio.run(rec())

# G. Provisioning/service-test contracts exist.
prov=(ROOT/'scripts/provision.sh').read_text(); svc=(ROOT/'scripts/service_ctl.sh').read_text(); smoke=(ROOT/'scripts/smoke_test.py').read_text()
assert 'comfy-cli==1.21.0' in prov and '--version 0.38.0' in prov
assert '--skip-manager' not in prov
assert 'COMFY_PYTHON=$COMFY_PYTHON' in prov
assert '${COMFY_PYTHON:-$PYTHON_BIN}' in svc
assert '"workflow","validate","--workflow"' in smoke
assert '--print-prompt' in smoke
# Test may write queue state via scheduler helpers; release/test must leave state clean.
q=ROOT/'state/queue.json'
if q.exists(): q.unlink()
for x in (ROOT/'state').glob('job_im*_*.json'): x.unlink()
print('INTEGRATION MATRIX PASS')
