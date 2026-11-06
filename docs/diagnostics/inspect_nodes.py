import pathlib,json,urllib.request,shlex
secrets=[]
for t in shlex.split(pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env').read_text()):
 k,_,v=t.partition('=')
 if any(x in k for x in ['PASSWORD','TOKEN','SECRET','KEY']) and v:secrets.append(v)
def safe(t):
 for v in secrets:t=t.replace(v,'[REDACTED]')
 return t
print('RENDER_LOG',safe(pathlib.Path('/workspace/render.log').read_text(errors='replace')[-28000:]))
with urllib.request.urlopen('http://127.0.0.1:8188/object_info') as r:catalog=json.load(r)
for path in pathlib.Path('/workspace/H3_VAST_MOBILE/workflows').glob('*.json'):
 if path.name in ['H3_FULL_RENDER.json','10EROS_FULL_RENDER.json','VAST_H3_PROMPT_ONLY_STAGE2.json']:
  d=json.loads(path.read_text());print('MISSING_CLASSES',path.name,sorted({n['type'] for n in d.get('nodes',[]) if n['type'] not in catalog}))
