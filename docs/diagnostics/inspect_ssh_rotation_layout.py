"""Read-only SSH key installation layout for future key rotation."""
import os,pathlib,subprocess
p=subprocess.run(['sshd','-T'],capture_output=True,text=True,timeout=8)
if p.returncode:
    p=subprocess.run(['/usr/sbin/sshd','-T'],capture_output=True,text=True,timeout=8)
print('SSHD_CONFIG_READABLE',p.returncode==0,flush=True)
for line in p.stdout.splitlines():
    if line.startswith(('authorizedkeysfile ','authorizedkeyscommand ','authorizedkeyscommanduser ','pubkeyauthentication ')):
        print('CONFIG',line[:140],flush=True)
for path in ('/root/.ssh','/root/.ssh/authorized_keys','/etc/ssh/sshd_config.d'):
    q=pathlib.Path(path)
    print('PATH',path,'exists',q.exists(),'file' if q.is_file() else 'dir' if q.is_dir() else 'other',
          'writable',os.access(path,os.W_OK),flush=True)
