"""Inspect last AJFinalPromptTimeGuard failure without printing generated text."""
import json,re,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8189/history?max_items=4',timeout=12) as r: history=json.load(r)
allowed={'compiler final answer is empty','ambiguous native reasoning boundary in compiler final answer','compiler final answer violates the v20 six-section contract','compiler final answer has an empty v20 section','final prompt shot identities differ from creative plan','final prompt has malformed or misplaced shot headers','Shot 1 must not have a timestamp','final cut requires a canonical timestamp','final cut differs from canonical creative timeline','extra or misplaced temporal cut directive','inline, malformed or misplaced structural shot directive','creative timeline is not contiguous or does not end at effective duration','creative plan shot identities are not sequential','each creative shot requires one canonical Timeline'}
for obj in history.values():
    for item in obj.get('status',{}).get('messages',[]):
        if item[0]!='execution_error' or item[1].get('node_id')!='aj_final_timeline':continue
        e=item[1]
        raw=str(e.get('exception_message') or '').splitlines()[0].strip()
        print('GUARD_ERROR',raw if raw in allowed else 'OTHER_UNRECOGNIZED',flush=True)
        print('EXCEPTION_CLASS',e.get('exception_type'),flush=True)
        inputs=e.get('current_inputs',{})
        print('INPUT_KEYS',list(inputs.keys()) if isinstance(inputs,dict) else type(inputs).__name__,flush=True)
        if isinstance(inputs,dict):
            for key in ('final_h3_prompt','canonical_creative_plan'):
                v=inputs.get(key)
                if isinstance(v,list) and len(v)==1:v=v[0]
                if isinstance(v,str):
                    print('INPUT_STRUCTURE',key,'length',len(v),'shot_headers',len(re.findall(r'(?m)^\\[Shot [0-9]+\\]',v)), 'timeline_headers',len(re.findall(r'(?m)^Timeline:',v)),'temporal_cuts',len(re.findall(r'\\bAt [0-9]+:[0-9]+\\.[0-9]+,',v)),flush=True)
        raise SystemExit(0)
print('NO_LATEST_TIMELINE_ERROR',flush=True)
