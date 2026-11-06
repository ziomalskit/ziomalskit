"""Non-inference validation that restores the selected CustomCombo index."""
import copy,json,os,pathlib,subprocess
base=pathlib.Path('/workspace/H3_VAST_MOBILE/state')
api_files=sorted(base.glob('job_smoke-*_prompt.api.json'),key=lambda p:p.stat().st_mtime,reverse=True)
assert api_files,'smoke preview not available'
api=api_files[0]
catalog=api.with_name(api.name.replace('.api.json','.catalog.json'))
assert catalog.is_file(),'smoke catalog missing'
graph=json.loads(api.read_text())
nodes={'2551:5422:5067':'MMH3 Ref2VA Step 1 Expand User Prompt',
       '2551:5422:5068':'MMH3 Ref2VA Step 2 Ref Analysis',
       '2551:5422:5069':'MMH3 Ref2VA Step 3 Creative',
       '2551:5422:5070':'MMH3 Ref2VA Step 4 Compiler'}
for key,choice in nodes.items():
 n=graph[key]
 assert n.get('class_type')=='CustomCombo' and n['inputs'].get('choice')==choice
 assert 'index' not in n['inputs']
 n['inputs']['index']=1
tmp=pathlib.Path('/workspace/.aj-diagnostics/active-combo-index-smoke.api.json')
tmp.write_text(json.dumps(graph))
env={**os.environ,'COMFY_LOCAL_URL':'http://127.0.0.1:8189','COMFY_WHERE':'local',
     'PATH':'/opt/aj/venv/bin:/usr/local/bin:/usr/bin:/bin'}
cmd=['/opt/aj/venv/bin/python','-m','comfy_cli','--workspace','/workspace/ComfyUI','--json',
     'workflow','validate','--workflow',str(tmp),'--input',str(catalog)]
result=subprocess.run(cmd,capture_output=True,env=env,timeout=90)
last=(result.stdout.decode(errors='replace').splitlines() or [''])[-1]
try:
 e=json.loads(last)
 print('COMFY_CLI_OK',e.get('ok'),'EXIT',result.returncode,
       'ERROR_CODE',e.get('error',{}).get('code') if isinstance(e.get('error'),dict) else None,
       'ERROR_MESSAGE',str(e.get('error',{}).get('message',''))[:400] if isinstance(e.get('error'),dict) else '',
       'VALID',e.get('data',{}).get('valid'))
except Exception as err:
 print('NO_ENVELOPE','EXIT',result.returncode,'OUTPUT_BYTES',len(result.stdout))
print('FOUR_STAGES_INDEX_1_ADDED',len(nodes))
raise SystemExit(result.returncode)
