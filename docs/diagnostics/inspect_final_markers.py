"""Read only structural shot-marker statistics from failed final prompt."""
import json,re,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8189/history?max_items=3',timeout=12) as r: histories=json.load(r)
for obj in histories.values():
    for typ,entry in obj.get('status',{}).get('messages',[]):
        if typ!='execution_error' or entry.get('node_id')!='aj_final_timeline':continue
        inputs=entry.get('current_inputs',{})
        for name in ('final_h3_prompt','canonical_creative_plan'):
            text=inputs.get(name)
            if isinstance(text,list):text=text[0] if len(text)==1 else None
            if not isinstance(text,str):continue
            candidates=list(re.finditer(r'\[shot(?=\s|\d|\]|$)',text,re.I))
            valid=list(re.finditer(r'(?m)^\[Shot ([1-9][0-9]*)\]',text))
            other=list(re.finditer(r'(?m)^\s*Shot\s+[0-9]+',text,re.I))
            details=[]
            for m in candidates[:24]:
                p=m.start();last=text.rfind('\n',0,p);prefix=text[last+1:p]
                right=text[p:p+30]
                num=re.search(r'(?i)shot\s*(\d+)',right)
                details.append({'num':int(num.group(1)) if num else None,'at_line_start':p==last+1,
                  'indent':len(prefix) if prefix.isspace() else None,
                  'exact_header':bool(re.match(r'\[Shot [1-9][0-9]*\]',right)),
                  'contains_line_prefix':bool(prefix.strip()),'close_bracket':']' in right,
                  'is_uppercase_label':right.startswith('[SHOT')})
            print('MARKERS',name,'text_len',len(text),'candidates',len(candidates),'valid',len(valid),
                  'valid_numbers',[int(v.group(1)) for v in valid[:20]],'other_headings',len(other),
                  'details',json.dumps(details),flush=True)
        raise SystemExit(0)
print('NO_TARGET',flush=True)
