const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[2],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body,text:async()=>JSON.stringify(body)});
const snapshot=(token='boot:0',plan={})=>({control_token:token,instance:{actual_status:'running'},plan:{plan:'none',idle_minutes:0,cost_guard_usd:0,...plan},persistence:{safe_for_destroy_keep_data:false}});
function load(handler){
 const stored=new Map(),requests=[],elements={},alerts=[];let prompts=0;
 const element=id=>elements[id]||(elements[id]={value:'',textContent:'',innerHTML:'',files:[],disabled:false});
 const context=vm.createContext({document:{getElementById:element,activeElement:null,querySelectorAll:()=>[]},
  localStorage:{getItem:key=>stored.get(key)||null,setItem:(k,v)=>stored.set(k,v),removeItem:key=>stored.delete(key)},
  crypto:{randomUUID:()=>require('node:crypto').randomUUID()},FormData:class{append(){}},
  alert:e=>alerts.push(String(e)),confirm:()=>true,prompt:()=>{prompts++;return context.answer??'approved'},setInterval:()=>{},
  fetch:(url,options)=>{requests.push({url,options});return handler(url,options)}});
 vm.runInContext(source,context);
 return {context,element,elements,stored,requests,alerts,prompts:()=>prompts,run:s=>vm.runInContext(s,context)};
}
const scenarios={
 async approval(){
  let n=0;const h=load(()=>response(++n===1?{detail:'empty'}:{ok:true},n===1?400:200));
  h.run("lastJobs=[{id:'review',final_h3_prompt:'candidate'}];refresh=async()=>{}");h.context.answer='   ';
  await h.run("approve('review')");assert.equal(h.stored.size,0);
  h.context.answer='corrected';await h.run("approve('review')");
  assert.equal(h.prompts(),2);assert.equal(JSON.parse(h.requests[1].options.body).final_prompt,'corrected');assert.equal(h.stored.size,0);
 },
 async approval_uncertain(){
  for(const status of [408,500,502]){
   const h=load(()=>response({detail:'uncertain'},status));h.run("lastJobs=[{id:'review',final_h3_prompt:'candidate'}]");
   await h.run("approve('review')");h.context.answer='edited';await h.run("approve('review')");
   assert.equal(h.prompts(),1);assert.equal(h.requests[0].options.body,h.requests[1].options.body);assert.equal(h.stored.size,1);
  }
 },
 async approval_replacement(){
  const h=load(()=>{h.stored.set('h3.pendingApproval.review',JSON.stringify({final_prompt:'other tab'}));return response({},400)});
  h.run("lastJobs=[{id:'review',final_h3_prompt:'candidate'}]");await h.run("approve('review')");
  assert.equal(JSON.parse(h.stored.get('h3.pendingApproval.review')).final_prompt,'other tab');
 },
 async selection(){
  const a=deferred(),b=deferred(),h=load(url=>url.includes('/A/')?a.promise:b.promise);
  h.run("lastJobs=[{id:'A',status:'pending_review'},{id:'B',status:'pending_review'}]");
  const x=h.run("selectJob('A')"),y=h.run("selectJob('B')");
  b.resolve(response({status:'completed',final_h3_prompt:'B'}));await y;
  a.resolve(response({status:'pending_review',final_h3_prompt:'A'}));await x;
  assert.equal(h.run('selected'),'B');assert.equal(h.element('p4').textContent,'B');assert.match(h.element('selectedStatus').textContent,/completed/);
 },
 async jobs(){
  const old=deferred(),fresh=deferred();let n=0;const h=load(()=>++n===1?old.promise:fresh.promise);
  h.run('refreshPrompts=async()=>{}');const x=h.run('refresh()'),y=h.run('refresh()');
  fresh.resolve(response({jobs:[{id:'B',status:'completed'}]}));await y;
  old.resolve(response({jobs:[{id:'A',status:'pending_review'}]}));await x;
  assert.equal(h.run('lastJobs[0].id'),'B');assert.equal(h.run('lastJobs[0].status'),'completed');
 },
 async vast(){
  const old=deferred(),fresh=deferred();let n=0;const h=load(()=>++n===1?old.promise:fresh.promise);
  const x=h.run('refreshVast()'),y=h.run('refreshVast()');
  fresh.resolve(response(snapshot('boot:2',{cost_guard_usd:5})));await y;
  old.resolve(response({...snapshot('boot:1',{plan:'stop_after_queue'}),persistence:{safe_for_destroy_keep_data:true}}));await x;
  assert.equal(h.run('lifecycleToken'),'boot:2');assert.equal(h.element('costGuard').value,5);
  assert.equal(h.element('destroyKeepBtn').disabled,true);assert.match(h.element('vastPlan').textContent,/none/);
 },
 async guards(){
  let gen=0;const h=load((url,options)=>options?response({ok:true,control_token:'boot:'+ ++gen}):response(snapshot('boot:'+gen)));
  await h.run('refreshVast()');h.element('idleMin').value='2';h.element('costGuard').value='5';h.run('guardEdited()');
  await h.run('refreshVast()');assert.equal(h.element('idleMin').value,'2');assert.equal(h.element('costGuard').value,'5');
  await h.run('saveGuards()');const posts=h.requests.filter(r=>r.options).map(r=>JSON.parse(r.options.body));
  assert.equal(posts[0].idle_minutes,2);assert.equal(posts[1].cost_guard_usd,5);
  assert.equal(posts[0].control_token,'boot:0');assert.equal(posts[1].control_token,'boot:1');assert.equal(h.run('guardsDirty'),false);
 },
 async focused(){
  const h=load(()=>response(snapshot()));h.element('idleMin').value='7';h.context.document.activeElement=h.element('idleMin');
  await h.run('refreshVast()');assert.equal(h.element('idleMin').value,'7');
 },
 async edit_during_save(){
  const first=deferred();let n=0;const h=load((url,options)=>!options?response(snapshot('boot:2')):++n===1?first.promise:response({ok:true,control_token:'boot:2'}));
  h.run("lifecycleToken='boot:0'");h.element('idleMin').value='2';h.element('costGuard').value='5';h.run('guardEdited()');
  const save=h.run('saveGuards()');h.element('costGuard').value='9';h.run('guardEdited()');
  first.resolve(response({ok:true,control_token:'boot:1'}));await save;
  assert.equal(h.run('guardsDirty'),true);assert.equal(h.element('costGuard').value,'9');assert.equal(h.run('guardBaseToken'),'boot:2');
 },
 async partial_save(){
  let n=0;const h=load((url,options)=>!options?response(snapshot('boot:1')):++n===1?response({ok:true,control_token:'boot:1'}):response({},500));
  h.run("lifecycleToken='boot:0'");h.element('idleMin').value='2';h.element('costGuard').value='5';h.run('guardEdited()');
  await h.run('saveGuards()');assert.equal(h.run('guardsDirty'),true);assert.equal(h.run('guardBaseToken'),'boot:1');assert.equal(h.element('costGuard').value,'5');
 },
 async guard_conflict(){
  let n=0,gen=2;const h=load((url,options)=>!options?response(snapshot('boot:'+gen)):++n===1?response({},409):response({ok:true,control_token:'boot:'+ ++gen}));
  h.run("lifecycleToken='boot:0'");h.element('idleMin').value='2';h.element('costGuard').value='5';h.run('guardEdited()');
  await h.run('saveGuards()');assert.equal(n,1);assert.equal(h.run('guardsDirty'),true);assert.equal(h.run('guardBaseToken'),'boot:2');
  await h.run('saveGuards()');assert.equal(n,3);assert.equal(h.run('guardsDirty'),false);
  const posts=h.requests.filter(r=>r.options).map(r=>JSON.parse(r.options.body));assert.equal(posts[1].control_token,'boot:2');assert.equal(posts[2].cost_guard_usd,5);
 },
 async restart(){
  const wait=deferred(),h=load(()=>wait.promise);const first=h.run("restartService('render')");
  await h.run("restartService('render')");assert.equal(h.requests.length,1);assert.equal(h.element('restart-render').disabled,true);
  wait.resolve(response({ok:true}));await first;assert.equal(h.element('restart-render').disabled,false);
 },
 async action_snapshot(){
  const old=deferred();const h=load((url,options)=>!options?old.promise:response({ok:true,control_token:'boot:1'}));
  h.run("lifecycleToken='boot:0'");const poll=h.run('refreshVast()');
  await h.run("performVastAction('cancel_plan',{},lifecycleToken)");old.resolve(response(snapshot('boot:0',{plan:'stop_after_queue'})));await poll;
  assert.equal(h.run('lifecycleToken'),'boot:1');assert.equal(h.element('vastPlan').textContent,'');
 },
 async jobs_failure(){
  const h=load(()=>response({},500));h.run("lastJobs=[{id:'retained',status:'completed'}]");
  await assert.rejects(h.run('refresh()'));assert.equal(h.run('lastJobs[0].id'),'retained');
 }
};
scenarios[process.argv[3]]().catch(e=>{console.error(e);process.exitCode=1});
