import os,pathlib,subprocess
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
env={**os.environ,'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64','PANEL_ROOT':'/workspace/H3_VAST_MOBILE','COMFY_ROOT':'/workspace/ComfyUI'}
commands=[['/opt/aj/venv/bin/python','-m','pip','install','--disable-pip-version-check','--no-cache-dir','-c','/opt/aj/ziomalskit/h3/runtime-constraints.txt','soundfile==0.13.1'],['bash','/workspace/H3_VAST_MOBILE/scripts/service_ctl.sh','restart','all'],['bash','/workspace/H3_VAST_MOBILE/scripts/smoke_test.sh']]
for index,command in enumerate(commands):
 log=state/('soundfile-fix-'+str(index)+'.log')
 with log.open('wb') as output:result=subprocess.run(command,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=600)
 print(log.read_text(errors='replace')[-20000:],flush=True)
 if result.returncode:raise SystemExit(result.returncode)
print('SERVICE_CONVERSION_READY')
