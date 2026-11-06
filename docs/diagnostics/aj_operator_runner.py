"""Temporary operator bridge for the already-rented AJ instance; no provider key."""
import base64, hashlib, json, os, pathlib, re, subprocess, time, urllib.error, urllib.request
REPO='ziomalskit/ziomalskit'
BRANCH='diag/vast-init-59453490'
API=f'https://api.github.com/repos/{REPO}/contents/'
HEADERS={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
def get_file(path):
    req=urllib.request.Request(API+path+'?ref='+BRANCH,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=25) as response: obj=json.load(response)
    return base64.b64decode(obj['content']), obj['sha']
def publish(report):
    path='docs/diagnostics/59453490-status.json'
    data={'message':'[skip ci] Record live diagnostic stage '+report['id'],'branch':BRANCH,'content':base64.b64encode(json.dumps(report,indent=2).encode()).decode()}
    try: data['sha']=get_file(path)[1]
    except urllib.error.HTTPError as e:
        if e.code!=404: raise
    req=urllib.request.Request(API+path,data=json.dumps(data).encode(),method='PUT',headers=HEADERS)
    with urllib.request.urlopen(req,timeout=25) as response: assert response.status in (200,201)
key=str(pathlib.Path(os.environ['RUNNER_TEMP'],'aj-key'))
options=['-i',key,'-p','16843','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(pathlib.Path(os.environ['RUNNER_TEMP'],'aj-known-hosts')),'-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3']
ssh=['ssh',*options,'root@154.59.156.40']
deadline=time.monotonic()+10500
for attempt in range(120):
    probe=subprocess.run([*ssh,'true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if probe.returncode==0: break
    time.sleep(5)
else: raise SystemExit('SSH authorization/reachability failed')
print('Authenticated SSH operator bridge ready',flush=True)
executed=set()
last_command=time.monotonic()
try:
    while time.monotonic()<deadline:
        control=json.loads(get_file('docs/diagnostics/59453490-control.json')[0])
        ident=control['id']
        if control.get('action')=='close':
            publish({'id':ident,'state':'closed'});break
        if ident in executed:
            if time.monotonic()-last_command>1200:break
            time.sleep(10);continue
        path=control['script_path']
        assert re.fullmatch(r'docs/diagnostics/[A-Za-z0-9_-]+\.py',path)
        script=get_file(path)[0]
        assert hashlib.sha256(script).hexdigest()==control['sha256'],'Stage checksum mismatch'
        timeout=min(max(int(control.get('timeout_seconds',600)),30),7200)
        executed.add(ident);last_command=time.monotonic()
        publish({'id':ident,'state':'running'})
        try:
            result=subprocess.run([*ssh,'python3 -'],input=script,capture_output=True,timeout=timeout)
            report={'id':ident,'state':'completed','exit_code':result.returncode,'stdout':result.stdout.decode('utf-8','replace')[-50000:],'stderr':result.stderr.decode('utf-8','replace')[-12000:]}
        except subprocess.TimeoutExpired:
            report={'id':ident,'state':'timeout','exit_code':124}
        publish(report)
        print('Stage '+ident+' finished: '+report['state']+' / '+str(report.get('exit_code')),flush=True)
        last_command=time.monotonic()
finally:
    pathlib.Path(key).unlink(missing_ok=True)
    pathlib.Path(key+'.pub').unlink(missing_ok=True)
