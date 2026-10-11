"""Execute actual frontend JS with capability data from the trusted backend."""
import json
from pathlib import Path
import subprocess
import unittest

from h3.app.workflow_adapters import get_adapter
from h3.app.production import read_loras

ROOT = Path(__file__).resolve().parents[1]


class AdapterFrontendTests(unittest.TestCase):
    def run_scenario(self, scenario):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const workflow=JSON.parse(process.argv[2]),registry=JSON.parse(process.argv[3]),scenario=process.argv[4],stored=new Map(),requests=[];
let uploads=0,loseReply=scenario==='retry';
function load(schema=workflow){
 const elements={model:{value:'h3_full'},pic:{files:['anchor']},refs:{files:[1,2,3,4,5]},audio:{files:[]},
  prompt:{value:'CPU frontend scene'},duration:{value:'20'},batches:{value:'1'},timeout:{value:'8'},
  generateBatch:{},newBatch:{},prefetchTarget:{},terminalSection:{},advancedRenderSettings:{}};
 function container(){let markup='';return{get innerHTML(){return markup},set innerHTML(value){markup=value;
  for(const match of value.matchAll(/<input\b[^>]*\bid="([^"]+)"[^>]*>/g))elements[match[1]]={value:match[0].match(/value="([^"]+)"/)?.[1]??'',checked:/\bchecked\b/.test(match[0])};
  for(const match of value.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g))
   elements[match[1]]={value:match[2].match(/<option value="([^"]+)" selected/)?.[1]??'false'};
  for(const match of value.matchAll(/<div id="(render_field_[^"]+)"/g))elements[match[1]]={classes:new Set(),classList:{toggle:(name,on)=>{if(on)elements[match[1]].classes.add(name);else elements[match[1]].classes.delete(name)}}};
 }}}
 elements.qualityControls=container();elements.advancedRenderControls=container();
 elements.loras=container();
 const context=vm.createContext({document:{getElementById:id=>elements[id]},
  localStorage:{getItem:key=>stored.get(key)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
  crypto:{randomUUID:()=>require('node:crypto').randomUUID()},FormData:class{append(){}},alert:()=>{},setInterval:()=>{},
  fetch:async(url,options)=>{
   if(url==='/api/config')return{ok:true,json:async()=>({default_profile:'h3_full',loras:registry,profiles:[
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
  assert.equal(elements.render_latent_upscaler_enabled.value,'false');assert.equal(elements.render_latent_upscaler_mpx.value,'1.2');
  assert.equal(elements.render_second_pass_enabled.value,'false');assert.equal(elements.render_latent_upscaler_mpx.disabled,true);
  assert.equal(elements.render_second_steps.disabled,true);assert.equal(elements.render_second_pass_loras_enable_0.disabled,true);
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
  assert.deepEqual(requests[0].render_settings,{target_mpx:.8,first_steps:32,latent_upscaler_enabled:false,
   latent_upscaler_mpx:1.2,second_pass_enabled:false,second_steps:8,second_pass_loras:[]});
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
 }else if(scenario==='dependencies'){
  for(const key of ['latent_upscaler_enabled','second_pass_enabled'])elements['render_'+key].value='true';
  vm.runInContext('updateRenderDependencies()',context);
  assert.equal(elements.render_latent_upscaler_mpx.disabled,false);assert.equal(elements.render_second_steps.disabled,false);
  elements.render_latent_upscaler_mpx.value='1.5';elements.render_second_steps.value='7';
  elements.render_second_pass_loras_enable_0.checked=true;elements.render_second_pass_loras_strength_0.value='.75';
  vm.runInContext('updateRenderDependencies()',context);assert.equal(elements.render_second_pass_loras_strength_0.disabled,false);
  for(const key of ['latent_upscaler_enabled','second_pass_enabled'])elements['render_'+key].value='false';
  vm.runInContext('updateRenderDependencies()',context);
  assert.equal(elements.render_latent_upscaler_mpx.disabled,true);assert.equal(elements.render_second_steps.disabled,true);
  assert.equal(elements.render_second_pass_loras_strength_0.disabled,true);
  assert.equal(elements.render_latent_upscaler_mpx.value,'1.5');assert.equal(elements.render_second_steps.value,'7');
  assert.equal(elements.render_second_pass_loras_strength_0.value,'.75');assert.equal(elements.render_second_pass_loras_enable_0.checked,true);
  assert.equal(elements.render_field_second_steps.classes.has('disabled'),true);
  await vm.runInContext('generate()',context);const r=requests[0].render_settings;
  assert.equal(r.latent_upscaler_enabled,false);assert.equal(r.second_pass_enabled,false);
  assert.equal(r.latent_upscaler_mpx,1.5);assert.equal(r.second_steps,7);
  assert.deepEqual(r.second_pass_loras,[{id:registry[0].id,enabled:true,strength:.75}]);
 }else if(scenario==='extended_retry'){
  loseReply=true;
  elements.render_latent_upscaler_enabled.value='true';elements.render_second_pass_enabled.value='true';
  elements.render_latent_upscaler_mpx.value='1.6';elements.render_second_steps.value='9';
  elements.render_second_pass_loras_enable_0.checked=true;elements.render_second_pass_loras_strength_0.value='.8';
  await vm.runInContext('generate()',context);
  const changed=JSON.parse(JSON.stringify(workflow));changed.render_controls.latent_upscaler_mpx.default=1.9;
  changed.render_controls.second_pass_enabled.default=true;
  let next=load(changed);context=next.context;elements=next.elements;await vm.runInContext('init()',context);
  elements.model.value='10eros_full';vm.runInContext('profileChanged()',context);
  elements.render_latent_upscaler_enabled.value='false';elements.render_second_pass_enabled.value='false';
  await vm.runInContext('generate()',context);assert.deepEqual(requests[1],requests[0]);assert.equal(uploads,6);
  assert.deepEqual(requests[1].render_settings.second_pass_loras,[{id:registry[0].id,enabled:true,strength:.8}]);
  assert.equal(requests[1].render_settings.latent_upscaler_mpx,1.6);assert.equal(requests[1].render_settings.second_steps,9);
 }else if(scenario==='toggle_independence'){
  elements.render_latent_upscaler_enabled.value='true';vm.runInContext('updateRenderDependencies()',context);
  assert.equal(elements.render_second_pass_enabled.value,'false');assert.equal(elements.render_second_steps.disabled,true);
  elements.render_latent_upscaler_enabled.value='false';elements.render_second_pass_enabled.value='true';
  vm.runInContext('updateRenderDependencies()',context);assert.equal(elements.render_latent_upscaler_enabled.value,'false');
  assert.equal(elements.render_latent_upscaler_mpx.disabled,true);assert.equal(elements.render_second_steps.disabled,false);
 }else if(scenario==='extended_invalid'){
  for(const [key,value] of [['latent_upscaler_mpx','NaN'],['latent_upscaler_mpx','Infinity'],
      ['latent_upscaler_mpx','2.01'],['second_pass_enabled','1'],['latent_upscaler_enabled','0']]){
   elements['render_'+key].value=value;await vm.runInContext('generate()',context);assert.equal(requests.length,0);
   elements['render_'+key].value=String(workflow.render_controls[key].default);
  }
  elements.render_second_pass_loras_strength_0.value='NaN';await vm.runInContext('generate()',context);assert.equal(requests.length,0);
 }else if(scenario==='defaults'){
  await vm.runInContext('generate()',context);
  assert.deepEqual(requests[0].render_settings,{target_mpx:.4,first_steps:24,latent_upscaler_enabled:false,
   latent_upscaler_mpx:1.2,second_pass_enabled:false,second_steps:4,second_pass_loras:[]});
 }else if(scenario==='downscale'){
  elements.render_target_mpx.value='1.6';elements.render_latent_upscaler_mpx.value='1.2';elements.render_latent_upscaler_enabled.value='true';
  await vm.runInContext('generate()',context);assert.equal(requests.length,0);assert.equal(uploads,0);
  elements.render_latent_upscaler_enabled.value='false';await vm.runInContext('generate()',context);
  assert.equal(requests[0].render_settings.target_mpx,1.6);assert.equal(requests[0].render_settings.latent_upscaler_mpx,1.2);
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        registry = read_loras(ROOT / "h3/config/lora_registry.json")
        public = [{"id": key, **{field: entry[field] for field in ("display_name", "strength_range", "defaults", "compatibility")}}
                  for key, entry in registry.items()]
        result = subprocess.run(["node", "-e", script, str(ROOT / "h3/static/index.html"),
                                 json.dumps(get_adapter("h3_v20").render_capabilities()), json.dumps(public), scenario],
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

    def test_disabled_dependents_keep_and_submit_exact_mpx_steps_and_lora_values(self):
        self.run_scenario("dependencies")

    def test_retry_keeps_extended_settings_across_reload_profile_and_default_changes(self):
        self.run_scenario("extended_retry")

    def test_toggles_never_implicitly_enable_each_other(self):
        self.run_scenario("toggle_independence")

    def test_invalid_extended_settings_never_submit(self):
        self.run_scenario("extended_invalid")

    def test_new_job_defaults_are_exact_with_both_features_off_and_no_loras(self):
        self.run_scenario("defaults")

    def test_capability_cross_field_rule_rejects_downscale_only_while_enabled(self):
        self.run_scenario("downscale")
