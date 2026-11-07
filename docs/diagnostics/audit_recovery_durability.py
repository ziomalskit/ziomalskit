"""Read-only durability audit for AJ instance 59453490.

Emits only booleans, file hashes, mount topology, summary counts and backup
presence. Does not print user content, environment secrets, filenames in user
output, or personal prompts.
"""
import hashlib,json,os,pathlib,subprocess,time,collections
root=pathlib.Path('/workspace/H3_VAST_MOBILE')
repo=pathlib.Path('/opt/aj/ziomalskit')
def datafile(path):
    try:
        p=pathlib.Path(path)
        if not p.is_file():return {'exists':False}
        st=p.stat()
        return {'exists':True,'bytes':st.st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                'mtime_age_sec':round(time.time()-st.st_mtime)}
    except Exception as e:return {'error':type(e).__name__}
for label,path in [
('repo_controller',repo/'h3/app/main.py'),
('workspace_controller',root/'app/main.py'),
('repo_mistral',repo/'h3/custom_nodes/aj_production/prompt_completion.py'),
('workspace_mistral',root/'custom_nodes/aj_production/prompt_completion.py'),
('comfy_mistral',pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/prompt_completion.py')),
('repo_timeline',repo/'h3/custom_nodes/aj_production/temporal.py'),
('workspace_timeline',root/'custom_nodes/aj_production/temporal.py'),
('comfy_timeline',pathlib.Path('/workspace/ComfyUI/custom_nodes/aj_production/temporal.py')),
('queue',root/'state/queue.json')]:
    print('FILE',label,json.dumps(datafile(path)),flush=True)
try:
    d=json.loads((root/'state/queue.json').read_text())
    jobs=d if isinstance(d,list) else d.get('queue',[])
    print('QUEUE_JOBS',len(jobs),'STATES',dict(collections.Counter(str(j.get('status')) for j in jobs)),flush=True)
except Exception as e:print('QUEUE_READ_ERROR',type(e).__name__,flush=True)
for label,p in [('root',pathlib.Path('/workspace')),
                ('models',pathlib.Path('/workspace/ComfyUI/models')),
                ('comfy_output',pathlib.Path('/workspace/ComfyUI/output')),
                ('state',root/'state'),('diagnostics',pathlib.Path('/workspace/.aj-diagnostics'))]:
    try:
        rs=subprocess.run(['findmnt','-T',str(p),'-n','-o','TARGET,SOURCE,FSTYPE'],text=True,capture_output=True,timeout=10)
        row=rs.stdout.strip().splitlines()
        source=row[0] if rs.returncode==0 and row else 'UNKNOWN'
        print('MOUNT',label,'exists',p.exists(),'resolved',source[:230],flush=True)
    except Exception as e:print('MOUNT_ERR',label,type(e).__name__,flush=True)
for label,p in [('LLM',pathlib.Path('/workspace/ComfyUI/models/LLM')),
                ('video_output',pathlib.Path('/workspace/ComfyUI/output'))]:
    try:
        count=sum(1 for item in p.iterdir()) if p.is_dir() else -1
        print('DIRECTORY',label,'exists',p.is_dir(),'top_level_entries',count,flush=True)
    except Exception as e:print('DIR_ERR',label,type(e).__name__,flush=True)
for name,path in [('source',repo),('workspace',root)]:
    try:
        r=subprocess.run(['git','-C',str(path),'status','--porcelain','-uno'],text=True,
                         capture_output=True,timeout=12)
        lines=r.stdout.splitlines()
        print('GIT',name,'is_repo',r.returncode==0,'tracked_modifications',len(lines),
              'porcelain_codes',dict(collections.Counter(x[:2] for x in lines)),flush=True)
        if r.returncode==0:
            branch=subprocess.run(['git','-C',str(path),'branch','--show-current'],text=True,
                         capture_output=True,timeout=8)
            print('GIT_BRANCH',name,branch.stdout.strip()[:80],flush=True)
    except Exception as e:print('GIT_ERR',name,type(e).__name__,flush=True)
for label,p in [('rotator_pid',pathlib.Path('/workspace/.aj-diagnostics/aj_gh_key_rotator.pid')),
                 ('rotator_helper',pathlib.Path('/workspace/.aj-diagnostics/aj_gh_key_rotator.py'))]:
    print('HELPER',label,'exists',p.exists(),flush=True)
try:
    p=pathlib.Path('/workspace/.aj-diagnostics/aj_gh_key_rotator.pid')
    pid=int(p.read_text())
    alive=b'aj_gh_key_rotator.py' in pathlib.Path('/proc',str(pid),'cmdline').read_bytes()
    print('SSH_ROTATOR_ALIVE',alive,flush=True)
except Exception as e:print('SSH_ROTATOR_STATE',type(e).__name__,flush=True)
for lab in ('/workspace/.aj-diagnostics','/workspace/H3_VAST_MOBILE/state'):
    p=pathlib.Path(lab)
    try:print('LOCAL_BACKUPS',p.name, len([x for x in p.iterdir() if 'before-' in x.name or 'backup' in x.name]),flush=True)
    except Exception as e: print('LOCAL_BACKUPS_ERR',type(e).__name__,flush=True)
print('AUDIT_READ_ONLY_PASS',flush=True)
