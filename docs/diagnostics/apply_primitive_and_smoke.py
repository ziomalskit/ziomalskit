"""Safely preserve exact PrimitiveInt frontend-only inputs; smoke with zero inference."""
import ast,os,pathlib,subprocess,urllib.request
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/846c90740983b3e1136521d50bd58e3ec38be2d0/h3/app/adapters/v20.py'
with urllib.request.urlopen(url,timeout=25) as response:source=response.read().decode()
start=source.index('            # PrimitiveInt\'s "fixed" is a frontend-only control-after-generate')
end=source.index('                a.fail(f"visual/audio output chain {key} changed")',start)
patch=source[start:end]
old='            if not frontend_combo and actual.get("inputs") != expected["inputs"]:\n'
assert 'primitive_fixed' in patch and 'if not (frontend_combo or primitive_fixed)' in patch
for label,path in [('source',pathlib.Path('/opt/aj/ziomalskit/h3/app/adapters/v20.py')),
                   ('deployed',pathlib.Path('/workspace/H3_VAST_MOBILE/app/adapters/v20.py'))]:
 content=path.read_text()
 if patch in content:print('ALREADY_PATCHED',label,flush=True);continue
 assert content.count(old)==1,'Unexpected v20 state '+label
 updated=content.replace(old,patch);ast.parse(updated)
 backup=state/('v20-before-primitive-'+label+'.py');backup.write_text(content)
 temp=path.with_name(path.name+'.aj-temp');temp.write_text(updated);os.chmod(temp,0o644);temp.replace(path)
 print('PATCHED',label,flush=True)
env={**os.environ,'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
     'PANEL_ROOT':'/workspace/H3_VAST_MOBILE','COMFY_ROOT':'/workspace/ComfyUI',
     'CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'}
with (state/'primitive-full-smoke.log').open('wb') as output:
 result=subprocess.run(['bash','/workspace/H3_VAST_MOBILE/scripts/smoke_test.sh'],env=env,stdout=output,stderr=subprocess.STDOUT,timeout=360)
log=(state/'primitive-full-smoke.log').read_text(errors='replace')
print('SMOKE_EXIT',result.returncode,flush=True)
for line in log.splitlines()[-50:]:
 if any(word in line for word in ('validated','PASSED','FAILED','Error','Exception','Traceback','API nodes','workflow has','comfy-cli exited','profile','render','prompt')):
  print('SMOKE_LOG',line[:650],flush=True)
raise SystemExit(result.returncode)
