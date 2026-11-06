import json,os,pathlib,subprocess,time
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics')
prompt=state/'native-probe-prompt.txt'
prompt.write_text('Write one short English sentence describing a small green toy robot waving in a bright studio. No introduction.\n')
binary=pathlib.Path('/workspace/ComfyUI/custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b10472/linux-x64-cuda/llama-cli')
model=pathlib.Path('/workspace/ComfyUI/models/LLM/Qwen3.5-27B-heretic.Q5_K_M.gguf')
assert binary.is_file() and model.is_file(),'Pinned 27B model or native runtime missing'
env={'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/bin:/bin','CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'}
command=[str(binary),'-m',str(model),'-f',str(prompt),'-n','128','-c','4096','-ngl','999','--seed','42','--single-turn','--reasoning','off','--reasoning-format','deepseek','--reasoning-budget','0']
started=time.monotonic()
with (state/'native-probe.stdout').open('wb') as out,(state/'native-probe.stderr').open('wb') as err:
 result=subprocess.run(command,env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,timeout=300)
text=(state/'native-probe.stdout').read_text(errors='replace')
errors=(state/'native-probe.stderr').read_text(errors='replace')
report={'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-started,2),'nonempty_response':bool(text.strip()),'response_tail':text[-1200:]}
print(json.dumps(report,indent=2))
if result.returncode or not text.strip():
 print('NATIVE_FAILURE',errors[-5000:]);raise SystemExit(1)
(state/'native-27b-pass.json').write_text(json.dumps(report,indent=2))
print('NATIVE_27B_INFERENCE_PASS')
