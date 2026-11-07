"""Inspect only code structure near cached-analysis selector guards; no secrets."""
import pathlib,hashlib,difflib,urllib.request
paths=[pathlib.Path('/opt/aj/ziomalskit/h3/app/main.py'),pathlib.Path('/workspace/H3_VAST_MOBILE/app/main.py')]
for i,p in enumerate(paths):
    s=p.read_text()
    lines=s.splitlines()
    print('FILE',i,'SHA256',hashlib.sha256(s.encode()).hexdigest(),'LINES',len(lines),
          'SELECTOR_GUARD',s.count('Pinned production step selector changed; refusing implicit Disabled stage'),
          'VISION_GUARD',s.count('Pinned vision analysis stage changed; context override is unsafe'),
          'CACHE_FIX_PRESENT',s.count('fully_cached_analysis = set(analysis_texts or {})'),
          'OVERLAP_PATCH',s.count('Qwen3.5-27B has 64 transformer blocks'),flush=True)
    for needle in ('stage_choices = {','for node_id, expected_choice in stage_choices.items():',
                   'for node_id in ("2551:2640:2441", "2551:2640:2447"):'):
        at=s.find(needle)
        if at<0:continue
        lineno=s.count('\n',0,at)
        print('ANCHOR',i,needle,'LINE',lineno+1,flush=True)
        for line in lines[max(0,lineno-2):lineno+9]:
            print('CODE',i,line[:165],flush=True)
assert all(p.is_file() for p in paths)
