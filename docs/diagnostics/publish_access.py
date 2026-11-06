import base64,hashlib,json,os,pathlib,re,shlex,subprocess,time,urllib.request,urllib.error
os.umask(0o077)
state=pathlib.Path('/workspace/.aj-diagnostics');state.mkdir(exist_ok=True)
try:urllib.request.urlopen('http://127.0.0.1:7860/',timeout=10)
except urllib.error.HTTPError as error:assert error.code==401,'Panel must require authentication'
else:raise SystemExit('Expected panel authentication challenge')
url='https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-linux-amd64'
expected='1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872'
binary=state/'cloudflared'
if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest()!=expected:
 temp=binary.with_suffix('.part')
 with urllib.request.urlopen(url,timeout=60) as response,temp.open('wb') as output:
  while chunk:=response.read(1024*1024):output.write(chunk)
 assert temp.stat().st_size==40274445 and hashlib.sha256(temp.read_bytes()).hexdigest()==expected
 os.chmod(temp,0o700);temp.replace(binary)
log=state/'cloudflared.log'
with log.open('ab',buffering=0) as output:
 process=subprocess.Popen([str(binary),'tunnel','--no-autoupdate','--protocol','http2','--url','http://127.0.0.1:7860'],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
(state/'cloudflared.pid').write_text(str(process.pid))
public_url=None
for _ in range(90):
 text=log.read_text(errors='replace')
 found=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com',text)
 if found:public_url=found[-1];break
 if process.poll() is not None:raise SystemExit('HTTPS tunnel process exited: '+text[-1500:])
 time.sleep(1)
assert public_url,'HTTPS tunnel did not publish its address'
runtime={}
lexer=shlex.shlex(pathlib.Path('/workspace/H3_VAST_MOBILE/runtime.env').read_text(),posix=True);lexer.whitespace_split=True
for token in lexer:
 key,sep,value=token.partition('=')
 if sep:runtime[key]=value
credentials={'url':public_url,'username':runtime.get('H3_PANEL_USER','h3'),'password':runtime['H3_PANEL_PASSWORD']}
headers={'Authorization':'Basic '+base64.b64encode((credentials['username']+':'+credentials['password']).encode()).decode()}
for attempt in range(24):
 try:
  try:urllib.request.urlopen(public_url+'/',timeout=15)
  except urllib.error.HTTPError as error:
   if error.code!=401:raise
  else:raise SystemExit('Public URL does not require authentication')
  with urllib.request.urlopen(urllib.request.Request(public_url+'/api/config',headers=headers),timeout=20) as response:
   assert response.status==200
  break
 except (urllib.error.HTTPError,urllib.error.URLError):
  if attempt==23:raise
  time.sleep(5)
public_key='-----BEGIN PUBLIC KEY-----\nMIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA4F0s/el19qPWAzQSpFXA\nZrks3y3kajwqSjtJ50s1HC9OdOVF9BsrmU2CMviZJbd7hdXtYw/6EORIN8bkaLva\nYl9xD7ssejVjrXGr9e4ojLQO1bz07q7mygaOR19vPO9hKkZIC/N0cv4TvtgQmakr\naQsbV+iiL/qQR75JvBSuxk+QD/ZG6YyaijqcFxN/ChfThkBoZxN7axiP2JcARDT8\n25tWS/F0cNmyfdAyHFW03Yk2o8LSVsLce/woydogOUcLvpUJm8qE0COM8Tem6t49\nnO1ivlkGHiRBm7Tl/d+CO6xPKBQv8m1BZgna/vcm1qHUqpGXsbqjeU0skNePCsfI\n2cV8XaLUaydSuWQt2Uc2AWPDnycbZkY0ziR8IA9cEoA7nNLBPgrw/NniE3KWQUJ4\nJXlqtye/Y+PfN78SA3JqQHKomRPrL1uYIlsrNEVb8eqqyFTId+fcqcHU17lV6sno\ndooHrzSIYEo/9yoG9wkGOwFa6w/gqmkrX9fCGOCmaRmhAgMBAAE=\n-----END PUBLIC KEY-----\n'
key_file=state/'operator-envelope.pub';key_file.write_text(public_key)
result=subprocess.run(['openssl','pkeyutl','-encrypt','-pubin','-inkey',str(key_file),'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256'],input=json.dumps(credentials).encode(),capture_output=True,check=True)
print('PUBLIC_HTTPS_AUTH_PASS',public_url)
print('ACCESS_CIPHERTEXT='+base64.b64encode(result.stdout).decode())
