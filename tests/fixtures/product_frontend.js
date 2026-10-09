const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
const response=(data,ok=true)=>({ok,json:async()=>data});
function load(handler){
 const elements={},requests=[],sockets=[],stored=new Map();
 const element=id=>elements[id]||(elements[id]={open:false,hidden:false,disabled:false,textContent:'',innerHTML:'',value:'',focus(){this.focused=true}});
 class Socket{
  constructor(url,protocols){this.url=url;this.protocols=protocols;this.readyState=0;this.sent=[];sockets.push(this)}
  send(text){this.sent.push(text)}
  close(){if(this.readyState===3)return;this.readyState=3;this.onclose?.()}
 }
 const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[]},
  localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},setInterval:()=>{},alert:()=>{},
  URL,location:{href:'https://fixture/panel',protocol:'https:'},WebSocket:Socket,
  fetch:async(url,options)=>{requests.push({url,options});return handler(url,options)}});
 vm.runInContext(source,context);
 return {context,element,requests,sockets,stored,run:s=>vm.runInContext(s,context),open:section=>{element('advanced').open=true;element(section).open=true}};
}
const checks=detail=>({checks:[{label:'Model files (provisional)',status:'WARN',detail}]});
const scenarios={
 async results(){
  const h=load(()=>{});
  h.run(`lastJobs=[{id:'job-1',status:'completed',batch_seq:2,candidate_index:4,render_seed:42,render_duration_seconds:12.34,
   video_outputs:['/api/proxy/render/view?filename=final.mp4&type=output','javascript:alert(1)'],
   outputs:['/api/proxy/render/view?filename=preview.png']},
   {id:'running',status:'render_running',outputs:['/api/proxy/render/view?filename=not-final.mp4']}];renderJobs()`);
  const text=h.element('jobs').innerHTML;
  assert.match(text,/OPEN MP4/);assert.match(text,/12\.3 s render/);
  assert.match(text,/Batch 2 · candidate #4 · job job-1 · seed 42/);
  assert.match(text,/rel="noopener"/);assert.ok(!text.includes('javascript:'));
  assert.ok(!text.includes('preview.png'));assert.ok(!text.includes('not-final.mp4'));assert.ok(!text.includes('>output<'));
 },
 async config(){
  const h=load(()=>response({loras:[],prompt_prefetch:2}));h.run('refresh=async()=>{}');
  await h.run('init()');assert.equal(h.element('prefetchTarget').textContent,2);
  assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/config');
 },
 async closed(){
  const h=load(()=>{throw Error('must not fetch')});
  await h.run('refreshDiagnostics()');await h.run('refreshLogs()');assert.equal(h.requests.length,0);
  assert.match(html,/<details class="card" id="advanced"(?![^>]*\bopen\b)/);
 },
 async diagnostics(){
  const h=load(()=>response(checks('<script>secret</script>')));h.open('diagnosticsSection');
  await h.run('refreshDiagnostics()');assert.match(h.element('diagnostics').innerHTML,/WARN/);
  assert.match(h.element('diagnostics').innerHTML,/provisional/);
  assert.ok(!h.element('diagnostics').innerHTML.includes('<script>'));
  assert.equal(h.element('diagnosticsRefresh').disabled,false);
 },
 async diagnostics_race(){
  const old=deferred(),fresh=deferred();let n=0;const h=load(()=>++n===1?old.promise:fresh.promise);h.open('diagnosticsSection');
  const a=h.run('refreshDiagnostics()'),b=h.run('refreshDiagnostics()');
  fresh.resolve(response(checks('fresh')));await b;old.resolve(response(checks('old')));await a;
  assert.match(h.element('diagnostics').innerHTML,/fresh/);assert.ok(!h.element('diagnostics').innerHTML.includes('old'));
 },
 async logs(){
  let data={status:'not_created',available:false,text:''};const h=load(()=>response(data));h.open('logsSection');h.element('logSource').value='panel';
  await h.run('refreshLogs()');assert.equal(h.element('logStatus').textContent,'Not created yet');
  data={status:'available',available:true,text:'<script>raw log</script>',line_count:200,truncated:true};
  await h.run('refreshLogs()');assert.equal(h.element('liveLogs').textContent,data.text);
  assert.match(h.element('logStatus').textContent,/truncated/);assert.equal(h.element('liveLogs').innerHTML,'');
  assert.equal(h.requests[0].url,'/api/logs/panel?lines=200');
 },
 async logs_race(){
  const old=deferred(),fresh=deferred();let n=0;const h=load(()=>++n===1?old.promise:fresh.promise);h.open('logsSection');
  h.element('logSource').value='render';const a=h.run('refreshLogs()');
  h.element('logSource').value='prompt';const b=h.run('refreshLogs()');
  fresh.resolve(response({available:true,text:'fresh prompt',line_count:1}));await b;
  old.resolve(response({available:true,text:'stale render',line_count:1}));await a;
  assert.equal(h.element('liveLogs').textContent,'fresh prompt');
 },
 async close_pending(){
  const wait=deferred(),h=load(()=>wait.promise);h.open('diagnosticsSection');
  const a=h.run('refreshDiagnostics()');h.element('advanced').open=false;h.run('advancedChanged()');
  wait.resolve(response(checks('stale closed')));await a;
  assert.equal(h.element('diagnostics').innerHTML,'');assert.equal(h.element('diagnosticsRefresh').disabled,false);
 },
 async terminal_disabled(){
  const h=load(()=>response({loras:[],terminal_enabled:false}));h.run('refresh=async()=>{}');
  await h.run('init()');h.open('terminalSection');await h.run('connectTerminal()');
  assert.equal(h.element('terminalSection').hidden,true);assert.equal(h.element('terminalConnect').disabled,true);
  assert.equal(h.element('terminalInput').disabled,true);assert.equal(h.sockets.length,0);assert.equal(h.requests.length,1);
  assert.match(html,/<details id="terminalSection" hidden/);
 },
 async terminal_connect(){
  const wait=deferred(),h=load(()=>wait.promise);h.open('terminalSection');h.run('terminalEnabled=true;terminalButtonState()');
  const first=h.run('connectTerminal()');await h.run('connectTerminal()');assert.equal(h.requests.length,1);
  wait.resolve(response({token:'A'.repeat(43)}));await first;
  const socket=h.sockets[0];assert.equal(socket.url,'wss://fixture/api/terminal/ws');
  assert.deepEqual(Array.from(socket.protocols),['h3-terminal','h3-session.'+'A'.repeat(43)]);
  assert.equal(h.requests[0].options.method,'POST');assert.equal(h.requests[0].options.cache,'no-store');
  socket.readyState=1;socket.onopen();assert.equal(h.element('terminalInput').disabled,false);
  socket.onmessage({data:'<script>raw output</script>'});assert.equal(h.element('terminalOutput').textContent,'<script>raw output</script>');
  assert.equal(h.element('terminalOutput').innerHTML,'');socket.onmessage({data:'x'.repeat(70000)});
  assert.equal(h.element('terminalOutput').textContent.length,65536);
  h.element('terminalInput').value='pwd';h.context.keyEvent={key:'Enter',preventDefault(){}};h.run('terminalKey(keyEvent)');
  h.run('interruptTerminal()');h.context.keyEvent={key:'c',ctrlKey:true,preventDefault(){}};h.run('terminalKey(keyEvent)');
  assert.deepEqual(socket.sent,['pwd\n','\x03','\x03']);assert.equal(h.stored.size,0);
  h.run('disconnectTerminal()');assert.equal(socket.readyState,3);assert.equal(h.element('terminalInput').disabled,true);
 },
 async terminal_handshake_race(){
  const wait=deferred(),h=load(()=>wait.promise);h.open('terminalSection');h.run('terminalEnabled=true');
  const first=h.run('connectTerminal()');h.element('advanced').open=false;h.run('advancedChanged()');
  wait.resolve(response({token:'A'.repeat(43)}));await first;
  assert.equal(h.sockets.length,0);assert.equal(h.run('terminalBusy'),false);assert.equal(h.element('terminalInput').value,'');
 },
 async terminal_stale_socket(){
  const h=load(()=>response({token:'A'.repeat(43)}));h.open('terminalSection');h.run('terminalEnabled=true');
  await h.run('connectTerminal()');const old=h.sockets[0];old.readyState=1;old.onopen();const staleClose=old.onclose;
  h.run('disconnectTerminal()');await h.run('connectTerminal()');const current=h.sockets[1];current.readyState=1;current.onopen();
  staleClose();old.onmessage({data:'stale'});assert.equal(h.element('terminalStatus').textContent,'Connected');
  assert.equal(h.element('terminalOutput').textContent,'');assert.equal(h.element('terminalInput').disabled,false);
  h.element('advanced').open=false;h.run('advancedChanged()');assert.equal(current.readyState,3);
 },
 async terminal_failure(){
  const h=load(()=>response({},false));h.open('terminalSection');h.run('terminalEnabled=true');
  await h.run('connectTerminal()');assert.equal(h.run('terminalBusy'),false);assert.equal(h.sockets.length,0);
  assert.match(h.element('terminalStatus').textContent,/unavailable/);assert.equal(h.element('terminalConnect').disabled,false);
 }
};
scenarios[process.argv[3]]().catch(error=>{console.error(error);process.exitCode=1});
