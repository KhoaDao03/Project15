"""Exercise the browser's price handler without starting a collector or browser server."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_reference_clock_lead_does_not_flash_and_stale_data_still_clears():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the dashboard price renderer")
    source = Path("src/btc15/static/app.js").read_text()
    start = source.index("const marketStream=")
    handler = source[start : source.index("\nrefreshOfficial();", start)]
    setup = """
const assert=require('node:assert/strict');
const nodes=new Map();
const $=id=>{
  if(!nodes.has(id)){
    let value='';
    nodes.set(id,{writes:0,value:'PAPER',get textContent(){return value;},set textContent(v){this.writes++;value=v;}});
  }
  return nodes.get(id);
};
const money=v=>'$'+Number(v).toFixed(2), fmt=v=>String(v);
let liveReference=null,livePrices=false,lastMarketEvent=0,marketClockOffset=0,now=1000,watchdog;
const performance={now:()=>now};
const setInterval=fn=>watchdog=fn;
class EventSource {}
const updateLiveReference=v=>liveReference=v;
const clearLiveQuotes=()=>{liveReference=null;};
const renderQuotes=()=>{},renderMarkets=()=>{};
const fresh=()=>({server_time:100,fresh:false,snapshot:null,reference:{connected:true,published_at:99.99,reference_5hz:{received:99.98,source_ts_ms:100650,value:'79200'}}});
const send=data=>marketStream.onmessage({data:JSON.stringify(data)});
"""
    checks = """
// Recorded failure: each new sample leads local time by ~650 ms, then ages
// through the former 500 ms cutoff between stream updates.
for(const [stamp,source] of [[100,100.65],[100.2,100.65],[100.21,100.85],[100.4,100.85]]){
  const data=fresh();data.server_time=stamp;data.reference.published_at=stamp-.01;
  data.reference.reference_5hz.received=stamp-.02;data.reference.reference_5hz.source_ts_ms=source*1000;
  send(data);
  assert.equal($('live-reference').textContent,'$79200.00');
  assert.equal(liveReference,79200);
  assert.equal($('reference-status').textContent,'Live · 5 Hz reference');
}
assert.equal($('live-reference').writes,1,'Identical prices should not be redrawn for every quote');
const changed=fresh();changed.reference.reference_5hz.value='79201';send(changed);
assert.equal($('live-reference').textContent,'$79201.00');

// A fresh display does not authorize entries or require a healthy strategy worker.
const recovering=fresh();recovering.reference.recovery={entries_blocked:true,state:'DRAINING',reasons:['PROCESSING_OVERLOAD']};
send(recovering);
assert.equal($('live-reference').textContent,'$79200.00');
assert.match($('official-status').textContent,/Entries blocked/);

for(const invalidate of [
  d=>d.reference.connected=false,
  d=>d.reference.published_at=98,
  d=>d.reference.reference_5hz.received=98,
  d=>d.reference.reference_5hz.received=100.1,
  d=>d.reference.reference_5hz.source_ts_ms=97999,
  d=>d.reference.reference_5hz.source_ts_ms=102001,
  d=>d.reference.reference_5hz.source_ts_ms=null,
  d=>d.reference.reference_5hz.value='invalid',
  d=>d.reference.reference_5hz=null,
]){
  send(fresh());const data=fresh();invalidate(data);send(data);
  assert.equal($('live-reference').textContent,'—');
  assert.equal(liveReference,null);
}
send(fresh());marketStream.onerror();
assert.equal($('live-reference').textContent,'—');
send(fresh());now+=2501;watchdog();
assert.equal($('live-reference').textContent,'—');
assert.equal(liveReference,null);
"""
    result = subprocess.run([node, "-e", setup + handler + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
