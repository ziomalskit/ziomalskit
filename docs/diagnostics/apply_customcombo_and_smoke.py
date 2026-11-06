"""Apply one narrow CustomCombo preview catalog patch and test with zero submissions."""
import os,pathlib,subprocess,urllib.request
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
source_url='https://raw.githubusercontent.com/ziomalskit/ziomalskit/aaa1af00dd1d49a6a743aca7603170c64982686e/h3/app/main.py'
with urllib.request.urlopen(source_url,timeout=25) as response:source=response.read().decode()
start=source.index('    # CustomCombo is a ComfyUI frontend-defined dropdown')
end=source.index('    validation = await run_cli_envelope(',start)
patch=source[start:end]
assert '    _atomic_json_write(catalog_path, validation_catalog)\n' in patch
original='    _atomic_json_write(catalog_path, catalog)\n'
for label,path in [('source',pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py')),
                   ('deployed',pathlib.Path('/workspace/H3_VAST_MOBILE/app/main.py'))]:
 content=path.read_text()
 if patch in content:print('ALREADY_PATCHED',label,flush=True);continue
 assert content.count(original)==1,'Unexpected source in '+label
 backup=state/('main-before-customcombo-'+label+'.py');backup.write_text(content)
 next_content=content.replace(original,patch)
 tmp=path.with_name(path.name+'.aj-temp');tmp.write_text(next_content)
 os.chmod(tmp,0o644);tmp.replace(path)
 print('PATCHED',label,flush=True)
env={**os.environ,'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
     'PANEL_ROOT':'/workspace/H3_VAST_MOBILE','COMFY_ROOT':'/workspace/ComfyUI',
     'CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'}
with (state/'customcombo-full-smoke.log').open('wb') as output:
 result=subprocess.run(['bash','/workspace/H3_VAST_MOBILE/scripts/smoke_test.sh'],
                       env=env,stdout=output,stderr=subprocess.STDOUT,timeout=300)
log=(state/'customcombo-full-smoke.log').read_text(errors='replace')
print('SMOKE_EXIT',result.returncode,flush=True)
for line in log.splitlines()[-40:]:
 if any(word in line for word in ('validated','PASSED','FAILED','Error','Exception','Traceback','API nodes','workflow has','comfy-cli exited')):
  print('SMOKE_LOG',line[:600],flush=True)
raise SystemExit(result.returncode)
