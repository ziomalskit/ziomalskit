"""Check deterministic repair of inline [Shot N] references on already-completed output."""
import importlib.util,json,pathlib,re,urllib.request
p=pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/temporal.py')
spec=importlib.util.spec_from_file_location('aj_temporal_probe',p)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
with urllib.request.urlopen('http://127.0.0.1:8189/history?max_items=3',timeout=15) as r:history=json.load(r)
def one(x):return x[0] if isinstance(x,list) and len(x)==1 else x
for obj in history.values():
    for typ,e in obj.get('status',{}).get('messages',[]):
        if typ!='execution_error' or e.get('node_id')!='aj_final_timeline':continue
        d=e.get('current_inputs',{})
        final,plan,duration=map(lambda x:one(d[x]),('final_h3_prompt','canonical_creative_plan','effective_duration_seconds'))
        assert isinstance(final,str) and isinstance(plan,str) and isinstance(duration,(int,float))
        good=set(int(z) for z in re.findall(r'(?m)^\[Shot ([1-9][0-9]*)\]',plan))
        visible=mod.directive_text(final)
        positions={m.start() for m in re.finditer(r'(?m)^\[Shot [1-9][0-9]*\]',visible)}
        replacements=[]
        for m in re.finditer(r'\[shot(?=\s|\d|\]|$)',visible,re.I):
            if m.start() in positions:continue
            exact=re.match(r'\[Shot ([1-9][0-9]*)\]',visible[m.start():])
            if not exact or int(exact.group(1)) not in good:
                print('UNSAFE_TO_NORMALIZE',flush=True);raise SystemExit(0)
            replacements.append((m.start(),m.start()+len(exact.group(0)), 'Shot '+exact.group(1)))
        fixed=final
        for a,b,s in reversed(replacements):fixed=fixed[:a]+s+fixed[b:]
        try:
            result=mod.final_timeline(fixed,plan,duration,canonicalize=True)
            print('SAFE_NORMALIZATION_PASS','replaced',len(replacements),'schedule_shots',len(good),
                  'final_chars',len(result),flush=True)
        except ValueError as ex:
            print('NEXT_CONTRACT_FAILURE',str(ex)[:150],flush=True)
        raise SystemExit(0)
print('NO_TARGET',flush=True)
