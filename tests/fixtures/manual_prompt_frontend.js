const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8'),config=JSON.parse(process.argv[3]),scenario=process.argv[4];
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\ninit\(\);\s*$/,'').replace(/\nrefreshVast\(\);\s*$/,'');
const stored=new Map(),requests=[],alerts=[];
const exact='  <Picture 1> calm movement. żółć 🎬\r\n<script>alert(1)</script> {$value|other} $(never_execute)  \n';
let uploads=0,loseReply=['retry','pending_switch'].includes(scenario),uploadGate,releaseUpload,preview;
function load(){
  const elements={model:{value:'h3_full'},promptMode:{value:'autoprompt',innerHTML:''},
    pic:{files:['anchor']},refs:{files:[1,2,3,4,5]},audio:{files:['audio']},
    prompt:{value:'Original scene'},manualFinalPrompt:{value:exact},duration:{value:'20'},batches:{value:'3'},timeout:{value:'8'},
    generateBatch:{},newBatch:{},scenePromptField:{},manualPromptField:{},promptModeHelp:{},batchHelp:{},
    prefetchTarget:{},terminalSection:{},advancedRenderSettings:{},selectedStatus:{},promptSource:{},
    jobs:{innerHTML:''},reviewPool:{innerHTML:''}};
  for(let i=0;i<5;i++){
    elements['p'+i]={textContent:'',set innerHTML(value){throw Error('Prompt text must never be assigned as HTML: '+value)}};
    elements['p'+i+'Label']={textContent:''};
  }
  function container(){let markup='';return {get innerHTML(){return markup},set innerHTML(value){
    markup=value;
    for(const match of value.matchAll(/<input\b[^>]*id="([^"]+)"[^>]*>/g))elements[match[1]]={value:match[0].match(/value="([^"]+)"/)?.[1]??'',checked:/\bchecked\b/.test(match[0])};
    for(const match of value.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g))elements[match[1]]={value:match[2].match(/<option value="([^"]+)" selected/)?.[1]??'false'};
    for(const match of value.matchAll(/<div id="(render_field_[^"]+)"/g))elements[match[1]]={classList:{toggle(){}}};
  }}}
  elements.qualityControls=container();elements.advancedRenderControls=container();elements.loras=container();
  const context=vm.createContext({document:{getElementById:id=>elements[id]},
    localStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
    crypto:{randomUUID:()=>require('node:crypto').randomUUID()},FormData:class{append(){}},
    alert:text=>alerts.push(String(text)),setInterval(){},
    fetch:async(url,options)=>{
      if(url==='/api/config')return {ok:true,json:async()=>config};
      if(url==='/api/upload'){
        uploads++;
        if(uploadGate){const gate=uploadGate;uploadGate=null;await gate}
        return {ok:true,json:async()=>({filename:'asset'+uploads})};
      }
      if(url.endsWith('/prompts'))return {ok:true,json:async()=>preview};
      assert.equal(url,'/api/batches');requests.push(JSON.parse(options.body));
      if(loseReply){loseReply=false;throw Error('accepted, reply lost')}
      return {ok:true,json:async()=>({created_batches:['durable-batch']})};
    }});
  vm.runInContext(source,context);
  vm.runInContext('refresh=async()=>{};terminalButtonState=()=>{}',context);
  return {context,elements};
}
function manual(context,elements){elements.promptMode.value='manual_final';vm.runInContext('promptModeChanged()',context)}
(async()=>{
  let {context,elements}=load();await vm.runInContext('init()',context);
  if(scenario==='selector'){
    assert.match(html,/Prompt mode/);assert.match(html,/Final H3 prompt/);
    assert.match(elements.promptMode.innerHTML,/Autoprompt/);assert.match(elements.promptMode.innerHTML,/Own final H3 prompt/);
    assert.equal(elements.promptMode.value,'autoprompt');assert.equal(elements.scenePromptField.hidden,false);assert.equal(elements.manualPromptField.hidden,true);
    assert.match(elements.batchHelp.textContent,/10 prompt candidates: 5 auto \+ 5 review/);
    manual(context,elements);
    assert.equal(elements.scenePromptField.hidden,true);assert.equal(elements.manualPromptField.hidden,false);
    assert.match(elements.batchHelp.textContent,/one render/);assert.match(elements.promptModeHelp.textContent,/No prompt generation or rewriting/);
    elements.promptMode.value='autoprompt';vm.runInContext('promptModeChanged()',context);
    assert.equal(elements.manualFinalPrompt.value,exact);assert.equal(elements.prompt.value,'Original scene');
  }else if(scenario==='autoprompt'){
    await vm.runInContext('generate()',context);const body=requests[0];
    assert.equal(body.prompt,'Original scene');assert.equal(body.batches,3);
    assert.equal('manual_final_prompt' in body,false);assert.equal('prompt_mode' in body,false);
    assert.deepEqual(Object.keys(body).sort(),['request_id','prompt','profile','duration_seconds','render_settings','batches','soft_timeout_minutes','pictures','audio','loras'].sort());
  }else if(['manual_body','retry','pending_switch'].includes(scenario)){
    manual(context,elements);
    elements.render_latent_upscaler_enabled.value='true';elements.render_second_pass_enabled.value='true';
    elements.render_first_steps.value='32';elements.render_second_steps.value='9';elements.render_target_mpx.value='.6';
    elements.render_second_pass_loras_enable_0.checked=true;elements.render_second_pass_loras_strength_0.value='.75';
    await vm.runInContext('generate()',context);const body=requests[0];
    assert.equal(body.prompt_mode,'manual_final');assert.equal(body.manual_final_prompt,exact);assert.equal('prompt' in body,false);
    assert.equal(body.batches,3);assert.equal(body.pictures.length,6);assert.equal(body.audio.length,1);
    assert.equal(body.render_settings.latent_upscaler_enabled,true);assert.equal(body.render_settings.latent_upscaler_mpx,1.2);
    assert.equal(body.render_settings.second_pass_enabled,true);assert.equal(body.render_settings.second_steps,9);
    assert.equal(body.render_settings.second_pass_loras[0].strength,.75);
    assert.doesNotMatch(JSON.stringify(body),/node_id|widget_path|graph_path/);
    if(scenario!=='manual_body'){
      assert.deepEqual(JSON.parse(stored.get('h3.pendingBatch')),body);
      ({context,elements}=load());await vm.runInContext('init()',context);
      elements.manualFinalPrompt.value='Changed final prompt';elements.prompt.value='Changed scene';elements.model.value='10eros_full';
      elements.duration.value='10';elements.batches.value='1';elements.render_first_steps.value='50';
      if(scenario==='pending_switch'){elements.promptMode.value='autoprompt';vm.runInContext('promptModeChanged()',context)}
      else manual(context,elements);
      await vm.runInContext('generate()',context);
      assert.deepEqual(requests[1],body);assert.equal(uploads,7);assert.equal(stored.size,0);
    }
  }else if(scenario==='during_upload'){
    manual(context,elements);uploadGate=new Promise(resolve=>{releaseUpload=resolve});
    const pending=vm.runInContext('generate()',context);
    await Promise.resolve();elements.manualFinalPrompt.value='Edited during uploads';elements.promptMode.value='autoprompt';
    elements.model.value='10eros_full';elements.duration.value='10';elements.batches.value='1';
    releaseUpload();await pending;
    assert.equal(requests[0].manual_final_prompt,exact);assert.equal(requests[0].prompt_mode,'manual_final');
    assert.equal(requests[0].profile,'h3_full');assert.equal(requests[0].duration_seconds,20);assert.equal(requests[0].batches,3);
  }else if(scenario==='empty'||scenario==='unknown'){
    manual(context,elements);
    if(scenario==='empty')elements.manualFinalPrompt.value=' \r\n\t';else elements.promptMode.value='unsupported';
    await vm.runInContext('generate()',context);assert.equal(requests.length,0);assert.equal(uploads,0);
    assert.match(alerts.join(' '),scenario==='empty'?/non-empty Final H3 prompt/:/Unknown prompt mode/);
  }else if(scenario==='preview'){
    preview={prompt_mode:'manual_final',manual_final_prompt:exact,final_h3_prompt:exact,status:'render_queued_auto',batch_seq:1,candidate_index:1,
      visual_facts:'must be suppressed',expanded_intent:'must be suppressed',reference_map:'must be suppressed',creative_plan:'must be suppressed'};
    vm.runInContext("selected='manual-job'",context);await vm.runInContext('refreshPrompts()',context);
    for(let i=0;i<4;i++){assert.match(elements['p'+i+'Label'].textContent,/skipped \(manual final prompt\)/);assert.match(elements['p'+i].textContent,/Skipped/)}
    assert.equal(elements.p4.textContent,exact);assert.equal(elements.p4Label.textContent,'Final H3 prompt');assert.match(elements.promptSource.textContent,/Own final H3 prompt/);
    preview={prompt_mode:'autoprompt',status:'pending_review',batch_seq:1,candidate_index:6,visual_facts:'Facts',creative_plan:'Real plan',final_h3_prompt:'Generated final'};
    await vm.runInContext('refreshPrompts()',context);assert.equal(elements.p0.textContent,'Facts');assert.equal(elements.p3.textContent,'Real plan');
    assert.equal(elements.p4Label.textContent,'Step 4 — Final H3 prompt');assert.match(elements.promptSource.textContent,/Autoprompt/);
  }else if(scenario==='review'||scenario==='escaping'){
    const jobs=[{id:'manual',prompt_mode:'manual_final',status:'render_queued_auto',batch_seq:1,candidate_index:1,render_seed:101,final_h3_prompt:exact,review_required:false}];
    if(scenario==='review')jobs[0].status='pending_review';
    else{jobs[0].current_node=exact;jobs.push({id:'review',prompt_mode:'autoprompt',status:'pending_review',batch_seq:1,candidate_index:6,final_h3_prompt:exact,review_required:true})}
    vm.runInContext('lastJobs='+JSON.stringify(jobs),context);vm.runInContext('renderJobs();renderReview()',context);
    assert.doesNotMatch(elements.jobs.innerHTML,/<script>/);assert.doesNotMatch(elements.reviewPool.innerHTML,/<script>/);
    if(scenario==='review'){assert.equal(elements.reviewPool.innerHTML,'—');assert.doesNotMatch(elements.jobs.innerHTML,/APPROVE|REVIEW/)}
    else{assert.match(elements.reviewPool.innerHTML,/&lt;script&gt;/);assert.match(elements.jobs.innerHTML,/Own final H3 prompt/)}
  }
})().catch(error=>{console.error(error);process.exitCode=1});
