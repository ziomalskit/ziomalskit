"""Execute actual frontend JS with capability data from the trusted backend."""
import json
from pathlib import Path
import subprocess
import unittest

from h3.app.workflow_adapters import get_adapter

ROOT = Path(__file__).resolve().parents[1]


class AdapterFrontendTests(unittest.TestCase):
    def run_scenario(self, scenario):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const workflow=JSON.parse(process.argv[2]),scenario=process.argv[3],stored=new Map(),requests=[];
let uploads=0,loseReply=scenario==='retry';
function load(schema=workflow){
 const elements={model:{value:'h3_full'},pic:{files:['anchor']},refs:{files:[1,2,3,4,5]},audio:{files:[]},
  prompt:{value:'CPU frontend scene'},duration:{value:'20'},batches:{value:'1'},timeout:{value:'8'},
  generateBatch:{},newBatch:{},loras:{innerHTML:''},prefetchTarget:{},terminalSection:{},advancedRenderSettings:{}};
 function container(){let markup='';return{get innerHTML(){return markup},set innerHTML(value){markup=value;
  for(const match of value.matchAll(/<input id="([^"]+)"[^>]*value="([^"]+)"/g))elements[match[1]]={value:match[2]};}}}
 elements.qualityControls=container();elements.advancedRenderControls=container();
 const context=vm.createContext({document:{getElementById:id=>elements[id]},
  localStorage:{getItem:key=>stored.get(key)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
  crypto:{randomUUID:()=>require('node:crypto').randomUUID()},FormData:class{append(){}},alert:()=>{},setInterval:()=>{},
  fetch:async(url,options)=>{
   if(url==='/api/config')return{ok:true,json:async()=>({default_profile:'h3_full',loras:[],profiles:[
    {id:'h3_full',label:'H3 Full',workflow:schema},{id:'10eros_full',label:'10Eros Full',workflow:schema}]})};
   if(url==='/api/upload')return{ok:true,json:async()=>({filename:'ref'+(++uploads)+'.png'})};
   assert.equal(url,'/api/batches');requests.push(JSON.parse(options.body));
   if(loseReply){loseReply=false;throw Error('synthetic lost reply')}
   return{ok:true,json:async()=>({created_batches:['durable-batch']})};
  }});
 vm.runInContext(source,context);vm.runInContext('refresh=async()=>{};terminalButtonState=()=>{}',context);
 return{context,elements};
}
(async()=>{
 let {context,elements}=load();await vm.runInContext('init()',context);
 if(scenario==='capabilities'){
  assert.match(elements.qualityControls.innerHTML,/Render area \(MPx\)/);
  assert.equal(elements.render_target_mpx.value,'0.4');assert.equal(elements.render_first_steps.value,'24');
  assert.equal(elements.render_second_steps.value,'4');assert.equal(elements.advancedRenderSettings.hidden,false);
  const markup=elements.qualityControls.innerHTML+elements.advancedRenderControls.innerHTML;
  assert.doesNotMatch(markup,/on_steps|off_steps|1731|7334|7335/);
  const first=workflow.render_controls.first_steps;
  assert.match(markup,new RegExp(`min="${first.minimum}" max="${first.maximum}"`));
 }else if(scenario==='unsupported'){
  let next=load({...workflow,render_controls:{}});context=next.context;elements=next.elements;
  await vm.runInContext('init()',context);
  assert.equal(elements.qualityControls.innerHTML,'');assert.equal(elements.advancedRenderControls.innerHTML,'');
  assert.equal(elements.advancedRenderSettings.hidden,true);
  await vm.runInContext('generate()',context);assert.deepEqual(requests[0].render_settings,{});
 }else if(scenario==='submit'||scenario==='retry'){
  elements.render_target_mpx.value='.8';elements.render_first_steps.value='32';elements.render_second_steps.value='8';
  await vm.runInContext('generate()',context);
  assert.deepEqual(requests[0].render_settings,{target_mpx:.8,first_steps:32,second_steps:8});
  assert.doesNotMatch(JSON.stringify(requests[0]),/node_id|widget_path|api_node/);
  if(scenario==='retry'){
   assert.deepEqual(JSON.parse(stored.get('h3.pendingBatch')).render_settings,requests[0].render_settings);
   const edited=JSON.parse(JSON.stringify(workflow));edited.render_controls.target_mpx.default=1.0;
   let next=load(edited);context=next.context;elements=next.elements;await vm.runInContext('init()',context);
   elements.model.value='10eros_full';vm.runInContext('profileChanged()',context);
   elements.render_target_mpx.value='1.5';elements.render_first_steps.value='50';
   await vm.runInContext('generate()',context);assert.deepEqual(requests[1],requests[0]);assert.equal(uploads,6);
  }
 }else if(scenario==='invalid'){
  for(const [key,value] of [['target_mpx',''],['target_mpx','NaN'],['target_mpx','Infinity'],
                           ['target_mpx','2.1'],['first_steps','1.5'],['second_steps','0']]){
   elements['render_'+key].value=value;await vm.runInContext('generate()',context);
   assert.equal(requests.length,0);elements['render_'+key].value=String(workflow.render_controls[key].default);
  }
 }else if(scenario==='schema_labels'){
  const changed=JSON.parse(JSON.stringify(workflow));changed.render_controls.target_mpx.label='Area <verified>';
  changed.render_controls.target_mpx.default=.6;changed.render_controls.target_mpx.maximum=1.0;
  let next=load(changed);context=next.context;elements=next.elements;await vm.runInContext('init()',context);
  assert.match(elements.qualityControls.innerHTML,/Area &lt;verified&gt;/);
  assert.equal(elements.render_target_mpx.value,'0.6');assert.match(elements.qualityControls.innerHTML,/max="1"/);
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", script, str(ROOT / "h3/static/index.html"),
                                 json.dumps(get_adapter("h3_v20").render_capabilities()), scenario],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_supported_controls_render_from_backend_capabilities(self):
        self.run_scenario("capabilities")

    def test_unsupported_controls_absent_and_advanced_section_hidden(self):
        self.run_scenario("unsupported")

    def test_values_submitted_as_typed_render_settings_without_graph_addresses(self):
        self.run_scenario("submit")

    def test_retry_reload_profile_and_default_changes_preserve_exact_settings_body(self):
        self.run_scenario("retry")

    def test_invalid_nonfinite_blank_and_fractional_steps_never_submit(self):
        self.run_scenario("invalid")

    def test_labels_defaults_and_bounds_are_derived_from_capability_schema(self):
        self.run_scenario("schema_labels")
