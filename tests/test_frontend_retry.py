"""Execute AJ's actual inline JS to check persistent retry behavior across reloads."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrontendRetryTests(unittest.TestCase):
    def test_batch_retry_survives_reload_preserves_body_and_new_action_gets_new_uuid(self):
        self.run_js("batch")

    def test_approval_retry_reuses_original_prompt_after_reload(self):
        self.run_js("approval")

    def test_old_confirmation_cannot_clear_another_tabs_new_pending_operation(self):
        self.run_js("replacement")

    def test_uuid_fallback_supports_browser_without_randomuuid(self):
        self.run_js("batch_fallback")

    def run_js(self, scenario):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const scenario=process.argv[2],stored=new Map(),requests=[];
let uploadCount=0,uuidCount=0,promptCount=0,loseReply=scenario!=='replacement';
const elements={pic:{files:['anchor']},refs:{files:['r1','r2','r3','r4','r5']},audio:{files:[]},
 prompt:{value:'identical scene'},model:{value:'native_int8'},batches:{value:'1'},timeout:{value:'8'},
 generateBatch:{},newBatch:{}};
function load(){
 const context=vm.createContext({
  document:{getElementById:id=>elements[id]},
  localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
  crypto:scenario==='batch_fallback'?{getRandomValues:bytes=>{uuidCount++;return require('node:crypto').randomFillSync(bytes)}}:
   {randomUUID:()=>`00000000-0000-4000-8000-${(++uuidCount).toString(16).padStart(12,'0')}`},
  FormData:class{append(){}},alert:()=>{},confirm:()=>true,setInterval:()=>{},
  prompt:()=>{promptCount++;return 'approved exact prompt'},
  fetch:async(url,options)=>{
   if(url==='/api/upload'){uploadCount++;return {ok:true,json:async()=>({filename:'ref'+uploadCount+'.png'})}}
   const body=JSON.parse(options.body); requests.push({url,body});
   if(scenario==='replacement'&&requests.length===1)stored.set('h3.pendingBatch',JSON.stringify({...body,request_id:'00000000-0000-4000-8000-000000000099'}));
   if(loseReply){loseReply=false;throw Error('server committed; response lost')}
   return {ok:true,json:async()=>url==='/api/batches'?{created_batches:['durable-batch']}:{ok:true,priority:'top'}};
  }
 });
 vm.runInContext(source,context);
 vm.runInContext("refresh=async()=>{};lastJobs=[{id:'review',final_h3_prompt:'candidate'}]",context);
 return context;
}
(async()=>{
 let context=load();
 if(scenario==='replacement'){
  await vm.runInContext('generate()',context);
  assert.equal(JSON.parse(stored.get('h3.pendingBatch')).request_id,'00000000-0000-4000-8000-000000000099');
  context=load();await vm.runInContext('generate()',context);
  assert.equal(requests[1].body.request_id,'00000000-0000-4000-8000-000000000099');assert.equal(stored.size,0);
 }else if(scenario.startsWith('batch')){
  const first=vm.runInContext('generate()',context);
  await vm.runInContext('generate()',context); // double click while uploads await
  await first;
  assert.equal(requests.length,1);assert.equal(uploadCount,6);assert.equal(uuidCount,1);
  assert.match(requests[0].body.request_id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.ok(stored.get('h3.pendingBatch'));
  context=load(); elements.prompt.value='edited form must not change retry';
  await vm.runInContext('generate()',context);
  assert.deepEqual(requests[1],requests[0]);assert.equal(uploadCount,6);
  assert.equal(stored.has('h3.pendingBatch'),false);
  await vm.runInContext('generate()',context);
  assert.notEqual(requests[2].body.request_id,requests[0].body.request_id);
  assert.equal(uuidCount,2);assert.equal(stored.size,0);
 }else{
  await vm.runInContext("approve('review')",context);
  assert.equal(promptCount,1);assert.ok(stored.get('h3.pendingApproval.review'));
  context=load();
  await vm.runInContext("approve('review')",context);
  assert.deepEqual(requests[1],requests[0]);assert.equal(promptCount,1);assert.equal(stored.size,0);
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", script, str(ROOT / "h3/static/index.html"), scenario],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
