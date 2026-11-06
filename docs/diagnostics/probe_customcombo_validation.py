"""Non-submitting proof: validate synthetic API graph with CustomCombo's frontend-defined options."""
import copy,json,os,pathlib,subprocess
root=pathlib.Path('/workspace/H3_VAST_MOBILE/state')
files=sorted(root.glob('job_smoke-*_prompt.api.json'),key=lambda p:p.stat().st_mtime,reverse=True)
assert files,'Synthetic smoke graph missing'
api=files[0];cat=api.with_name(api.name.replace('.api.json','.catalog.json'))
assert cat.is_file()
prompt=json.loads(api.read_text())
catalog=json.loads(cat.read_text())
combo=catalog['CustomCombo']['input']['required']['choice']
assert combo[0]=='COMBO' and isinstance(combo[1],dict) and combo[1].get('options')==[], 'Unexpected CustomCombo schema'
values={n['inputs']['choice'] for n in prompt.values() if n.get('class_type')=='CustomCombo' and 'choice' in n.get('inputs',{})}
assert values and len(values)<=20 and all(isinstance(v,str) and len(v)<200 for v in values)
catalog=copy.deepcopy(catalog);catalog['CustomCombo']['input']['required']['choice'][1]['options']=sorted(values)
temp=pathlib.Path('/workspace/.aj-diagnostics/customcombo-smoke-catalog.json')
temp.write_text(json.dumps(catalog,ensure_ascii=False))
env={**os.environ,'COMFY_LOCAL_URL':'http://127.0.0.1:8189','COMFY_WHERE':'local','PATH':'/opt/aj/venv/bin:/usr/local/bin:/usr/bin:/bin'}
cmd=['/opt/aj/venv/bin/python','-m','comfy_cli','--workspace','/workspace/ComfyUI','--json','workflow','validate','--workflow',str(api),'--input',str(temp)]
result=subprocess.run(cmd,capture_output=True,env=env,timeout=120)
print('DYNAMIC_CHOICES',len(values),'EXIT_CODE',result.returncode)
lines=result.stdout.decode(errors='replace').splitlines()
try:
 d=json.loads(lines[-1]);print('ENVELOPE_OK',d.get('ok'),'ERROR',str(d.get('error',{}).get('message',''))[:600],'VALID',d.get('data',{}).get('valid'),'ERROR_COUNT',d.get('data',{}).get('error_count'))
except (json.JSONDecodeError,IndexError,AttributeError):
 print('NO_ENVELOPE','STDERR_LEN',len(result.stderr),'STDOUT_LEN',len(result.stdout))
raise SystemExit(result.returncode)
