"""Run profile/default selection through the actual browser JavaScript."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionFrontendTests(unittest.TestCase):
    def test_primary_selector_contains_only_two_production_labels(self):
        source = (ROOT / "h3/static/index.html").read_text()
        selector = re.search(r'<select id="model"[^>]*>(.*?)</select>', source, re.S)[1]
        self.assertEqual(re.findall(r'<option value="([^"]+)">([^<]+)</option>', selector),
                         [("h3_full", "H3 Full"), ("10eros_full", "10Eros Full")])
        self.assertNotIn(".safetensors", source)
        self.assertNotIn("native_int8", source)

    def test_profile_defaults_strength_edit_and_retry_preserve_original_canonical_ids(self):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
 .replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const elements={model:{value:'h3_full'},pic:{files:['anchor']},refs:{files:[1,2,3,4,5]},audio:{files:[]},
 prompt:{value:'walk for twenty seconds'},duration:{value:'20'},batches:{value:'1'},timeout:{value:'8'},
 generateBatch:{},newBatch:{},le0:{checked:true},ls0:{value:1}};
let markup='';elements.loras={get innerHTML(){return markup},set innerHTML(value){markup=value;
 elements.le0.checked=/<input[^>]*id="le0"[^>]*checked/.test(value);
 elements.ls0.value=/<input[^>]*id="ls0"[^>]*value="([^"]+)"/.exec(value)[1];}};
const stored=new Map(),requests=[];let loseReply=true,uploads=0;
const context=vm.createContext({document:{getElementById:id=>elements[id]},
 localStorage:{getItem:key=>stored.get(key)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
 crypto:{randomUUID:()=>require('node:crypto').randomUUID()},FormData:class{append(){}},alert:()=>{},setInterval:()=>{},
 fetch:async(url,options)=>{if(url==='/api/upload')return{ok:true,json:async()=>({filename:'ref'+(++uploads)+'.png'})};
  requests.push(JSON.parse(options.body));if(loseReply){loseReply=false;throw Error('synthetic lost acknowledgement')}
  return{ok:true,json:async()=>({created_batches:['durable-batch']})};}});
vm.runInContext(source,context);vm.runInContext("refresh=async()=>{};loras=[{id:'movement-v1',filename:'never-submit-this.safetensors',display_name:'Movement <safe>',help:'pending',strength_range:[-2,2],defaults:{h3_full:{enabled:true,strength:.5},'10eros_full':{enabled:false,strength:.15}}}]",context);
(async()=>{
 vm.runInContext('profileChanged()',context);assert.equal(elements.le0.checked,true);assert.equal(+elements.ls0.value,.5);
 assert.match(markup,/Movement &lt;safe&gt;/);assert.doesNotMatch(markup,/never-submit-this/);
 assert.match(markup,/min="-2" max="2"/);
 elements.model.value='10eros_full';vm.runInContext('profileChanged()',context);
 assert.equal(elements.le0.checked,false);assert.equal(+elements.ls0.value,.15);
 elements.le0.checked=true;elements.ls0.value=.35;
 await vm.runInContext('generate()',context);
 assert.equal(requests[0].profile,'10eros_full');assert.equal(requests[0].duration_seconds,20);
 assert.deepEqual(requests[0].loras,[{id:'movement-v1',enabled:true,strength:.35}]);
 elements.model.value='h3_full';vm.runInContext('profileChanged()',context);
 await vm.runInContext('generate()',context);
 assert.deepEqual(requests[1],requests[0]);assert.equal(uploads,6);assert.equal(stored.size,0);
 assert.doesNotMatch(JSON.stringify(requests),/safetensors|filename|never-submit/);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(["node", "-e", script, str(ROOT / "h3/static/index.html")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
