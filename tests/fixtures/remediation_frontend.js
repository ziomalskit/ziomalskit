const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const config = JSON.parse(process.argv[3]);
const source = fs.readFileSync(process.argv[2], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/\ninit\(\);\s*$/, '').replace(/\nrefreshVast\(\);\s*$/, '');
const elements = {model: {value: 'h3_full'}, promptMode: {value: 'autoprompt'},
  prompt: {value: 'CPU scene'}, manualFinalPrompt: {value: 'Continuous movement.'},
  pic: {files: ['anchor']}, refs: {files: [1, 2, 3, 4, 5]}, audio: {files: []},
  duration: {value: '20'}, batches: {value: '1'}, timeout: {value: '8'},
  generateBatch: {}, newBatch: {}, prefetchTarget: {}, terminalSection: {}, advancedRenderSettings: {},
  scenePromptField: {}, manualPromptField: {}, promptModeHelp: {}, batchHelp: {}};
function container() {
  return {set innerHTML(value) {
    for (const match of value.matchAll(/<input\b[^>]*id="([^"]+)"[^>]*>/g))
      elements[match[1]] = {value: match[0].match(/value="([^"]+)"/)?.[1] ?? '', checked: /\bchecked\b/.test(match[0])};
    for (const match of value.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g))
      elements[match[1]] = {value: match[2].match(/<option value="([^"]+)" selected/)?.[1] ?? 'false'};
    for (const match of value.matchAll(/<div id="(render_field_[^"]+)"/g))
      elements[match[1]] = {classList: {toggle() {}}};
  }};
}
for (const key of ['qualityControls', 'advancedRenderControls', 'loras']) elements[key] = container();
const requests = [], alerts = [], stored = new Map();
const context = vm.createContext({document: {getElementById: id => elements[id]},
  localStorage: {getItem: k => stored.get(k) || null, setItem: (k,v) => stored.set(k,v), removeItem: k => stored.delete(k)},
  crypto: require('node:crypto'), FormData: class {append() {}}, setInterval() {}, alert: m => alerts.push(String(m)),
  fetch: async (url, options) => {
    if (url === '/api/config') return {ok: true, json: async () => config};
    if (url === '/api/upload') return {ok: true, json: async () => ({filename: 'synthetic.png'})};
    assert.equal(url, '/api/batches'); requests.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({created_batches: ['accepted']})};
  }});
vm.runInContext(source, context);
vm.runInContext('refresh=async()=>{};terminalButtonState=()=>{}', context);
function settings() {return JSON.parse(JSON.stringify(vm.runInContext('submittedRenderSettings()', context)));}
(async () => {
  await vm.runInContext('init()', context);
  assert.equal(elements.render_second_pass_enabled.value, 'false');
  assert.equal(elements.render_second_pass_loras_enable_0.checked, false);
  const initial = Number(elements.render_second_pass_loras_strength_0.value);
  assert(initial >= -.5 && initial <= .5, 'untouched unused row must initialize within its own bounds');
  assert.deepEqual(settings().second_pass_loras, []);
  await vm.runInContext('generate()', context);
  assert.equal(requests.length, 1, alerts.join('\n'));
  assert.deepEqual(requests[0].render_settings.second_pass_loras, []);
  elements.render_second_pass_enabled.value = 'true';
  elements.render_second_pass_loras_enable_0.checked = true;
  elements.render_second_pass_loras_strength_0.value = '.25';
  assert.equal(settings().second_pass_loras[0].strength, .25);
  elements.render_second_pass_loras_strength_0.value = '1';
  assert.throws(settings, /strength/);
  elements.render_second_pass_loras_strength_0.value = '.25';
  elements.render_second_pass_loras_enable_0.checked = false;
  elements.render_second_pass_enabled.value = 'false';
  assert.deepEqual(settings().second_pass_loras, [{id: config.loras[0].id, enabled: false, strength: .25}]);
  elements.render_second_pass_loras_strength_0.value = '1';
  assert.throws(settings, /strength/, 'explicitly edited inactive settings still require validation');
  elements.model.value = '10eros_full'; vm.runInContext('profileChanged()', context);
  assert(Number(elements.render_second_pass_loras_strength_0.value) <= .5);
  assert.deepEqual(settings().second_pass_loras, []);
  vm.runInContext(`renderControls.second_pass_loras.default=[{id:loras[0].id,enabled:false,strength:.1}];renderSettingsForm()`, context);
  assert.equal(Number(elements.render_second_pass_loras_strength_0.value), .1, 'valid saved strength takes precedence');
  assert.equal(settings().second_pass_loras[0].strength, .1, 'explicit inactive saved row is retained');
  vm.runInContext(`renderControls.second_pass_loras.default=[{id:loras[0].id,enabled:false,strength:1}];renderSettingsForm()`, context);
  assert.equal(Number(elements.render_second_pass_loras_strength_0.value), .5, 'invalid saved value falls back to trusted profile');
  vm.runInContext(`renderControls.second_pass_loras.default=[];delete loras[0].defaults['10eros_full'].strength;renderSettingsForm()`, context);
  assert.equal(Number(elements.render_second_pass_loras_strength_0.value), .5, 'control default is clamped when no profile strength is available');
  assert.deepEqual(settings().second_pass_loras, []);
  elements.jobs = {};
  vm.runInContext(`lastJobs=[{id:'quarantined',status:'reconciliation_required',prompt_mode:null}];renderJobs()`, context);
  assert.match(elements.jobs.innerHTML, /Prompt source unavailable/);
  assert.doesNotMatch(elements.jobs.innerHTML, /candidate #| · auto /);
})().catch(error => {console.error(error); process.exitCode = 1;});
