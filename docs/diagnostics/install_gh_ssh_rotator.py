"""Install a restricted self-rotating GitHub Actions key on the existing Vast instance.

No edits to sshd configuration; preserves Vast's existing authorized keys.
The forced command runs only SHA-pinned scripts from this user's repo control.
"""
import hashlib,os,pathlib,stat,subprocess,sys,time,urllib.request
assert os.geteuid()==0,"Root-owned SSH key policy required"
directory=pathlib.Path("/workspace/.aj-diagnostics")
assert directory.is_dir() and directory.stat().st_uid==0
assert not(stat.S_IMODE(directory.stat().st_mode)&0o022),"Unsafe diagnostic directory permissions"
keys=pathlib.Path("/root/.ssh/authorized_keys")
assert keys.is_file() and not keys.is_symlink(),"Unexpected authorized_keys"
assert keys.stat().st_uid==0 and not(stat.S_IMODE(keys.stat().st_mode)&0o022)
initial=keys.read_text()
root="https://raw.githubusercontent.com/ziomalskit/ziomalskit/9043376693d3f2e54a774bb596648718e10c900e/docs/diagnostics/"
items=[
  ("aj_gh_ssh_guard.py","3b532a9c8e7cd34472d45ebebd5ff1b7a2b8dcf3266eda13a3c7c0f07e7721c5"),
  ("aj_gh_key_rotator.py","c7abc89d134080a8d375b81ea5700dfee909fe770386bd343f6e0793e56283ff"),
]
for name,expected in items:
    target=directory/name
    if target.is_symlink():raise RuntimeError("Refuse symlink target")
    with urllib.request.urlopen(root+name,timeout=25) as r:source=r.read()
    assert hashlib.sha256(source).hexdigest()==expected,"Pinned SSH helper mismatch"
    compile(source,name,"exec")
    temporary=directory/(name+".tmp")
    try:
        temporary.write_bytes(source)
        os.chmod(temporary,0o700)
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    assert target.stat().st_uid==0
pidfile=directory/"aj_gh_key_rotator.pid"
if pidfile.exists():
    try:
        oldpid=int(pidfile.read_text().strip())
        if b'aj_gh_key_rotator.py' in pathlib.Path('/proc/'+str(oldpid)+'/cmdline').read_bytes():
            print("ROTATOR_ALREADY_RUNNING",oldpid,flush=True)
            raise SystemExit(0)
    except (ValueError,FileNotFoundError,PermissionError):
        pass
backup=directory/"authorized_keys.before_gh_rotator.bak"
if not backup.exists():
    backup.write_text(initial)
    os.chmod(backup,0o600)
logfile=directory/"aj_gh_key_rotator.log"
with logfile.open("ab",buffering=0) as log:
    proc=subprocess.Popen([sys.executable,"-u",str(directory/"aj_gh_key_rotator.py")],
          stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
          env={"PATH":os.environ.get("PATH","/usr/local/bin:/usr/bin:/bin"),
               "HOME":"/root","LANG":"C.UTF-8"},
          start_new_session=True,close_fds=True)
pidfile.write_text(str(proc.pid)+"\n")
os.chmod(pidfile,0o600)
for _ in range(6):
    time.sleep(1)
    if proc.poll() is not None:break
    if "aj-gh-rotating-key-59453490" in keys.read_text():break
assert proc.poll() is None,"SSH key updater process exited"
now=keys.read_text()
print("ROTATOR_ALIVE",True,flush=True)
print("MANAGED_RESTRICTED_KEY_INSTALLED","aj-gh-rotating-key-59453490" in now,flush=True)
assert all(line in now.splitlines() for line in initial.splitlines()),"Existing Vast keys were modified"
print("ORIGINAL_USER_KEYS_PRESERVED",True,flush=True)
print("NO_SSHD_RESTART",True,flush=True)
