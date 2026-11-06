"""Read-only inspect dynamic CustomCombo backend implementation and API schema."""
import json,os,pathlib,urllib.request
try:
 with urllib.request.urlopen('http://127.0.0.1:8189/object_info/CustomCombo',timeout=10) as response:
  d=json.load(response)
 print('CUSTOMCOMBO_SCHEMA',json.dumps(d,ensure_ascii=True)[:2500])
except Exception as e:print('OBJECT_INFO_ERROR',type(e).__name__)
root=pathlib.Path('/workspace/ComfyUI/custom_nodes')
found=[]
for base,dirs,files in os.walk(root):
 dirs[:]=[x for x in dirs if x not in ('.git','__pycache__','venv','.venv','node_modules')]
 for name in files:
  if not name.endswith('.py'):continue
  p=pathlib.Path(base)/name
  try:
   if p.stat().st_size>1000000:continue
   s=p.read_text(errors='replace')
  except Exception:continue
  for needle in ('class CustomCombo','"CustomCombo"',"\'CustomCombo\'"):
   i=s.find(needle)
   if i>=0:
    found.append((str(p.relative_to(root)),needle,s[max(0,i-150):min(len(s),i+1700)]))
    break
 if len(found)>10:break
print('MATCHED_FILES',len(found))
for p,kind,s in found[:10]:print('FILE',p,'MATCH',kind,'SOURCE_EXCERPT',s[:1800])
