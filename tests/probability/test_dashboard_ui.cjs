// Dependency-free regression checks for selected-model readiness labels.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = {};
const context = vm.createContext({document: {getElementById(id) {return elements[id] ||= {value: '', textContent: ''};}}, Date, Number, JSON, Math, Set});
const source = fs.readFileSync(path.join(__dirname, '../../src/btc_probability/static/app.js'), 'utf8');
vm.runInContext(source.slice(0, source.indexOf("$('market').addEventListener")), context);
vm.runInContext("draw=()=>{}; $('view').value='latest'; $('model').value='research';", context);
const forecast = {forecast_time:Date.now()/1000, mode:'live', readiness:'UNAVAILABLE', reasons:['ROUNDING_TIE_UNSPECIFIED'], research_settlement:{available:true,result:{p_yes:.6}}, discount:1, explanation:'Settlement estimate unavailable: ROUNDING_TIE_UNSPECIFIED', quotes:{},input_ages:{},volatility:{},known_samples:0,expected_samples:60,remaining_seconds:100};
function render(row) {context.row=row;vm.runInContext('rows=[row];render();',context);}
render(forecast);
assert.equal(elements.value.textContent,'60.00¢');
assert.match(elements.status.textContent,/^RESEARCH AVAILABLE/);
assert.match(elements.explanation.textContent,/Settlement estimate unavailable/);
vm.runInContext("$('model').value='settlement';",context);
render(forecast);
assert.match(elements.status.textContent,/^UNAVAILABLE.*ROUNDING_TIE_UNSPECIFIED/);
assert.equal(elements.value.textContent,'UNAVAILABLE');
vm.runInContext("$('model').value='research';",context);
render({...forecast,research_settlement:null,volatility:{reasons:['REALIZED_WARMUP_OR_GAP']}});
assert.match(elements.status.textContent,/RESEARCH UNAVAILABLE.*REALIZED_WARMUP_OR_GAP/);
render({...forecast,research_settlement:null,reasons:['OBSERVATION_END_UNVERIFIED','RULES_REVIEW_REQUIRED']});
assert.match(elements.status.textContent,/RESEARCH UNAVAILABLE.*OBSERVATION_END_UNVERIFIED/);
console.log('Dashboard selected-model readiness: 4 cases passed');
