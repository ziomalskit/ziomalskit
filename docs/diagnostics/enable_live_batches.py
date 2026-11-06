"""Explicit user-authorized live inference enablement. No test jobs or submissions."""
import collections,json,os,pathlib,subprocess,tempfile,urllib.error,urllib.request

state=pathlib.Path('/workspace/H3_VAST_MOBILE/state/queue.json')
runtime=pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env')
if not state.is_file() or not runtime.is_file():
    raise SystemExit('BLOCKED: controller persistence or runtime settings missing')
doc=json.loads(state.read_text())
jobs=doc if isinstance(doc,list) else doc.get('queue',[])
counts=collections.Counter(str(j.get('status')) for j in jobs)
terminal={'prompt_failed','render_failed','completed','cancelled','rejected',
          'prompt_stuck_skipped','render_stuck_skipped','review_skipped_shutdown'}
if any(status not in terminal for status in counts):
    raise SystemExit('BLOCKED: queue contains jobs that may still run; no config changed')
if not counts or counts != {'prompt_failed':30}:
    raise SystemExit('BLOCKED: unexpected historical job state; no config changed')
text=runtime.read_text()
old_line='H3_ALLOW_SUBMISSIONS=0\n'
new_line='H3_ALLOW_SUBMISSIONS=1\n'
if text.count(old_line)!=1 or new_line in text:
    raise SystemExit('BLOCKED: runtime flag unexpectedly changed; no config changed')

def private_replace(new_content):
    fd,temp=tempfile.mkstemp(prefix='.aj-live-',dir=runtime.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,runtime)
        fd=os.open(runtime.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:pathlib.Path(temp).unlink(missing_ok=True)

service=['bash','/workspace/H3_VAST_MOBILE/scripts/service_ctl.sh','restart','panel']
status=['bash','/workspace/H3_VAST_MOBILE/scripts/service_ctl.sh','status','all']
private_replace(text.replace(old_line,new_line))
try:
    proc=subprocess.run(service,capture_output=True,timeout=200)
    if proc.returncode:
        raise RuntimeError('panel restart failed with code '+str(proc.returncode))
    check=subprocess.run(status,capture_output=True,timeout=30)
    output=check.stdout.decode('utf-8','replace')
    if check.returncode or not all(x+' RUNNING' in output for x in ('render','prompt','panel')):
        raise RuntimeError('owned services not all running')
    try:
        urllib.request.urlopen('http://127.0.0.1:7860/',timeout=10)
        raise RuntimeError('panel unexpectedly permits unauthenticated access')
    except urllib.error.HTTPError as e:
        if e.code!=401:raise RuntimeError('panel authentication not ready')
    processes=[]
    for p in pathlib.Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            command=(p/'cmdline').read_bytes().replace(b'\x00',b' ')
            if b'uvicorn app.main:app' in command and b'--port 7860' in command:
                env=(p/'environ').read_bytes().split(b'\x00')
                processes.append(b'H3_ALLOW_SUBMISSIONS=1' in env)
        except (OSError,PermissionError):continue
    if processes != [True]:
        raise RuntimeError('panel process does not have exactly one enabled runtime')
    print('LIVE_BATCHES_ENABLED',flush=True)
    print('PREVIOUS_FAILURES_TERMINAL',sum(counts.values()),flush=True)
    print('PANEL_AUTHENTICATED',flush=True)
    print('COMFY_PROMPT_RENDER_RUNNING',flush=True)
    print('NO_JOBS_SUBMITTED',flush=True)
except Exception as error:
    private_replace(text)
    restored=subprocess.run(service,capture_output=True,timeout=200)
    print('ROLLBACK_TO_SUBMISSIONS_OFF',restored.returncode,flush=True)
    raise RuntimeError('Unable to enable production safely: '+str(error))
