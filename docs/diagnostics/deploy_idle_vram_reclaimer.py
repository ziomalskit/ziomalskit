"""Install an independent bounded idle-VRAM reclaimer without touching AJ services."""
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

root=pathlib.Path("/workspace/.aj-diagnostics")
root.mkdir(mode=0o700,exist_ok=True)
target=root/"aj_idle_vram_reclaimer.py"
pidfile=root/"aj_idle_vram_reclaimer.pid"
logpath=root/"aj_idle_vram_reclaimer.log"

if pidfile.exists():
    try:
        pid=int(pidfile.read_text().strip())
        cmd=(pathlib.Path("/proc")/str(pid)/"cmdline").read_bytes()
        if target.name.encode() in cmd:
            print("ALREADY_RUNNING_PID",pid,flush=True)
            raise SystemExit(0)
    except (ValueError,FileNotFoundError,PermissionError):
        pass

url=("https://raw.githubusercontent.com/ziomalskit/ziomalskit/"
     "08b9115b831502191bcf655a9552a0c6a080628a/"
     "h3/scripts/aj_idle_vram_reclaimer.py")
with urllib.request.urlopen(url,timeout=30) as r: source=r.read()
assert b"MIN_FREE_MIB = 57344" in source
assert b"MAX_CYCLES = 720" in source
assert b"fcntl.flock(infer_fd" in source

temp=target.with_suffix(".tmp")
try:
    temp.write_bytes(source)
    os.chmod(temp,0o700)
    temp.replace(target)
finally:temp.unlink(missing_ok=True)

with logpath.open("ab",buffering=0) as log:
    proc=subprocess.Popen(
        [sys.executable,"-u",str(target)],
        stdin=subprocess.DEVNULL,
        stdout=log,stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
pidfile.write_text(str(proc.pid)+"\n")
os.chmod(pidfile,0o600)
time.sleep(2)
assert proc.poll() is None,"RECLAIMER_FAILED_TO_START"
print("RECLAIMER_STARTED_PID",proc.pid,flush=True)
print("NO_COMFY_SERVICE_RESTARTED",flush=True)
print("NO_NEW_GENERATION_SUBMITTED",flush=True)
