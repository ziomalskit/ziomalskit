import base64,json,os,pathlib,shlex,subprocess,time,urllib.request,urllib.error,urllib.parse,uuid
os.umask(0o077)
root=pathlib.Path('/workspace');state=root/'.aj-diagnostics';runtime_path=root/'H3_VAST_MOBILE/runtime.env'
runtime={};lexer=shlex.shlex(runtime_path.read_text(),posix=True);lexer.whitespace_split=True
for token in lexer:
 key,sep,value=token.partition('=')
 if sep:runtime[key]=value
headers={'Authorization':'Basic '+base64.b64encode((runtime.get('H3_PANEL_USER','h3')+':'+runtime['H3_PANEL_PASSWORD']).encode()).decode()}
def request(path,data=None,content_type='application/json',raw=False):
 req=urllib.request.Request('http://127.0.0.1:7860'+path,data=data,headers={**headers,**({'Content-Type':content_type} if data is not None else {})})
 try:
  with urllib.request.urlopen(req,timeout=60) as response:
   body=response.read();return body if raw else json.loads(body)
 except urllib.error.HTTPError as error:
  detail=error.read(5000).decode('utf-8','replace');raise RuntimeError('Panel HTTP '+str(error.code)+': '+detail)
assert (state/'native-27b-pass.json').is_file(),'Native 27B inference must pass before normal use'
existing=request('/api/jobs')['jobs']
assert not any(j.get('status') in {'rendering','prompting','preparing','queued'} for j in existing),'Work already active; do not add smoke jobs'
fixtures=state/'smoke-images';fixtures.mkdir(exist_ok=True)
fixture_code=r'''
from PIL import Image,ImageDraw
from pathlib import Path
root=Path('/workspace/.aj-diagnostics/smoke-images')
for i in range(6):
 im=Image.new('RGB',(768,512),(235-i*3,239-i*2,244));d=ImageDraw.Draw(im)
 d.ellipse((232,405,538,451),fill=(200,205,211))
 offset=(i-2)*5
 d.rounded_rectangle((290+offset,130,475+offset,285),radius=22,fill=(45,158,98),outline=(22,77,54),width=5)
 d.rounded_rectangle((305+offset,290,460+offset,405),radius=18,fill=(38,140,86),outline=(22,77,54),width=5)
 d.ellipse((330+offset,183,356+offset,209),fill=(245,249,255));d.ellipse((411+offset,183,437+offset,209),fill=(245,249,255))
 d.rectangle((348+offset,242,421+offset,252),fill=(22,77,54));d.rectangle((350+offset,320,415+offset,365),fill=(245,143,42))
 d.line((310+offset,315,259+offset,360),fill=(38,140,86),width=26);d.line((460+offset,315,505+offset,260-i*3),fill=(38,140,86),width=26)
 d.line((337+offset,397,329+offset,430),fill=(22,77,54),width=23);d.line((429+offset,397,438+offset,430),fill=(22,77,54),width=23)
 im.save(root/f'robot-{i+1}.png')
'''
subprocess.run(['/opt/aj/venv/bin/python','-'],input=fixture_code.encode(),check=True)
uploads=state/'smoke-uploads.json'
if uploads.exists():pictures=json.loads(uploads.read_text())
else:
 pictures=[]
 for file in sorted(fixtures.glob('*.png')):
  boundary='AJ'+uuid.uuid4().hex
  body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\nContent-Type: image/png\r\n\r\n'.encode()+file.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
  pictures.append(request('/api/upload',body,'multipart/form-data; boundary='+boundary)['filename'])
 uploads.write_text(json.dumps(pictures))
assert len(pictures)==6
runtime['H3_ALLOW_SUBMISSIONS']='1'
env={**os.environ,**runtime,'PATH':'/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/bin:/bin','CUDA_HOME':'/usr/local/cuda','LD_LIBRARY_PATH':'/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64'}
subprocess.run(['/opt/aj/venv/bin/python',str(root/'H3_VAST_MOBILE/scripts/runtime_config.py'),'write-env',str(runtime_path)],env=env,check=True,stdout=subprocess.DEVNULL)
with (state/'enable-panel.log').open('ab') as log:subprocess.run(['bash',str(root/'H3_VAST_MOBILE/scripts/service_ctl.sh'),'restart','panel'],env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
reports=[]
for profile in ['h3_full','10eros_full']:
 body_file=state/(profile+'-smoke-body.json')
 if body_file.exists():body=body_file.read_bytes()
 else:
  payload={'request_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'aj-59453490-robot-smoke-'+profile)),'profile':profile,'prompt_mode':'manual_final','manual_final_prompt':'A small green toy robot with a square head and orange chest slowly lifts its right arm and waves twice in a bright white studio. The camera stays still in a medium shot. Soft even daylight, smooth continuous motion, quiet mechanical clicking. No people, no writing, no scene cuts.','pictures':pictures,'audio':[],'batches':1,'duration_seconds':5,'seed':42,'random_each':False,'loras':[{'id':'hm-breasts-v2','enabled':False,'strength':1.0}],'soft_timeout_minutes':20}
  if profile=='h3_full':
   payload['prompt_mode']='autoprompt'
   payload['prompt']=payload.pop('manual_final_prompt')
  body=json.dumps(payload).encode();body_file.write_bytes(body)
 ack_file=state/(profile+'-smoke-ack.json')
 if ack_file.exists():ack=json.loads(ack_file.read_text())
 else:
  ack=request('/api/batches',body);ack_file.write_text(json.dumps(ack))
 batches=ack['created_batches'];assert len(batches)==1
 # Exercise the normal autoprompt path once, retaining only its first candidate.
 # The batch contract remains unchanged; surplus candidates are cancelled.
 if profile=='h3_full':
  for item in request('/api/jobs')['jobs']:
   if item.get('batch_id') in batches and item.get('candidate_index')!=1 and item.get('status') not in {'cancelled','completed','rejected','prompt_failed','render_failed'}:
    request('/api/jobs/'+item['id']+'/cancel',b'')
 deadline=time.monotonic()+1800;previous=None
 while time.monotonic()<deadline:
  matches=[j for j in request('/api/jobs')['jobs'] if j.get('batch_id') in batches and (profile!='h3_full' or j.get('candidate_index')==1)]
  assert len(matches)==1,'Smoke must retain exactly one candidate'
  job=matches[0];status=job.get('status')
  if status!=previous:print('RENDER_STATE',profile,status,flush=True);previous=status
  if status=='completed':break
  if status in {'render_failed','prompt_failed','cancelled','render_stuck_skipped','reconciliation_required','failed'} or job.get('reconciliation_required'):
   print('RENDER_FAILED',json.dumps({k:job.get(k) for k in ['id','status','error','last_error','render_prompt_id','reconciliation_required']}));raise SystemExit(1)
  time.sleep(5)
 else:raise SystemExit('Render deadline reached; retain existing job identity and inspect history')
 videos=job.get('video_outputs',[]);assert videos,'Completed render has no canonical video output'
 query=urllib.parse.parse_qs(urllib.parse.urlsplit(videos[0]).query)
 path=(root/'ComfyUI/output'/query.get('subfolder',[''])[0]/query['filename'][0]).resolve()
 assert path.is_relative_to((root/'ComfyUI/output').resolve()) and path.is_file()
 probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],text=True))
 video=next(s for s in probe['streams'] if s['codec_type']=='video')
 report={'profile':profile,'job_id':job['id'],'video':videos[0],'file':str(path),'size_bytes':path.stat().st_size,'width':video['width'],'height':video['height'],'duration':probe['format']['duration'],'frames':video.get('nb_frames'),'wall_seconds':job.get('render_duration_seconds')}
 reports.append(report);print('RENDER_COMPLETE',json.dumps(report),flush=True)
(state/'render-smoke-report.json').write_text(json.dumps(reports,indent=2))
print('BOTH_FULL_PROFILES_RENDERED')
