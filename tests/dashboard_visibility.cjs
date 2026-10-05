// Run with Node; executes the actual polling/stream lifecycle code with browser stubs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/btc15/static/app.js', 'utf8');
const manual = fs.readFileSync('src/btc15/static/manual.js', 'utf8');
const nodes = new Map();
const listeners = {};
let requests = 0, refreshes = 0, opened = 0, closed = 0, balanceReads = 0;
const timers = [];
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {open: false, textContent: '', addEventListener() {}});
  return nodes.get(id);
};
const context = vm.createContext({
  document: {hidden: false, addEventListener(name, fn) {listeners[name] = fn;}, getElementById: node},
  window: {addEventListener() {}},
  $: node, fleetMode: true, active: 'monitor', shuttingDown: false,
  livePrices: false, apiPath: p => p,
  clearLiveQuotes() {}, updateLiveReference() {},
  renderBotVersion() {}, async get() {requests++;}, async runs() {requests++;},
  async refresh() {refreshes++;}, refreshFleet() {},
  setTimeout(fn, delay) {timers.push({fn, delay}); return timers.length;}, clearTimeout() {}, Date,
  EventSource: class {constructor() {opened++;} close() {closed++;}},
});
const run = code => vm.runInContext(code, context);

(async () => {
  run(source.slice(source.indexOf('function detailsVisible()'), source.indexOf('\npoll();')));
  await run('poll()');
  assert.equal(requests + refreshes, 0, 'collapsed detail view makes no detail requests');
  node('asset-details').open = true;
  await run('poll()');
  assert.equal(requests, 1);
  assert.equal(refreshes, 1);
  context.document.hidden = true;
  await run('poll()');
  assert.equal(refreshes, 1, 'hidden tab does not poll');

  run(source.slice(source.indexOf('let marketStream=null;'), source.indexOf('setInterval(()=>{if((livePrices')));
  assert.equal(opened, 0, 'hidden tab does not connect');
  context.document.hidden = false;
  run('syncMarketStream();syncMarketStream()');
  assert.equal(opened, 1, 'only one stream per visible detail view');
  context.document.hidden = true;
  run('syncMarketStream()');
  assert.equal(closed, 1);
  assert.equal(node('live-reference').textContent, '—');
  context.document.hidden = false;
  run('syncMarketStream()');
  assert.equal(opened, 2, 'returning to the view reconnects');
  node('asset-details').open = false;
  run('syncMarketStream()');
  assert.equal(closed, 2, 'collapsing details disconnects');
  let operational;
  context.renderOperational = value => {operational = value;};
  context.selected = 'BTC';
  context.data = {assets: [{asset: 'BTC', run_id: 'btc-live', operational: {state: 'READY', summary: 'Fresh'}}]};
  run(source.slice(source.indexOf('    // The collapsed detail poll'), source.indexOf("    $('asset-details-label').textContent=")));
  assert.equal(operational.state, 'READY');
  assert.equal(node('bot-version').textContent, 'Ready · btc-live', 'sidebar uses fresh fleet health while details are paused');

  context.fleetMode = false;
  context.api = async () => {balanceReads++; return {available_cash_dollars: 20, server_time: 1};};
  context.amount = String;
  run(manual.slice(manual.indexOf('  let balanceBusy='), manual.indexOf('  function quoteText()')));
  context.fleetMode = true;
  await run('refreshRealBalance()');
  assert.equal(balanceReads, 1);
  context.document.hidden = true;
  listeners.visibilitychange();
  await run('refreshRealBalance()');
  assert.equal(balanceReads, 1, 'hidden balance requests are suppressed');
  assert.equal(node('real-balance').textContent, '—');
  context.document.hidden = false;
  listeners.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(balanceReads, 2, 'balance refreshes immediately on return');

  let finishBalance;
  context.api = () => new Promise(resolve => {finishBalance = resolve;});
  const pending = run('refreshRealBalance()');
  context.document.hidden = true;
  listeners.visibilitychange();
  context.document.hidden = false;
  listeners.visibilitychange();
  finishBalance({available_cash_dollars: 999, server_time: 1});
  await pending;
  assert.equal(node('real-balance').textContent, '—', 'pre-hide response cannot restore stale balance');
  assert.equal(timers.at(-1).delay, 0, 'refresh missed during in-flight request is retried immediately');
  console.log('Dashboard visibility and stream lifecycle checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
