"""Minimal synthetic llama-server receipt probe; never uses or prints user data."""
import json,os,pathlib,socket,subprocess,time,urllib.request,urllib.error
root=pathlib.Path('/workspace/ComfyUI')
binary=root/'custom_nodes/ComfyUI-LLM-text-processor/vendor/llama.cpp/b10472/linux-x64-cuda/llama-server'
model=root/'models/LLM/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-Q6_K.gguf'
assert binary.is_file() and model.is_file(),'PINNED_FILE_MISSING'
q=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
                 capture_output=True,text=True,timeout=15)
assert q.returncode==0,'GPU_READ_FAILED'
free_mb=int(q.stdout.splitlines()[0].strip())
print('GPU_FREE_MIB',free_mb,flush=True)
assert free_mb>37000,'BLOCKED_INSUFFICIENT_GPU_HEADROOM'
with socket.socket() as s:
    s.bind(('127.0.0.1',0));port=s.getsockname()[1]
env=os.environ.copy()
env.update({'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','CUDA_HOME':'/usr/local/cuda',
            'LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'})
cmd=[str(binary),'-m',str(model),'-c','8192','-ngl','999',
     '--host','127.0.0.1','--port',str(port),'--parallel','1','--no-context-shift']
url='http://127.0.0.1:'+str(port)
process=subprocess.Popen(cmd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    deadline=time.monotonic()+210
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        if process.poll() is not None:raise RuntimeError('MODEL_SERVER_EXITED')
        if time.monotonic()>=deadline:raise RuntimeError('MODEL_LOAD_TIMEOUT')
        try:
            with opener.open(url+'/health',timeout=2) as response:
                if response.status==200:break
        except Exception:pass
        time.sleep(1)
    print('MODEL_SERVER_HEALTHY',flush=True)
    body=json.dumps({'messages':[{'role':'user','content':'Reply only: OK'}],
                     'stream':False,'verbose':True,'max_tokens':64,'temperature':0,
                     'top_p':1,'top_k':40,'repeat_penalty':1}).encode()
    request=urllib.request.Request(url+'/v1/chat/completions',data=body,
                                   headers={'Content-Type':'application/json'})
    try:
        with opener.open(request,timeout=120) as response:
            result=json.load(response)
    except urllib.error.HTTPError as err:
        print('HTTP_STATUS',err.code,flush=True)
        raise
    info=result.get('__verbose')
    choice=(result.get('choices') or [{}])[0]
    msg=choice.get('message') or {}
    print('RECEIPT',json.dumps({'verbose_present':isinstance(info,dict),
         'verbose_keys':sorted(info) if isinstance(info,dict) else None,
         'stop':info.get('stop') if isinstance(info,dict) else None,
         'stop_type':info.get('stop_type') if isinstance(info,dict) else None,
         'truncated':info.get('truncated') if isinstance(info,dict) else None,
         'finish_reason':choice.get('finish_reason'),
         'response_char_length':len(str(msg.get('content') or '')),
         'completion_tokens':(result.get('usage') or {}).get('completion_tokens')},sort_keys=True),flush=True)
finally:
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=15)
    print('PROBE_SERVER_STOPPED',flush=True)
