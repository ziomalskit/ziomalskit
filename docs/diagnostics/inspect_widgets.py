import json,pathlib,subprocess,os,shlex
env={**os.environ}
for token in shlex.split(pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env').read_text()):
 key,sep,value=token.partition('=')
 if sep:env[key]=value
code=r'''
import sys,json,pathlib,urllib.request
sys.path.insert(0,'/workspace/H3_VAST_MOBILE')
from app.workflow_validation import graph_scopes,_validate_widgets,DECORATIONS
with urllib.request.urlopen('http://127.0.0.1:8189/object_info') as r:catalog=json.load(r)
print('RESOLUTION_SCHEMA',json.dumps(catalog.get('ResolutionSelector')))
seen=set()
for path in pathlib.Path('/workspace/H3_VAST_MOBILE/workflows').glob('*json'):
 if path.name not in ['H3_v20_heretic_MASTER_DURATION_EXACT.json','VAST_H3_PROMPT_ONLY_STAGE2.json']:continue
 for graph in graph_scopes(json.loads(path.read_text())):
  for node in graph.get('nodes',[]):
   if node['type'] not in catalog or node['type'] in DECORATIONS:continue
   try:_validate_widgets(node,catalog)
   except Exception as error:
    text=str(error)
    if text not in seen:print('WIDGET_ERROR',text);seen.add(text)
'''
result=subprocess.run(['/opt/aj/venv/bin/python','-'],input=code.encode(),env=env,capture_output=True,timeout=60)
print(result.stdout.decode());print(result.stderr.decode());raise SystemExit(result.returncode)
