"""Read-only inspect service supervisor availability and safe config directory."""
import pathlib,subprocess,os
p=subprocess.run(['supervisorctl','status'],capture_output=True,text=True,timeout=10)
print('SUPERVISOR_STATUS',p.returncode,flush=True)
for line in p.stdout.splitlines():
  parts=line.split()
  print('SUPERVISOR_PROGRAM',parts[0] if parts else 'unknown',parts[1] if len(parts)>1 else 'unknown',flush=True)
for path in ('/etc/supervisor/conf.d','/etc/supervisord.d','/etc/supervisor/supervisord.conf'):
  p=pathlib.Path(path)
  print('CONF_PATH',path,'exists',p.exists(),'writable',os.access(path,os.W_OK),flush=True)
