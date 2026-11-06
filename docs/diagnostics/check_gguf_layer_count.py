"""Read GGUF header only: determine exact architecture layer counts without loading weights."""
import pathlib,struct
root=pathlib.Path('/workspace/ComfyUI/models/LLM')
files=['Qwen3.5-27B-heretic.Q5_K_M.gguf','Qwen3-14B-heretic.Q4_K_M.gguf']
def u(f,n):return int.from_bytes(f.read(n),'little')
def string(f):return f.read(u(f,8)).decode('utf-8','replace')
def value(f,typ):
    sizes={0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}
    if typ in sizes:
        n=sizes[typ];return int.from_bytes(f.read(n),'little')
    if typ==8:return string(f)
    if typ==9:
        subtype=u(f,4);length=u(f,8)
        if subtype in sizes:f.seek(length*sizes[subtype],1);return '<array>'
        for _ in range(length):value(f,subtype)
        return '<array>'
    raise ValueError('unknown GGUF type '+str(typ))
for name in files:
    p=root/name
    if not p.is_file():
        print('MODEL_FILE_NOT_FOUND',name,flush=True);continue
    with p.open('rb') as f:
        assert f.read(4)==b'GGUF','INVALID_GGUF'
        version=u(f,4);n_tensors=u(f,8);n_kv=u(f,8)
        print('MODEL_HEADER',name,'version',version,'tensor_count',n_tensors,'kv_count',n_kv,'bytes',p.stat().st_size,flush=True)
        for i in range(n_kv):
            k=string(f);typ=u(f,4);v=value(f,typ)
            if k.endswith('block_count') or k=='general.architecture':
                print('METADATA',name,k,v,flush=True)
            if k.endswith('block_count'):break
