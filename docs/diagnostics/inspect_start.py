import pathlib,re,shlex,json
state=pathlib.Path('/workspace/.aj-diagnostics')
secrets=[]
p=pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env')
for token in shlex.split(p.read_text()):
 k,_,v=token.partition('=')
 if any(x in k for x in ['PASSWORD','TOKEN','SECRET','KEY']) and v:secrets.append(v)
def safe(t):
 for v in secrets:t=t.replace(v,'[REDACTED]')
 return t
print(safe((state/'final-start.log').read_text(errors='replace')[-25000:]))
for p in pathlib.Path('/workspace/H3_VAST_MOBILE').glob('logs/*'):
 if p.is_file():print('LOG_FILE',p.name,p.stat().st_size)
