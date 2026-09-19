'use strict';
const $=id=>document.getElementById(id);
const assetBase=window.location.pathname.match(/^\/assets\/(BTC|ETH|SOL|XRP|GOLD|SILVER|WTI)(?:\/|$)/)?.[0].replace(/\/$/,'')||'';
let fleetMode=false;
const apiPath=path=>fleetMode&&path.startsWith('/api/shutdown')?path:assetBase+path;
const fmt=(v,d=2)=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});
const describeReason=r=>{const labels={revalidate_entry_signal:'Recheck signal after order submission',VENUE_PAUSED:'Market trading paused; awaiting fresh active venue confirmation',PROCESSING_LAG:'Processing is behind the live feed; execution blocked',STRATEGY_DISABLED:'Strategy entries are disabled',ENTRY_WINDOW:'Outside the configured entry window',EXISTING_ENTRY:'A position, pending order, exhausted retries or re-entry cooldown blocks another entry',POST_CLOSE_COOLDOWN:'Waiting after a completed trade before opening another position',STALE_REFERENCE:'Official reference data is stale',STALE_BOOK:'Order book is stale or invalid',BLEEP_MIN_PROBABILITY:'Bleep probability for the selected side is below the required threshold',MIN_EV:'Expected value is below the minimum',MIN_EDGE:'Net edge is below the minimum',UNVERIFIED_FEES:'Fee metadata has not been verified',MODEL_UNAVAILABLE:'Settlement model unavailable',RISK_LIMIT:'Risk budget exhausted',BOLLINGER_EXTENSION:'Entry reference is outside the Bollinger band for this side'};return (r.message||labels[r.code]||r.code.replaceAll('_',' '))+(r.actual===null||r.actual===undefined?'':typeof r.actual==='number'?' · observed '+fmt(r.actual,3):' · '+String(r.actual));};
const pct=v=>v===null||v===undefined?'—':fmt(v*100,1)+'%';
const money=v=>v===null||v===undefined?'—':'$'+fmt(v);
const text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
let referenceDigits=2;
const referenceMoney=(v,d=referenceDigits)=>v==null?'—':'$'+fmt(v,d);
let shuttingDown=false;
let active='monitor', offset=0, generation=0, liveReference=null;
let runSelectionExplicit=false;
async function get(path){const r=await fetch(apiPath(path),{cache:'no-store'});if(!r.ok)throw Error('Request failed: '+r.status);return r.json();}
function query(){return new URLSearchParams({mode:$('mode').value,scope:$('history-scope').value,...($('run').value?{run_id:$('run').value}:{})});}
function tiles(id,values){$(id).replaceChildren(...values.map(([label,value,note])=>{const e=text('div','','tile');e.append(text('div',label,'label'),text('strong',value));if(note)e.append(text('small',note));return e;}));}
function metrics(id,values){$(id).replaceChildren(...values.map(([k,v])=>{const e=text('div','','metric');e.append(text('span',k),text('span',v));return e;}));}
function renderEntryEconomics(id,r){
  const node=$(id);node.replaceChildren();
  if(r?.status!=='AVAILABLE'){node.append(text('p',r?.reason||'Not recorded for this evaluation.','muted'));return;}
  const dollars=v=>v==null?'—':'$'+fmt(v,4);
  const rows=[
    ['Settlement EV',r.settlement.ev_total,r.settlement.ev_per_contract,0],
    ['Target-sale proxy',r.target_sale.probability_weighted_proxy_total,r.target_sale.probability_weighted_proxy_per_contract,r.target_sale.exit_fee],
    ['If sold at target',r.target_sale.net_total,r.target_sale.net_per_contract,r.target_sale.exit_fee],
    ['If sold at stop price',r.stop_exit.net_total,r.stop_exit.net_per_contract,r.stop_exit.exit_fee],
    ['Immediate unwind · '+r.immediate_unwind.status,r.immediate_unwind.net_total,r.immediate_unwind.net_total==null?null:r.immediate_unwind.net_total/r.quantity,r.immediate_unwind.exit_fee]
  ];
  node.append(text('p',fmt(r.quantity)+' contracts at '+dollars(r.entry_price)+' · entry fee '+dollars(r.entry_fee)+' · entry allowance '+dollars(r.entry_slippage_total)+' total.'));
  const table=document.createElement('table'),head=document.createElement('tr');
  for(const label of ['Scenario','Net total','Net / contract','Exit fee'])head.append(text('th',label));
  table.append(head);
  for(const [label,total,unit,fee] of rows){const tr=document.createElement('tr');tr.append(text('td',label),text('td',dollars(total)),text('td',dollars(unit)),text('td',dollars(fee)));table.append(tr);}
  node.append(table,text('p','Reporting only; entry filters are unchanged. Target '+dollars(r.target_sale.assumed_sale_price)+'; stop scenario '+dollars(r.stop_exit.assumed_sale_price)+'. The target proxy assumes settlement winners sell at target and losers pay zero; it does not forecast dynamic exits. The stop scenario is not a loss cap. Immediate unwind uses current bid depth. Fees assume taker execution; no extra exit haircut.','muted'));
}
function tab(name){document.body.classList.toggle('overview-active',name==='monitor');if((name==='monitor'||name==='strategies')&&$('history-scope').value!=='settlement'){$('history-scope').value='settlement';runSelectionExplicit=false;$('run').value='';runs();}if(name==='trades')offset=0;active=name;document.querySelectorAll('.tab').forEach(e=>e.hidden=e.id!==name);$('replay').hidden=true;document.querySelectorAll('nav button').forEach(e=>e.classList.toggle('selected',e.dataset.tab===name));$('page-title').textContent={monitor:'Live overview',trades:'Trade history',analytics:'Results & accuracy',strategies:'Settings'}[name];refresh();}
document.querySelectorAll('nav button').forEach(e=>e.onclick=()=>tab(e.dataset.tab));
let lastOperationalReceipt=0;
function renderOperational(o){
  lastOperationalReceipt=performance.now();
  $('operational-panel').dataset.state=o.state;
  $('operational-state').textContent=o.state;
  $('operational-summary').textContent=o.summary;
  $('operational-run').textContent='Current collector: '+(o.run_id||'unavailable')+' · independent of the history selection';
  const rows=(id,values)=>$(id).replaceChildren(...values.map(r=>text('div',r.message+(r.market?' · '+r.market:''),'reason')));
  rows('operational-reasons',o.reasons);rows('operational-warnings',o.warnings);
  metrics('operational-metrics',[
    ['Feed',o.connected?'Connected':'Not confirmed connected'],
    ['Collector status age',o.status_age==null?'—':fmt(o.status_age,1)+' s'],
    ['Processing delay',o.processing_lag==null?'—':fmt(o.processing_lag*1000,0)+' ms'],
    ['Queue',o.queue_depth==null?'—':fmt(o.queue_depth,0)+' / '+fmt(o.queue_capacity,0)],
    ['Reference age',o.reference_age==null?'—':fmt(o.reference_age,1)+' s'],
    ['Recovery',o.recovery_phase||'No recovery status']
  ]);
  const labels={UNKNOWN:'Entry readiness unknown',BLOCKED:'Entries blocked',CHECKING:'Entry readiness being checked',WAITING:'Latest evaluation: waiting for entry conditions',CANDIDATE:'Latest evaluation passed entry filters; execution checks still apply'};
  $('operational-entry').textContent=(labels[o.entry_status]||o.entry_status)+(o.evaluation_market?' · '+o.evaluation_market:'');
  rows('operational-entry-reasons',o.entry_reasons);
}
setInterval(()=>{
  if(lastOperationalReceipt&&performance.now()-lastOperationalReceipt>6000){
    $('operational-panel').dataset.state='BLOCKED';
    $('operational-state').textContent='UNKNOWN';
    $('operational-summary').textContent='Dashboard updates stopped; current bot state cannot be confirmed.';
    $('operational-entry').textContent='Entry readiness unknown';
    $('operational-reasons').replaceChildren();$('operational-entry-reasons').replaceChildren();
    $('operational-metrics').replaceChildren();$('operational-warnings').replaceChildren();
    $('bot-version').textContent='Status unavailable';
  }
},1000);
function renderBotVersion(health){
  const tag=$('bot-version'),collector=health.collector;
  const member=collector?.models?.find(model=>model.run_id===collector.run_id);
  tag.textContent=collector?.run_id?(health.collector_fresh?'Running · ':'Last seen · ')+collector.run_id:'No active bot';
  const operational=health.operational;
  if(operational){
    tag.textContent=operational.state.toLowerCase().replace(/^./,c=>c.toUpperCase())+' · '+(operational.run_id||'No current run');
    renderOperational(operational);
  }
  tag.title=collector?.run_id?[
    'Run: '+collector.run_id,
    'Mode: '+(liveOnlyFleet?'Live':collector.mode),
    member?.model?.model_version?'Model: '+member.model.model_version:null,
    member?.model?.config_hash?'Configuration: '+member.model.config_hash:null,
    health.collector_fresh?'Collector status is current.':'Collector status is stale; this version is not confirmed running.'
  ].filter(Boolean).join('\n'):'No collector status available.';
}
async function runs(){
  const mode=$('mode').value,scope=$('history-scope').value;
  const [rows,health]=await Promise.all([get('/api/runs?mode='+mode+'&scope='+scope),get('/api/health')]);
  referenceDigits=health.reference_digits||2;$('reference-label').textContent=health.asset+' · '+health.reference_index;renderBotVersion(health);
  if(mode!==$('mode').value||scope!==$('history-scope').value)return;
  const selected=$('run').value,selectedLabel=$('run').selectedOptions[0]?.textContent;
  const current=scope==='settlement'&&health.collector?.mode===mode?health.collector.run_id:null;
  $('run').replaceChildren(new Option('All runs (combined history)',''),...rows.map(r=>new Option((r.run_id===current?'Current · ':'')+(r.body.model?.model_name||'BTC15 Settlement Edge')+' · '+r.run_id+' · '+new Date(r.timestamp*1000).toLocaleString(),r.run_id)));
  if(runSelectionExplicit){
    if(selected&&!rows.some(r=>r.run_id===selected))$('run').add(new Option(selectedLabel,selected));
    $('run').value=selected;
  }else if(scope==='settlement'){
    if(current&&!rows.some(r=>r.run_id===current))$('run').add(new Option('Current · '+current,current));
    $('run').value=current||rows[0]?.run_id||'';
  }
  if(selected!==$('run').value){generation++;offset=0;if(active==='trades')refresh();}
}

$('mode').onchange=async()=>{runSelectionExplicit=false;generation++;$('run').value='';refreshOfficial();await runs();offset=0;if(active==='replay')tab('trades');else refresh();};$('run').onchange=()=>{runSelectionExplicit=true;generation++;offset=0;if(active==='replay')tab('trades');else refresh();};
$('reload').onclick=()=>{offset=0;refresh();};$('more').onclick=()=>{offset+=100;refresh();};$('record-kind').onchange=()=>{offset=0;refresh();};$('decision-filter').onchange=()=>{offset=0;refresh();};$('search').onchange=()=>{offset=0;refresh();};$('close-replay').onclick=()=>tab('trades');
function chart(id,series,domain=null){const svg=$(id);svg.replaceChildren();const ns='http://www.w3.org/2000/svg';const el=(tag,attrs,value)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(value!==undefined)e.textContent=value;svg.append(e);return e;};const points=series.flatMap(s=>s.values).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));if(!points.length){el('text',{x:30,y:100,fill:'#92a6a9'},'No observations available');return;}const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=domain?domain[0]:Math.min(...ys),ymax=domain?domain[1]:Math.max(...ys);const X=x=>55+(x-xmin)/(xmax-xmin||1)*510,Y=y=>195-(y-ymin)/(ymax-ymin||1)*155;for(let i=0;i<=4;i++){const v=ymin+(ymax-ymin)*i/4;el('line',{x1:55,x2:565,y1:Y(v),y2:Y(v),stroke:'#304043'});el('text',{x:2,y:Y(v)+4,fill:'#92a6a9','font-size':10},fmt(v));}for(const s of series){const p=s.values.filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));el('polyline',{points:p.map(([x,y])=>X(x)+','+Y(y)).join(' '),fill:'none',stroke:s.color||'#94e1c0','stroke-width':2});}el('text',{x:55,y:222,fill:'#92a6a9','font-size':10},series.map(s=>s.label).join(' / '));}
async function refresh(){if(shuttingDown)return;const gen=++generation;try{$('mode-label').hidden=liveOnlyFleet;$('mode-label').textContent=liveOnlyFleet?'':$('mode').value+' RESEARCH';$('error').textContent='';if(active==='monitor'){const [health,data,strategies,recentTrades]=await Promise.all([get('/api/health'),get('/api/evaluation?'+query()),get('/api/strategies?'+query()),get('/api/trades?'+query()+'&limit=5&include_open=true')]);if(gen!==generation)return;referenceDigits=health.reference_digits||2;$('reference-label').textContent=health.asset+' · '+health.reference_index;renderBotVersion(health);renderRecentTrades(recentTrades);renderStrategies('strategy-overview',strategies);$('strategy-overview-status').textContent=(health.asset||'BTC')+'15 Settlement Edge · '+(liveOnlyFleet?'LIVE':$('mode').value)+' · one strategy; configuration and run are shown below';const b=data.record?.body;renderEntryEconomics('entry-economics',b?.entry_economics);$('evaluation-title').textContent='Evaluation details · '+(b?.model?.model_name||(b?'BTC15 Settlement Edge':'no selection'));$('evaluation-status').textContent=(!health.collector_fresh&&health.collector_startup_error?health.collector_startup_error:data.message)+(data.evaluation_age===null?'':' Last evaluated '+fmt(Math.max(0,data.evaluation_age),0)+' seconds ago.');const collector=health.collector;const member=collector?.mode===$('mode').value?collector?.models?.find(m=>m.run_id===($('run').value||data.record?.run_id)):null;const c=collector?.mode===$('mode').value&&(!$('run').value||$('run').value===collector.run_id)?collector:null;$('health').textContent=health.collector_fresh&&collector?.connected&&(c||member)?'Collector connected · '+(liveOnlyFleet?'LIVE':collector.mode):'Collector offline or stale';$('clock').textContent='Backend · '+new Date(health.server_time*1000).toLocaleTimeString();tiles('main-tiles',[['P(YES)',pct(b?.probability?.p_yes),'Uncalibrated model'],['Quality',fmt(b?.quality?.score,0)+'/100','Model score'],['Entry filter EV / contract',money(b?.net_ev),'Settlement-based entry filter']]);$('market-name').textContent=b?b.ticker+' · recorded '+new Date(b.timestamp*1000).toLocaleTimeString():'No market observations yet';$('recommendation').textContent=b?.decision||'WAIT';$('reasons').replaceChildren(...(b?.reasons?.length?b.reasons.map(r=>text('div',describeReason(r),'reason')):[text('div',b?'All configured entry filters passed; execution performs its own checks.':'Waiting for recorded market data.','muted')]));metrics('decision-metrics',[[b?.probability?.model?.startsWith('bleep-')?'Bleep YES probability':'Recorded YES probability',pct(b?.probability?.p_yes)],['Reference used in evaluation',referenceMoney(b?.features?.reference)],['Model age at decision',b?.model_age_seconds==null?'—':fmt(b.model_age_seconds*1000,0)+' ms'],['Bollinger entry filter',({disabled:'Disabled',unavailable:'Unavailable · original checks apply',allowed:'Passed',rejected:'Entry blocked'})[b?.bollinger_entry_filter?.status]||'—'],['Entry path',b?.entry_path||'standard'],['Confirmed samples',fmt(b?.lead?.confirmation_samples,0)],['Settlement lead / uncertainty',fmt(b?.lead?.lead_sigma)],['Known settlement samples',fmt(b?.lead?.known_samples,0)],['Required remaining average to reach strike',referenceMoney(b?.lead?.required_remaining_average)],['Capped entry confidence · '+(b?.entry_probability_basis||'bleep'),pct(b?.conservative_probability)],['Entry filter EV / contract',money(b?.net_ev)],['Effective entry ceiling',b?.effective_max_entry_price===undefined?'—':b.effective_max_entry_price===null?'No eligible price':money(b.effective_max_entry_price)],['Configured price ceiling',money(b?.config?.max_entry_price)],['Raw edge',pct(b?.raw_edge)],['Fee estimate',money(b?.estimated_fees)],['Slippage allowance',money(b?.expected_slippage)],['Signed distance',referenceMoney(b?.signed_distance)],['Time remaining at evaluation',fmt(b?.seconds_remaining,0)+' s']]);if($('mode').value==='BACKTEST')renderQuotes(b?.book,b?b.ticker+' · saved evaluation':'No recorded quotes',true);else if(!livePrices)renderQuotes(null,'Waiting for live quotes');metrics('features',[['Regime',b?.features?.regime||'—'],['Spread',pct(b?.book?.spread)],['ATR',referenceMoney(b?.features?.atr)],['Stochastic RSI',pct(b?.features?.stochastic_rsi)],['YES depth',fmt(b?.book?.yes_depth)],['NO depth',fmt(b?.book?.no_depth)],['60s momentum',pct(b?.features?.momentum_60)],['Bollinger position',pct(b?.features?.bollinger?.position)]]);metrics('positions', [['Worst-case exposure',money(c?.exposure)],['Daily realized P&L',money(c?.daily?.pnl)],['Kill switch',(c?.halted??member?.halted)?'HALTED':(c||member)?'Inactive':'—'],...Object.entries(c?.venue_pauses||{}).map(([ticker,p])=>[ticker+' · venue status','Trading blocked: '+p.event]),...(member&&!c?[['Strategy',member.model.model_name],['Open positions',fmt(member.open_positions,0)],['Realized P&L',money(member.realized_pnl)],['Entries',member.entries_active?'Enabled':'Inactive']]:[]),...Object.entries(c?.positions||{}).map(([ticker,p])=>[ticker,p.side.toUpperCase()+' · '+fmt(p.quantity)+' contracts at '+money(p.cost/p.bought)])]);}
else if(active==='trades'){
  const q=query();q.set('search',$('search').value);q.set('decision',$('decision-filter').value);q.set('offset',offset);
  const kind=$('record-kind').value,completed=kind==='trades',ledger=['order','fill','execution_rejection'].includes(kind);
  if(completed)q.set('include_open','true');
  if(!completed&&!ledger)q.set('group_by_market','true');
  if(ledger){q.set('kind',kind);q.delete('decision');}
  const d=await get((completed?'/api/trades?':'/api/records?')+q);if(gen!==generation)return;
  $('decision-filter-label').hidden=completed||ledger;
  if(offset===0)$('trade-rows').replaceChildren();
  $('record-count').textContent=fmt(d.total,0)+(completed?' trades':ledger?' '+kind+' records':' markets · '+fmt(d.evaluations,0)+' retained evaluations')+' match your filters · loaded '+new Date().toLocaleTimeString();
  if(completed&&d.total)renderTradeTable(d);
  else for(const r of d.rows)$('trade-rows').append(ledger?ledgerCard(r):marketGroup(r,q));
  if(!d.total)$('trade-rows').append(text('article','No records match this view. Try another run, mode, or filter. Live execution is disabled.','empty-state'));
  $('more').hidden=offset+d.rows.length>=d.total;
}
else if(active==='strategies'){const d=await get('/api/strategy');if(gen!==generation)return;renderStrategy(d);}
else if(active==='analytics'){const d=await get('/api/analytics?'+query());if(gen!==generation)return;tiles('analytics-tiles',[['Settled trades',fmt(d.trades,0)],['Net P&L',money(d.net_pnl)],['Brier score',fmt(d.calibration.brier,4)],['Calibration error',pct(d.calibration.ece)],['Win rate',pct(d.win_rate)],['Max drawdown',money(d.max_drawdown)],['Fill rate',pct(d.fill_rate)],['Calibration markets',fmt(d.calibration.n,0)]]);chart('calibration-chart',[{label:'Perfect calibration',color:'#71888b',values:[[0,0],[1,1]]},{label:'Observed accuracy',values:d.calibration.buckets.map(b=>[b.predicted,b.actual])}],[0,1]);chart('pnl-chart',[{label:'Closed trades / net dollars',values:d.cumulative_pnl.map((y,x)=>[x,y])}]);$('breakdowns').textContent=JSON.stringify({rejections:d.rejections,pnl_groups:d.pnl_groups,limitations:d.limitations},null,2);$('analytics-json').href=apiPath('/api/analytics?'+query());}}
catch(e){$('error').textContent=e.message;}}
async function replay(id){const gen=++generation;try{const d=await get('/api/replay/'+encodeURIComponent(id));if(gen!==generation)return;active='replay';document.querySelectorAll('.tab').forEach(e=>e.hidden=true);$('replay').hidden=false;$('replay-title').textContent=d.opportunity.market+' · '+d.opportunity.mode;const b=d.opportunity.body,path=d.path;chart('reference-chart',[{label:b.settlement_spec?.index_name||'Official reference',values:path.map(r=>[r.timestamp,r.body.features?.reference])},{label:'Strike',color:'#dea771',values:path.map(r=>[r.timestamp,r.body.settlement_spec.strike])}]);chart('probability-chart',[{label:'P(YES)',values:path.map(r=>[r.timestamp,r.body.probability?.p_yes])},{label:'Conservative YES',color:'#71888b',values:path.map(r=>[r.timestamp,r.body.probability?.conservative_yes])}],[0,1]);chart('price-chart',[{label:'Time / YES ask',values:path.map(r=>[r.timestamp,r.body.book.yes_ask])}],[0,1]);renderExplanation(b);renderEntryEconomics('replay-economics',b.entry_economics);metrics('replay-metrics',[['Evaluated at',new Date(d.opportunity.timestamp*1000).toLocaleString()],['Side considered',b.side?.toUpperCase()||'Undetermined'],['Estimated YES probability',pct(b.probability?.p_yes)],['Entry price / contract',money(b.expected_fill_price)],['Entry filter EV / contract',money(b.net_ev)],['Official reference at evaluation',referenceMoney(b.features?.reference)]]);$('replay-summary').textContent=JSON.stringify({opportunity_id:id,decision:b.decision,probability:b.probability,quality:b.quality,reasons:b.reasons,results:d.timeline.filter(r=>['trade_result','settlement'].includes(r.kind)).map(r=>r.body)},null,2);$('replay-json').href=apiPath('/api/replay/'+id);$('timeline').replaceChildren(...d.timeline.map(r=>{const e=text('div','','timeline-row');e.append(text('time',new Date(r.timestamp*1000).toLocaleTimeString()),text('strong',({transition:'System state changed',order:'Order recorded',fill:'Order filled',trade_result:'Trade completed',settlement:'Market settled',exit_intent:'Exit requested',execution_rejection:'Execution blocked'})[r.kind]||r.kind),technicalDetails(r.body));return e;}));}catch(e){$('error').textContent=e.message;}}
let lastRuns=0;
async function poll(){
  try {
    if(!shuttingDown&&!document.hidden){
      if(active!=='monitor')renderBotVersion(await get('/api/health'));
      if(active==='monitor'&&Date.now()-lastRuns>15000){await runs();lastRuns=Date.now();}
      if(active==='monitor')await refresh();
    }
  } catch(e){$('error').textContent=e.message;}
  finally{setTimeout(poll,active==='monitor'?1000:5000);}
}
poll();


function updateLiveReference(value){
  liveReference=value;
  updateContractContext();

}
const quoteFields=new Map();
function renderQuotes(book,label,recorded=false){
  $('quote-title').textContent=recorded?'Recorded market quotes':'Live market quotes';
  $('quote-context').textContent=label;
  for(const key of ['yes_bid','yes_ask','no_bid','no_ask']){
    let value=quoteFields.get(key);
    if(!value){const box=text('div','','quote');value=text('strong','—');box.append(text('small',key.replace('_',' ').toUpperCase()),value);$('quotes').append(box);quoteFields.set(key,value);}
    const next=book?.[key]==null?'—':fmt(book[key]*100,2)+'¢';
    if(value.textContent!==next)value.textContent=next;
  }
}
function clearLiveQuotes(){updateLiveReference(null);invalidateMarketQuotes();if($('mode').value!=='BACKTEST')renderQuotes(null,'Live quotes unavailable · reconnecting or waiting for data');}
let livePrices=false, lastMarketEvent=0;
const marketCards=new Map();
let displayedMarkets=[];
let marketClockOffset=0;
const cents=value=>value==null?'—':fmt(Number(value)*100,2)+'¢';
function renderMarkets(markets){
  displayedMarkets=markets;
  $('market-empty').hidden=markets.length>0;
  const keep=new Set(markets.map(m=>m.ticker));
  for(const [ticker,card] of marketCards){if(!keep.has(ticker)){card.root.remove();marketCards.delete(ticker);}}
  for(const m of markets){
    let card=marketCards.get(m.ticker);
    if(!card){
      const root=text('div','','contract-card'),heading=text('div','','section-title');
      const fields={};
      fields.phase=text('span','','badge');fields.feed=text('span','','muted');
      heading.append(fields.phase,fields.feed);root.append(heading,text('h3',m.ticker));
      fields.title=text('p','','muted');root.append(fields.title);
      const numbers=text('div','','contract-numbers');
      for(const [key,label] of [['strike','Strike price'],['distance','Reference vs. strike'],['countdown','Time to close']]){
        const cell=text('div','');fields[key]=text('strong','—');cell.append(text('small',label),fields[key]);numbers.append(cell);
      }
      root.append(numbers);
      const quotes=text('div','','contract-quotes');
      for(const side of ['yes','no']){
        const box=text('div','','contract-side');box.append(text('strong',side.toUpperCase()));
        for(const [suffix,label] of [['bid','Bid'],['ask','Ask'],['spread','Spread']]){
          const row=text('div','','metric');fields[side+'_'+suffix]=text('span','—');row.append(text('span',label),fields[side+'_'+suffix]);box.append(row);
        }
        quotes.append(box);
      }
      root.append(quotes);fields.footer=text('p','','muted contract-footer');root.append(fields.footer);
      card={root,fields};marketCards.set(m.ticker,card);$('official-markets').append(root);
    }
    const usable=m.fresh!==false;
    const values={title:m.title||'Crypto 15-minute contract',feed:m.fresh===false?'Quotes unavailable':m.fresh===true?'Streaming':'REST snapshot',strike:referenceMoney(m.floor_strike),footer:'Closes '+new Date(m.close_time).toLocaleTimeString()+' · Volume '+fmt(m.volume_fp,0)+' contracts'};
    for(const side of ['yes','no']){
      const bid=usable?m[side+'_bid_dollars']:null,ask=usable?m[side+'_ask_dollars']:null;
      values[side+'_bid']=cents(bid);values[side+'_ask']=cents(ask);
      values[side+'_spread']=bid!=null&&ask!=null&&Number(ask)>=Number(bid)?cents(Number(ask)-Number(bid)):'—';
    }
    for(const [key,value] of Object.entries(values))if(card.fields[key].textContent!==value)card.fields[key].textContent=value;
    card.root.classList.toggle('stale',!usable);
    $('official-markets').append(card.root);
  }
  updateContractContext();
}
function updateContractContext(){
  const now=Date.now()+marketClockOffset;
  for(const m of displayedMarkets){
    const fields=marketCards.get(m.ticker)?.fields;if(!fields)continue;
    const left=(Date.parse(m.close_time)-now)/1000;
    fields.phase.textContent=left<=0?'Closed':m.status==='open'?'Open contract':'Upcoming contract';
    fields.countdown.textContent=!Number.isFinite(left)?'—':left<=0?'Closed':Math.floor(left/60)+'m '+String(Math.floor(left%60)).padStart(2,'0')+'s';
    const strike=m.floor_strike==null?null:Number(m.floor_strike);
    const delta=liveReference!=null&&strike>0&&left>0?liveReference-strike:null;
    fields.distance.textContent=delta===null?'—':(delta>0?'+':delta<0?'−':'')+referenceMoney(Math.abs(delta))+' ('+(delta>0?'+':delta<0?'−':'')+pct(Math.abs(delta)/strike)+')';
  }
}
function invalidateMarketQuotes(){
  renderMarkets(displayedMarkets.map(m=>({...m,fresh:false})));
}
setInterval(()=>{if(!shuttingDown)updateContractContext();},1000);
async function refreshOfficial(){
  if(shuttingDown)return;
  try {
    const data=await get('/api/official-markets');
    if(livePrices)return;
    const stamp=data.fetched_at?new Date(data.fetched_at*1000).toLocaleTimeString():'—';
    $('official-status').textContent=(data.error?'Market data unavailable. Last snapshot: ':'REST snapshot · updated ')+stamp+' · Waiting for live collector';
    renderMarkets(data.markets.map(m=>({...m,fresh:data.error?false:undefined})));
  } catch(e){if(!livePrices)$('official-status').textContent='Market update unavailable; displayed snapshots may be stale.';}
}
const marketStream=new EventSource(apiPath('/api/market-stream'));
marketStream.onmessage=event=>{
  const data=JSON.parse(event.data);lastMarketEvent=performance.now();
  marketClockOffset=data.server_time*1000-Date.now();
  const s=data.snapshot;
  livePrices=!!(data.fresh&&s?.markets?.length);
  const receipt=data.reference;
  const ref=receipt?.reference_5hz;
  // Display only: allow 2 s of source clock lead, matching the normal collector
  // tolerance. Receipt/source age limits stay strict; execution checks are separate.
  const freshReference=receipt?.connected&&data.server_time-receipt.published_at>=0&&data.server_time-receipt.published_at<2&&ref&&data.server_time-ref.received>=0&&data.server_time-ref.received<2&&ref.value!=null&&Number.isFinite(Number(ref.value))&&ref.source_ts_ms!=null&&data.server_time-Number(ref.source_ts_ms)/1000>=-2&&data.server_time-Number(ref.source_ts_ms)/1000<2;
  if(!livePrices)clearLiveQuotes();
  updateLiveReference(freshReference?Number(ref.value):null);
  const referenceText=freshReference?referenceMoney(ref.value):'—';
  if($('live-reference').textContent!==referenceText)$('live-reference').textContent=referenceText;
  $('reference-status').textContent=freshReference?'Live · 5 Hz reference':ref?'Reference stale · '+fmt(Math.max(0,data.server_time-ref.received),1)+' s old':'Awaiting first reference';
  const recovery=receipt?.recovery||s?.recovery;
  const recoveryText=recovery?.entries_blocked?'Entries blocked · '+recovery.state+' · '+recovery.reasons.join(', '):recovery?.warning?'Backlog warning · ':'';
  if(!livePrices){$('official-status').textContent=recoveryText||'Processed quotes unavailable or stale · reference display is separate from strategy readiness';return;}
  const age=Math.max(0,(data.server_time-s.published_at)*1000);
  $('official-status').textContent='Streaming Kalshi quotes · processing lag '+fmt(s.processing_lag*1000,0)+' ms · snapshot age '+fmt(age,0)+' ms'+(s.clock_ok?'':' · clock check failed');
  if(recoveryText)$('official-status').textContent=recoveryText+' · '+$('official-status').textContent;
  if($('mode').value!=='BACKTEST'){const market=s.markets[0];renderQuotes(market.fresh?market.book:null,market.ticker+' · '+(market.fresh?'live · updated '+new Date(s.published_at*1000).toLocaleTimeString(undefined,{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}):'quotes stale / awaiting snapshot'));}
  renderMarkets(s.markets.map(m=>({...m,yes_bid_dollars:m.book.yes_bid,yes_ask_dollars:m.book.yes_ask,no_bid_dollars:m.book.no_bid,no_ask_dollars:m.book.no_ask})));
};
marketStream.onerror=()=>{livePrices=false;clearLiveQuotes();$('official-status').textContent='Live connection interrupted · reconnecting; displayed prices may be stale';$('live-reference').textContent='—';$('reference-status').textContent='Reference unavailable · reconnecting';};
setInterval(()=>{if((livePrices||liveReference!==null)&&performance.now()-lastMarketEvent>2500){livePrices=false;clearLiveQuotes();$('official-status').textContent='Live feed delayed · displayed prices may be stale';$('live-reference').textContent='—';$('reference-status').textContent='Reference unavailable · reconnecting';}},500);
refreshOfficial();
setInterval(()=>{if(active==='monitor'&&!livePrices)refreshOfficial();},15000);

function decisionLabel(b){return b.decision==='TRADE_CANDIDATE'?'Passed entry checks':b.decision==='NO_TRADE'?'Skipped entry':'Waiting for data';}
function simpleReason(r){
  const labels={VENUE_PAUSED:'Market trading paused; awaiting fresh active venue confirmation',PROCESSING_LAG:'Processing is behind the live feed; execution blocked',STRATEGY_DISABLED:'Strategy entries are disabled',ENTRY_WINDOW:'Outside the entry time window',EXISTING_ENTRY:'A position, pending order, exhausted retries or re-entry cooldown blocks another entry',POST_CLOSE_COOLDOWN:'Waiting after a completed trade before opening another position',MARKET_OPEN:'Market is not open for entry',FAVORED_SIDE:'No side meets the strategy requirements',MIN_PRICE:'Entry price is below the allowed range',MAX_PRICE:'Entry price is above the allowed range',MIN_PROBABILITY:'Estimated probability is too low',MIN_EDGE:'Estimated advantage is too small after costs',MIN_EV:'Estimated value is too low after costs',SPREAD:'The gap between buy and sell prices is too wide',LIQUIDITY:'Too few contracts are available',MODEL_QUALITY:'Model quality is below the required level',REGIME:'Market volatility is too extreme',STALE_REFERENCE:'Waiting for fresh official reference data',STALE_BOOK:'Waiting for a fresh, valid order book',MODEL_UNAVAILABLE:'Waiting for enough data to evaluate',UNVERIFIED_FEES:'Trading fees have not been verified',RISK_LIMIT:'The risk limit prevents another entry'};
  return labels[r.code]||String(r.code||'Unknown check').toLowerCase().replaceAll('_',' ');
}
function technicalDetails(body){const d=document.createElement('details');d.append(text('summary','Technical details'),text('pre',JSON.stringify(body,null,2)));return d;}
function tradeDollars(value, fixed=false){
  if(value==null||!Number.isFinite(Number(value)))return '—';
  return '$'+Number(value).toLocaleString('en-US',{minimumFractionDigits:fixed?4:2,maximumFractionDigits:4});
}
function renderRecentTrades(data){
  const root=$('recent-trades'),signature=JSON.stringify(data);
  $('recent-trades-status').textContent=(data.stale?'Update delayed · ':'')+'Latest 5 trades · open and closed · updated '+new Date(data.updated_at?data.updated_at*1000:Date.now()).toLocaleTimeString();
  if(root.dataset.snapshot===signature)return;
  root.dataset.snapshot=signature;
  if(data.rows.length){if(!$('recent-trade-body'))root.replaceChildren();renderTradeTable(data,'recent-trades',0);}
  else root.replaceChildren(text('p','No purchases filled in this selection yet.','muted'));
}
function tradeTime(label,timestamp){
  const line=text('small',label+': ','muted');
  if(!Number.isFinite(timestamp)){line.append('Unavailable');return line;}
  const date=new Date(timestamp*1000),time=text('time',date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}));
  time.dateTime=date.toISOString();time.title=date.toLocaleString(undefined,{timeZoneName:'long'});
  line.append(time);return line;
}
function renderTradeTable(data,target='trade-rows',pageOffset=offset){
  const bodyId=target==='trade-rows'?'completed-trade-body':'recent-trade-body';
  let body=$(bodyId);
  if(!body){
    const wrap=text('div','','trade-table-wrap'),table=text('table','','trade-table');
    table.append(text('caption','Trades · newest purchases first · numbers follow the selected filters'));
    const head=text('thead',''),headers=text('tr','');
    for(const label of ['Trade','Bought','Exit or payout','Market result','Total fees','Net result']){
      const cell=text('th',label);cell.scope='col';headers.append(cell);
    }
    head.append(headers);body=text('tbody','');body.id=bodyId;
    table.append(head,body);wrap.append(table);$(target).append(wrap);
  }
  const retained=target==='recent-trades'?new Map([...body.children].map(row=>[row.dataset.tradeId,row])):new Map();
  const visible=new Set();
  data.rows.forEach((record,index)=>{
    const key=record.run_id+':'+(record.body.trade_id||record.opportunity_id),signature=JSON.stringify([record,data.total-pageOffset-index]);
    visible.add(key);
    const existing=retained.get(key);
    if(existing?.dataset.signature===signature){body.append(existing);return;}
    if(existing)existing.remove();
    const b=record.body,isOpen=b.status==='OPEN',row=text('tr',''),identity=text('th','');identity.scope='row';
    row.dataset.tradeId=key;row.dataset.signature=signature;
    identity.append(text('strong',String(data.total-pageOffset-index)),text('small',record.market,'muted'));
    identity.append(text('small',isOpen?'OPEN':'CLOSED',isOpen?'pnl-positive':'muted'));
    if(b.source==='LIVE_FALLBACK')identity.append(text('small','Live fallback · confirmed exchange fills','muted'));else{const details=text('button','View details');details.onclick=()=>replay(record.opportunity_id);identity.append(details);}
    const bought=text('td',fmt(b.bought)+' '+(b.side||'').toUpperCase()+' at '+tradeDollars(b.entry));
    bought.append(tradeTime('First buy',b.opened));
    const settled=b.reason==='SETTLEMENT';
    const exit=text('td',isOpen?'Open · '+fmt(b.quantity)+' remaining':settled?(b.proceeds===0?'Settled worthless':'Settlement / sales returned '+tradeDollars(b.proceeds)+' total'):'Sold at '+tradeDollars(b.exit));
    exit.append(text('small',isOpen?(b.proceeds>0?'Partial sales returned '+tradeDollars(b.proceeds)+' so far':'Awaiting sale or settlement'):settled?'Final market result: '+(b.settlement_result||'unknown').toUpperCase():'Average sale price per contract','muted'));
    if(!isOpen)exit.append(tradeTime(settled?'Settled':'Sold / exit',b.exit_timestamp??(settled?(b.settlement_timestamp??record.timestamp):record.timestamp)));
    const fees=text('td',tradeDollars(b.fees,true)),net=text('td','');
    if(isOpen){fees.append(text('small','Fees so far','muted'));net.append(text('strong','Pending','muted'));}
    else net.append(text('strong',(b.net_pnl>0?'+':b.net_pnl<0?'−':'')+tradeDollars(Math.abs(b.net_pnl),true),b.net_pnl>0?'pnl-positive':b.net_pnl<0?'pnl-negative':'pnl-neutral'));
    const outcome=b.market_result??b.settlement_result;
    const marketResult=text('td',['yes','no'].includes(outcome)?outcome.toUpperCase():'Pending','market-result');
    marketResult.title='Recorded final market outcome, independent of the side bought or the trade’s profit. Pending means no confirmed outcome has been recorded.';
    row.append(identity,bought,exit,marketResult,fees,net);body.append(row);
  });
  for(const [key,row] of retained)if(!visible.has(key))row.remove();
}
function activityCard(r,completed){
  const b=r.body,card=text('article','','activity-card');
  const top=text('div','','activity-heading');
  top.append(text('span',completed?'Completed trade':decisionLabel(b),'outcome '+(completed?'completed':b.decision==='TRADE_CANDIDATE'?'candidate':'skipped')),text('time',new Date(r.timestamp*1000).toLocaleString(),'muted'));
  card.append(top,text('h3',r.market||b.ticker||'Market unavailable'));
  const reasons=b.reasons||[];
  card.append(text('p',completed?'Realized net result: '+money(b.net_pnl)+' · '+String(b.reason||'Completed').replaceAll('_',' ').toLowerCase():reasons.length?simpleReason(reasons[0])+(reasons.length>1?' · '+(reasons.length-1)+' more checks blocked entry':''):'Entry filters passed. Execution and risk checks still apply.','activity-reason'));
  const numbers=text('div','','activity-numbers');
  const values=completed?[['Side',b.side?.toUpperCase()||'—'],['Contracts',fmt(b.bought)],['Entry / contract',money(b.entry)],['Exit / payout',money(b.exit)]]:[['Side considered',b.side?.toUpperCase()||'—'],['Estimated YES probability',pct(b.probability?.p_yes)],['Entry / contract',money(b.expected_fill_price)],['Est. net value / contract',money(b.net_ev)]];
  for(const [label,value] of values){const box=text('div','');box.append(text('small',label),text('strong',value));numbers.append(box);}
  const button=text('button','View explanation →');button.onclick=()=>replay(completed?r.opportunity_id:r.id);
  card.append(numbers,button);return card;
}
function renderExplanation(b){
  const root=$('replay-explanation');root.replaceChildren(text('h3',decisionLabel(b)));
  if(b.probability?.model?.startsWith('bleep-')){root.append(text('p','Bleep YES probability: '+pct(b.probability.p_yes)+'. Safety clamp is '+(b.probability.safety_clamp_enabled?'on':'off')+'.'));}
  if(b.reasons?.length){root.append(text('p','Entry was skipped because these checks did not pass:'));const list=document.createElement('ul');for(const r of b.reasons)list.append(text('li',simpleReason(r)));root.append(list);}
  else root.append(text('p','Entry filters passed. This evaluation alone does not confirm an order or a fill.'));
  root.append(text('p','Probability and net value are model estimates, not guaranteed outcomes. Prices below are saved values from this evaluation.','muted'));
}

function marketGroup(group,filters){
  const root=text('details','','market-group'),summary=text('summary','');
  summary.append(text('strong',group.market||'Unknown market'),text('span',fmt(group.total,0)+' evaluations · '+fmt(group.skipped,0)+' skipped · '+fmt(group.candidates,0)+' passed entry checks','muted'),text('small','Latest evaluation '+new Date(group.latest_at*1000).toLocaleString()+' · '+group.runs+' run(s)','muted'));
  const body=text('div','','activity-list'),more=text('button','Load evaluations');
  root.append(summary,body,more);
  let loaded=0,busy=false;
  async function load(){
    if(busy)return;busy=true;more.disabled=true;more.textContent='Loading…';
    const q=new URLSearchParams(filters);q.delete('group_by_market');q.set('market',group.market);q.set('offset',loaded);
    try{const data=await get('/api/records?'+q);for(const row of data.rows)body.append(activityCard(row,false));loaded+=data.rows.length;more.hidden=loaded>=data.total;more.textContent='Load more evaluations';}
    catch(e){more.textContent='Retry loading evaluations';$('error').textContent=e.message;}
    finally{busy=false;more.disabled=false;}
  }
  root.ontoggle=()=>{if(root.open&&!loaded)load();};more.onclick=load;
  return root;
}

let strategySettings=null;
function renderStrategy(data){
  $('strategy-name').textContent=data.name;strategySettings=data.config;$('strategy-enabled').checked=data.config.enabled;
  const labels={fixed_stop_price:'Fixed stop bid price (0 uses multiplier)',entry_value_filters_enabled:'Require minimum net edge and expected value for entries',bleep_exchange_seed_enabled:'Seed Bleep indicators with historical exchange candles',bleep_safety_clamp_enabled:'Cap Bleep favored-side confidence at 75% when distance is below 0.5 sigma',one_trade_per_market:'Allow only one filled trade per market',standard_cashout_enabled:'Cash out standard entries at $0.10 net per contract with over 2 minutes left',post_close_cooldown:'Pause after a full position closes, across markets (seconds)',entry_retry_cooldown:'Unfilled-order retry cooldown (seconds)',exit_probability:'Held-side probability exit threshold (0–1)',profit_value_exit_enabled:'Enable the older probability-based profit-value exit',bollinger_entry_filter_enabled:'Filter entries outside fresh Bollinger Bands',daily_entry_limits_enabled:'Enforce daily attempt and cumulative exposure caps',late_min_probability:'Late entry minimum capped confidence (0 inherits standard)',late_lead_confirmation_samples:'Late entry confirming samples (0 inherits standard)',sustained_lead_enabled:'Require confirmed settlement lead',late_entry_enabled:'Allow confirmed late-settlement entries',late_no_new_entry:'Stop late entries (seconds before close)',lead_confirmation_samples:'Consecutive reference samples required',min_lead_sigma:'Normal entry lead (sigma; 0 disables minimum)',late_min_lead_sigma:'Late entry lead (sigma; 0 disables minimum)',hold_value_exit_enabled:'Exit when selling exceeds estimated hold value',entry_window_start:'Start considering entries (seconds before close)',no_new_entry:'Stop new entries (seconds before close)',min_entry_price:'Minimum entry price ($ / contract)',max_entry_price:'Maximum entry price ($ / contract)',min_probability:'Minimum capped Bleep confidence (0–1)',min_quality:'Minimum model quality (0–100)',min_edge:'Minimum edge ($ / contract)',min_ev:'Minimum expected value ($ / contract)',max_spread:'Maximum bid–ask spread ($)',min_liquidity:'Minimum available contracts',evaluation_interval:'Model calculation interval (seconds)',bankroll:'Paper bankroll ($)',fixed_contracts:'Contracts per entry',max_contracts:'Maximum contracts per entry',max_trade_dollars:'Maximum cost per trade ($)',max_open_exposure:'Maximum open exposure ($)',max_daily_loss:'Maximum daily loss ($)',max_daily_trades:'Maximum daily trades',take_profit:'Take-profit price ($; blank disables)'};
  const main=text('div','','strategy-fields'),advanced=document.createElement('details'),extra=text('div','','strategy-fields');advanced.append(text('summary','Advanced model, execution and risk settings'),extra);
  for(const [key,value] of Object.entries(data.config)){
    if(key==='enabled'||key==='asset')continue;
    const dailyCapDisabled=data.config.daily_entry_limits_enabled===false&&['max_daily_trades','max_daily_exposure'].includes(key);const label=text('label',(labels[key]||key.replaceAll('_',' '))+(dailyCapDisabled?' (disabled)':''));const input=document.createElement('input');input.name=key;
    input.type=typeof value==='boolean'?'checkbox':typeof value==='string'?'text':'number';
    if(input.type==='checkbox')input.checked=value;else{input.value=value??'';if(input.type==='number'){input.step='any';input.required=key!=='take_profit';}}
    label.append(input);(labels[key]?main:extra).append(label);
  }
  $('strategy-fields').replaceChildren(main,advanced);
  $('strategy-status').textContent='Saved configuration '+data.version+(data.version!==data.session_version?' · differs from this dashboard’s startup configuration':' · matches this dashboard’s startup configuration');
}
$('strategy-form').onsubmit=async event=>{
  event.preventDefault();if(!strategySettings)return;
  const values={...strategySettings,enabled:$('strategy-enabled').checked};
  for(const input of $('strategy-fields').querySelectorAll('input'))values[input.name]=input.type==='checkbox'?input.checked:input.type==='number'?(input.value===''?null:Number(input.value)):input.value;
  const button=event.submitter;button.disabled=true;$('strategy-status').textContent='Saving…';
  try{const response=await fetch(apiPath('/api/strategy'),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Settings were rejected');strategySettings=values;$('strategy-status').textContent=data.message;}
  catch(e){$('strategy-status').textContent='Not saved: '+e.message;}
  finally{button.disabled=false;}
};


function selectStrategyRun(runId, name, target){
  runSelectionExplicit=true;
  if(!Array.from($('run').options).some(o=>o.value===runId))$('run').add(new Option(name+' · '+runId,runId));
  $('run').value=runId;
  generation++;
  offset=0;
  tab(target);
  if(target==='monitor')$('evaluation-title').scrollIntoView({behavior:'smooth',block:'start'});
}
function renderStrategies(id,data){
  const root=$(id);
  if(id==='strategy-overview'){
    const total=data.lifetime;
    const summary=$('lifetime-summary');
    summary.replaceChildren(text('div',data.mode==='BACKTEST'?'Replay net P&L':'Realized net P&L · '+(liveOnlyFleet?'LIVE':data.mode),'label'),pnlValue(total.net_pnl));
    const stats=text('div','','overview-stats');
    for(const [label,value] of [['Completed',fmt(total.completed_trades,0)],['Win rate',pct(total.win_rate)],['Drawdown',money(total.max_drawdown)],['Open trades',fmt(total.open_trades,0)]]){
      const cell=text('div','');cell.append(text('small',label),text('strong',value));stats.append(cell);
    }
    summary.append(stats);
    const performance=performanceMetrics(total);performance.classList.add('overview-performance');summary.append(performance);
  }
  // Keep keyboard focus stable while the overview polls.
  if(root.contains(document.activeElement))return;
  root.replaceChildren(...data.rows.map(entry=>{
    const m=entry.model, r=entry.record, b=r?.body;
    const card=text('article','','strategy-card');
    const selected=entry.runs.some(run=>run.run_id===$('run').value);
    card.classList.toggle('selected',selected);
    const heading=text('div','','section-title');
    heading.append(text('h3',m.model_name),text('span',m.model_version,'badge'));
    card.append(heading,text('p','Config '+m.config_hash.slice(0,12)+' · '+entry.runs.length+' run(s)','muted'));
    card.append(text('p',entry.feed_status==='current'?'Evaluations current · '+(liveOnlyFleet?'live signal feed':entry.paper_execution?'paper execution on':'observation only'):entry.feed_status==='waiting_or_stale'?'Waiting for a fresh evaluation':'Strategy stopped · showing saved history','strategy-state'));
    card.append(text('p',entry.entries_enabled===null?'Historical configuration':entry.entries_enabled?'Entries enabled in configuration':'Entries inactive','strategy-state'));
    const performance=text('div','','strategy-performance');
    performance.append(text('small',data.mode==='BACKTEST'?'All-time replay net P&L':'Lifetime realized net P&L','muted'),pnlValue(entry.lifetime.net_pnl),text('small',fmt(entry.lifetime.completed_trades,0)+' completed trades · this configuration, all runs · after recorded fees','muted'));
    if(!entry.lifetime.completed_trades)performance.append(text('p','No completed trades yet.','muted'));
    performance.append(performanceMetrics(entry.lifetime));
    card.append(performance);
    if(data.mode==='LIVE')card.append(text('p','Live execution is disabled.','muted'));
    if(r){
      card.append(text('strong',b.decision==='TRADE_CANDIDATE'?'Passed entry checks':b.decision==='NO_TRADE'?'Skipped entry':b.decision||'Recorded evaluation','strategy-decision'));
      card.append(text('p',r.market+' · '+new Date(r.timestamp*1000).toLocaleString(),'muted'));
      const values=text('div','','activity-numbers');
      for(const [label,value] of [['Side',b.side?.toUpperCase()||'—'],['Conservative P(side)',pct(b.conservative_probability)],['Entry filter EV / contract',money(b.net_ev)]]){
        const cell=text('div','');cell.append(text('small',label),text('strong',value));values.append(cell);
      }
      card.append(values,text('p',b.reasons?.length?describeReason(b.reasons[0]):'Entry checks passed; an evaluation does not confirm a fill.','activity-reason'));
      card.append(text('small','Saved '+fmt(Math.max(0,data.server_time-r.timestamp),0)+' seconds ago · '+r.run_id,'muted'));
    }else card.append(text('p',entry.entries_enabled===false?'Entries are disabled. Evaluation and position management remain available while the collector runs.':'No saved evaluations in this mode. Select another mode or collect data for this strategy.','empty-state'));
    const runId=r?.run_id||entry.runs[0]?.run_id;
    if(runId){
      const actions=text('div','','strategy-actions');
      for(const [label,target] of [['Evaluation','monitor'],['History','trades'],['Results','analytics']]){
        const button=text('button',label);button.setAttribute('aria-label',label+' for '+m.model_name+' '+m.model_version);button.onclick=()=>selectStrategyRun(runId,m.model_name,target);actions.append(button);
      }
      card.append(actions);
    }
    return card;
  }));
}

$('shutdown').onclick=async()=>{
  if(!window.confirm(liveOnlyFleet?'Stop live signal collection and this dashboard? New live entries will be disabled. Real positions remain at the exchange; resume signal collection for automatic stop monitoring.':(fleetMode?'Stop ALL crypto paper collectors and this dashboard? ':'Shut down the bot and dashboard? ')+ ' New entries will stop, unfilled orders will be cancelled, and open paper positions will be saved without liquidation. Resume the same paper run later to continue position management and settlement.'))return;
  const button=$('shutdown'),status=$('shutdown-status');
  shuttingDown=true;generation++;button.disabled=true;status.hidden=false;
  status.textContent='Requesting safe shutdown…';
  try{
    const response=await fetch(apiPath('/api/shutdown'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
    const result=await response.json();
    if(!response.ok)throw Error(result.detail||'Shutdown request failed');
    while(true){
      const state=await get('/api/shutdown');
      status.textContent=state.message;
      if(state.status==='failed')throw Error(state.message);
      if(state.status==='stopped'){
        marketStream.close();livePrices=false;clearLiveQuotes();
        button.textContent='Stopped';$('health').textContent='Stopped safely';
        $('official-status').textContent='Dashboard stopped · displayed data is historical';
        if(state.open_positions!=null)status.textContent+=' Open paper positions saved: '+state.open_positions+'.';
        return;
      }
      await new Promise(resolve=>setTimeout(resolve,250));
    }
  }catch(error){
    status.textContent='Shutdown not confirmed: '+error.message+'. Check the bot log before restarting.';
    shuttingDown=false;button.disabled=false;
  }
};

function pnlValue(value){
  const node=text('strong',(value>0?'+':value<0?'−':'')+money(Math.abs(value)),'pnl-value');
  node.classList.add(value>0?'pnl-positive':value<0?'pnl-negative':'pnl-neutral');
  return node;
}

function performanceMetrics(data){
  const root=text('div','','performance-metrics');
  const streak=data.current_streak>0?data.current_streak+(data.current_streak===1?' win':' wins'):data.current_streak<0?Math.abs(data.current_streak)+(data.current_streak===-1?' loss':' losses'):data.completed_trades?'None · break-even':'—';
  const factor=data.profit_factor===null?(data.wins?'N/A · no losses':'—'):fmt(data.profit_factor);
  const values=[
    ['Total wins',fmt(data.wins,0),'Completed trades with positive net P&L after recorded fees, within the selected scope. Open and break-even trades are excluded.'],
    ['Total losses',fmt(data.losses,0),'Completed trades with negative net P&L after recorded fees, within the selected scope. Open and break-even trades are excluded.'],
    ['Max realized drawdown',money(data.max_drawdown),'Largest peak-to-trough decline in completed-trade P&L, starting at zero.'],
    ['Average net P&L / trade',money(data.average_pnl),'Completed trades after recorded fees.'],
    ['Win rate',pct(data.win_rate),data.wins+' wins / '+data.completed_trades+' completed trades; break-even trades count in the denominator.'],
    ['Profit factor',factor,'Gross winning net P&L divided by absolute losing net P&L. Undefined when there are no losses.'],
    ['Open exposure at cost',money(data.open_exposure),'Remaining filled inventory at average entry cost including allocated entry fees. Excludes unfilled orders; not a live valuation.'],
    ['Open trades',fmt(data.open_trades,0),'Trades with remaining filled inventory and no completion record.'],
    ['Current streak',streak,'Consecutive completed wins or losses; a break-even trade resets the streak.'],
    ['Longest win / loss streak',data.longest_win_streak+' / '+data.longest_loss_streak,'Longest consecutive wins / losses.']
  ];
  for(const [label,value,note] of values){
    const cell=text('div','','performance-metric');cell.title=note;
    cell.append(text('small',label),text('strong',value));root.append(cell);
  }
  const details=document.createElement('details');
  details.append(text('summary','How these metrics are calculated'),text('p','Drawdown and streaks follow completed trades by timestamp (record ID breaks ties). Break-even trades reset streaks and count toward the win-rate sample. Open exposure uses remaining inventory at entry cost, including allocated entry fees; it excludes unfilled orders. Selected-scope totals interleave completed runs; archived strategies are excluded from the active view. Backtest metrics describe replay records, not a continuous portfolio.','muted'));
  root.append(details);
  return root;
}

$('history-scope').onchange=async()=>{runSelectionExplicit=false;generation++;$('run').value='';offset=0;await runs();tab('trades');};

function ledgerCard(record){
  const b=record.body,card=text('article','','activity-card');
  if(record.kind==='execution_rejection'){
    card.append(text('h3','Submission rejected'),text('p',record.market+' · '+new Date(record.timestamp*1000).toLocaleString(),'muted'));
    card.append(text('p',b.message||String(b.reason||'Recorded rejection').replaceAll('_',' '),'reason'));
    card.append(text('small','Code: '+b.reason+' · Run: '+record.run_id,'muted'),technicalDetails(b));
    return card;
  }
  const label=record.kind==='fill'?'Fill · '+(b.action||'unknown'): 'Order · '+(b.status||'recorded');
  card.append(text('h3',label),text('p',record.market+' · '+new Date(record.timestamp*1000).toLocaleString(),'muted'));
  card.append(text('p',(b.side?b.side.toUpperCase()+' · ':'')+'Quantity '+fmt(b.quantity??b.remaining)+' · '+money(b.price??b.limit)));
  if(b.reason)card.append(text('p',b.reason.replaceAll('_',' '),'reason'));
  if(b.status==='cancelled'&&b.details?.reasons?.length){
    for(const r of b.details.reasons)card.append(text('p',describeReason(r),'reason'));
    card.append(text('small','Attempt '+(b.attempt||1)+' · open '+fmt(b.elapsed_seconds,2)+' s · queue remaining '+fmt(b.queue_remaining,2),'muted'));
  }
  card.append(technicalDetails(b));
  return card;
}

document.body.classList.toggle('overview-active',active==='monitor');


let liveOnlyFleet=false;
const fleetCards=new Map();
function markTradesDelayed(card){
  card.tradesDelayed=true;
  let notice=card.trades.querySelector('.trade-update-delayed');
  if(!notice){notice=text('p','Update delayed · showing last available trades','muted trade-update-delayed');card.trades.append(notice);}
  if(!card.trades.querySelector('.fleet-trade'))notice.textContent='Update delayed · waiting for trade history';
}
async function refreshFleet(){
  try{
    if(document.hidden||active!=='monitor')return;
    const response=await fetch('/api/fleet',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(response.status===404)return;
    if(!response.ok)throw Error('Portfolio status unavailable');
    const data=await response.json();
    liveOnlyFleet=Boolean(data.live_only);
    if(liveOnlyFleet){
      $('mode-label').hidden=true;
      document.querySelector('#strategies .badge').hidden=true;
      document.querySelector('#fleet-panel h2').textContent='All markets';
      $('fleet-panel').setAttribute('aria-label','Live portfolios');
      $('mode').value='PAPER';
      $('mode').closest('label').hidden=true;
      $('history-scope').value='settlement';
      $('history-scope').closest('label').hidden=true;
      document.querySelector('.aside-bottom').textContent='Live trading · Settlement Edge';
    }
    if(!fleetMode){$('asset-details').open=false;$('asset-details-label').hidden=false;document.body.classList.add('fleet-mode');window.dispatchEvent(new Event('fleet-ready'));}
    fleetMode=true;
    $('manual-open').hidden=false;
    window.manualMarketChoices=data.assets.flatMap(row=>(row.markets||[]).filter(m=>Date.parse(m.close_time)>data.server_time*1000).map(m=>({ticker:m.ticker,asset:row.asset})));
    $('fleet-panel').hidden=false;$('shutdown').textContent='Stop all safely';
    const selected=assetBase.split('/')[2]||data.default_asset;
    $('asset-details-label').textContent=selected+' · decisions, positions and feed details';
    $('fleet-status').textContent=(liveOnlyFleet?'Live bots · P&L after recorded fees · latest 3 purchases per asset · updated ':'Active paper runs · P&L after recorded fees · latest 3 purchases per asset · updated ')+new Date(data.server_time*1000).toLocaleTimeString()+'. Select an asset for full history and settings.';
    if(data.live?.available===false)$('fleet-status').textContent+=' Live execution unavailable; order and position status cannot be confirmed.';
    $('fleet-total').textContent=(data.totals_complete?'Realized net P&L · ':'Partial realized net P&L · ')+money(data.realized_pnl);
    for(const row of data.assets){
      let card=fleetCards.get(row.asset);
      if(!card){
        const link=document.createElement('a');link.className='fleet-card';link.href=row.url;
        const title=text('strong',row.asset),price=text('strong','—','fleet-price'),state=text('span','','badge'),detail=text('p','','muted'),result=text('div','','fleet-result');
        const contract=text('div','','fleet-contract'),trades=text('div','','fleet-trades');
        const column=text('div','','fleet-column'),tradeButton=text('button','Buy / sell '+row.asset+' · real','fleet-trade-button');
        tradeButton.type='button';tradeButton.dataset.asset=row.asset;tradeButton.disabled=true;
        tradeButton.onclick=()=>{if(tradeButton.dataset.ticker)window.openManualTicket(tradeButton.dataset.ticker);};
        const livePanel=text('div','','fleet-live-panel'),liveTitle=text('strong','Live bot · '+row.asset+' · all markets'),liveLabel=text('label','Allow new buys'),liveEnabled=document.createElement('input'),liveCountLabel=text('label','Contracts (1–20)'),liveCount=document.createElement('input'),liveSave=text('button','Apply live settings'),liveTakeover=text('button','Take manual control'),liveStatus=text('p','Disabled · exits remain managed','muted');
        liveEnabled.type='checkbox';liveEnabled.setAttribute('aria-label',row.asset+' allow automatic real buys');liveLabel.prepend(liveEnabled);
        liveCount.type='number';liveCount.min='1';liveCount.max='20';liveCount.step='1';liveCount.value='10';liveCount.required=true;liveCount.setAttribute('aria-label',row.asset+' live bot contracts');liveCountLabel.append(liveCount);
        liveSave.type=liveTakeover.type='button';livePanel.append(liveTitle,liveLabel,liveCountLabel,liveSave,liveTakeover,liveStatus);
        if(row.paper_only){liveTitle.textContent='Paper bot · '+row.asset;liveLabel.hidden=liveCountLabel.hidden=liveSave.hidden=liveTakeover.hidden=true;tradeButton.hidden=true;}
        let liveRevision=0,liveTicker='',liveDirty=false,liveBusy=false;
        liveEnabled.onchange=liveCount.oninput=()=>{liveDirty=true;};
        liveSave.onclick=async()=>{
          if(liveBusy||!liveTicker||!liveCount.reportValidity())return;
          const enabled=liveEnabled.checked,ticker=liveTicker,count=Number(liveCount.value);
          if(enabled&&!window.confirm('Enable REAL trades for all '+row.asset+' markets until switched off? Buy up to '+count+' contracts per market; hard stop sells with a 1¢ minimum and take profit sells at 99¢ or higher. Continues into future markets and across dashboard restarts.'))return;
          liveBusy=true;liveSave.disabled=true;
          try{const response=await fetch('/api/live/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker,enabled,contracts:count,revision:liveRevision,confirm:enabled?'ENABLE_REAL_TRADING':''})});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Invalid live settings');liveDirty=false;liveRevision=result.revision;liveStatus.textContent=enabled?'Live buys enabled':'New buys off · automatic exits continue';}catch(e){liveStatus.textContent=e.message;}finally{liveBusy=false;liveSave.disabled=false;}
        };
        liveTakeover.onclick=async()=>{
          if(liveBusy||!liveTicker||!window.confirm('Pause automatic buys AND exits for '+liveTicker+' and turn off future buys for this asset so you can manage the position manually? Already submitted orders may still execute.'))return;
          liveBusy=true;
          try{const response=await fetch('/api/live/takeover/'+encodeURIComponent(liveTicker),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok)throw Error('Manual takeover failed');liveDirty=false;liveStatus.textContent='Manual control · automatic exits paused';}catch(e){liveStatus.textContent=e.message;}finally{liveBusy=false;}
        };
        const updateLive=(ticker,state)=>{
          if(row.paper_only){liveStatus.textContent='Paper validation · real trading disabled';return;}
          const changed=ticker!==liveTicker;if(changed){liveTicker=ticker;liveDirty=false;}
          const control=state?.controls?.[ticker],policy=state?.assets?.[row.asset];const guardChanged=policy?.revision!==liveRevision;liveRevision=policy?.revision||0;
          if(policy?.loss_guard&&!policy.enabled&&guardChanged){liveDirty=false;liveEnabled.checked=false;}
          if(!liveDirty&&!liveBusy){liveEnabled.checked=policy?.enabled||false;liveCount.value=policy?.contracts||10;}
          liveSave.disabled=liveBusy||!ticker||!state?.running;liveTakeover.disabled=liveBusy||!ticker;
          if(!liveDirty&&!liveBusy)liveStatus.textContent=!state?.running?'Live worker unavailable':policy?.loss_guard?.reason||state?.messages?.[ticker]||(control?.paused?'Manual control · automatic exits paused':policy?.enabled?'Live buys enabled · continues across markets':'New buys off · automatic exits continue');
        };
        const quick=text('div','','fleet-quick'),buySections=text('div','','fleet-buy-sections'),quickBuys=[];
        for(const outcome of ['yes','no']){
          const section=text('div','','fleet-buy-section fleet-buy-'+outcome),heading=text('strong',outcome.toUpperCase());
          const label=text('label','Contracts'),quantity=document.createElement('input');
          quantity.type='number';quantity.min='0.01';quantity.max='100000';quantity.step='0.01';quantity.value='1';quantity.required=true;
          quantity.setAttribute('aria-label',row.asset+' '+outcome.toUpperCase()+' buy quantity');label.append(quantity);
          const button=text('button','Buy '+outcome.toUpperCase()+' · max 95¢','fleet-quick-buy');
          button.type='button';button.disabled=true;button.dataset.asset=row.asset;
          button.onclick=()=>{if(quantity.reportValidity()&&tradeButton.dataset.ticker)window.openManualTicket(tradeButton.dataset.ticker,{action:'buy',side:outcome,count:quantity.value});};
          section.append(heading,label,button);buySections.append(section);quickBuys.push(button);
        }
        const sellSection=text('div','','fleet-sell-section'),quantityLabel=text('label','Contracts to sell'),quantity=document.createElement('input'),sideLabel=text('label','Sell side'),side=document.createElement('select');
        quantity.type='number';quantity.min='0.01';quantity.max='100000';quantity.step='0.01';quantity.value='1';quantity.required=true;
        quantity.setAttribute('aria-label',row.asset+' sell quantity');
        for(const value of ['yes','no']){const option=text('option',value.toUpperCase());option.value=value;side.append(option);}
        side.setAttribute('aria-label',row.asset+' sell outcome');quantityLabel.append(quantity);sideLabel.append(side);
        const quickSell=text('button','Sell now · min 1¢','fleet-quick-sell');
        quickSell.type='button';quickSell.disabled=true;quickSell.dataset.asset=row.asset;
        quickSell.onclick=()=>{if(quantity.reportValidity()&&tradeButton.dataset.ticker)window.openManualTicket(tradeButton.dataset.ticker,{action:'sell',side:side.value,count:quantity.value});};
        sellSection.append(quantityLabel,sideLabel,quickSell);
        const bought=text('div','','fleet-manual-bought');bought.setAttribute('aria-live','polite');
        quick.append(buySections,bought,sellSection);if(row.paper_only)quick.hidden=true;
        const stopBot=text('button','Stop '+row.asset+' bot','shutdown-button'),stopStatus=text('p','','muted');
        stopBot.type='button';stopStatus.setAttribute('role','status');
        stopBot.onclick=async()=>{
          if(!window.confirm('Stop the '+row.asset+' collector and disable its new automatic buys? Other bots and this dashboard stay running. Paper positions are saved. Managed real positions must be closed or taken over manually first.'))return;
          stopBot.disabled=true;
          const endpoint='/api/bots/'+encodeURIComponent(row.asset)+'/shutdown';
          try{
            const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
            let result=await response.json();if(!response.ok)throw Error(result.detail||'Shutdown failed');
            const deadline=Date.now()+70000;
            while(result.status==='stopping'){
              stopStatus.textContent=result.message||'Stopping…';
              if(Date.now()>deadline)throw Error('Shutdown not confirmed. Check collector status before retrying.');
              await new Promise(resolve=>setTimeout(resolve,1000));
              const check=await fetch(endpoint,{cache:'no-store'});if(!check.ok)throw Error('Cannot confirm shutdown status');result=await check.json();
            }
            if(result.status!=='stopped')throw Error(result.message||'Shutdown failed');
            stopStatus.textContent=row.asset+' stopped. Other bots remain running.';
          }catch(error){stopStatus.textContent=error.message;}finally{stopBot.disabled=false;}
        };
        link.append(title,price,state,detail,result,contract,trades);column.append(livePanel,tradeButton,quick,stopBot,stopStatus,link);$('fleet-assets').append(column);
        card={link,price,state,detail,result,contract,trades,tradeButton,quickBuys,quickSell,bought,updateLive};fleetCards.set(row.asset,card);
      }
      card.link.classList.toggle('selected',row.asset===selected);
      if(row.asset===selected)card.link.setAttribute('aria-current','page');else card.link.removeAttribute('aria-current');
      card.price.textContent=referenceMoney(row.price,row.reference_digits);
      card.state.textContent=row.operational?.state||'UNKNOWN';
      card.detail.textContent=row.operational?.summary||'Waiting for collector';
      if(row.paper_worker){const p=row.paper_worker;card.detail.textContent+=' Paper simulation: '+p.state.toLowerCase()+(p.reason?' — '+p.reason:'')+'.';}
      card.result.replaceChildren(text('small',liveOnlyFleet?'Recorded live net P&L':'Recorded net P&L · paper + live fallback'),pnlValue(row.realized_pnl),text('small',(row.completed_trades??'—')+' completed · '+(row.open_positions??'—')+' open'));
      const streak=row.current_streak;
      const streakLabel=streak==null||!row.completed_trades?'—':streak>0?streak+(streak===1?' win':' wins'):streak<0?Math.abs(streak)+(streak===-1?' loss':' losses'):'None · break-even';
      const stats=text('div','','fleet-performance');
      for(const [label,value] of [['Win rate',pct(row.win_rate)],['Wins / losses',(row.wins??'—')+' / '+(row.losses??'—')],['Current streak',streakLabel],['Longest win / loss',row.completed_trades?row.longest_win_streak+' / '+row.longest_loss_streak:'—']]){
        const cell=text('div','');cell.append(text('small',label),text('strong',value));stats.append(cell);
      }
      stats.title=(row.breakeven_trades??'—')+' break-even trades. Completed trades after fees only. Win rate includes break-even trades; break-even resets the current streak. Open trades are excluded.';
      card.result.append(stats);
      const market=row.markets?.filter(m=>Date.parse(m.close_time)>data.server_time*1000).sort((a,b)=>Date.parse(a.close_time)-Date.parse(b.close_time))[0];
      card.updateLive(market?.ticker||'',data.live);
      const purchases=market?.manual_purchases;
      card.bought.replaceChildren(text('small','Bought this market · real dashboard orders'));
      if(purchases){
        const counts=text('div','','fleet-bought-counts');
        counts.append(text('strong','YES '+fmt(Number(purchases.yes),2),'bought-yes'),text('strong','NO '+fmt(Number(purchases.no),2),'bought-no'));
        card.bought.append(counts,text('small',purchases.pending?'Order status pending · totals may be incomplete':'Total purchases, including contracts later sold','muted'));
      }else card.bought.append(text('p',market?'Purchase totals unavailable':'Awaiting current market','muted'));
      card.tradeButton.dataset.ticker=market?.ticker||'';
      card.tradeButton.disabled=!market||data.live?.available===false;for(const button of card.quickBuys)button.disabled=!market||data.live?.available===false;card.quickSell.disabled=!market||data.live?.available===false;
      card.contract.replaceChildren(text('h3','Current contract'));
      if(market){
        const left=Math.max(0,Math.ceil(Date.parse(market.close_time)/1000-data.server_time));
        card.contract.append(text('p',market.ticker,'fleet-ticker'),text('p','Strike '+referenceMoney(market.floor_strike,row.reference_digits)),text('p','Closes in '+Math.floor(left/60)+'m '+String(left%60).padStart(2,'0')+'s · '+new Date(market.close_time).toLocaleTimeString()));
        const quotes=text('div','','fleet-quotes');
        for(const side of ['yes','no'])quotes.append(text('p',side.toUpperCase()+' · '+cents(market.fresh?market.book?.[side+'_bid']:null)+' / '+cents(market.fresh?market.book?.[side+'_ask']:null)));
        card.contract.append(text('small',market.fresh?'Bid / ask':'Quotes unavailable or stale','muted'),quotes);
      }else card.contract.append(text('p','Awaiting current contract','muted'));
    }
    await Promise.all(data.assets.map(async row=>{
      const card=fleetCards.get(row.asset);
      try{
        if(row.recent_trades_stale)markTradesDelayed(card);
        if(row.recent_trade_version!==undefined&&card.recentTradeVersion===row.recent_trade_version&&!card.tradesDelayed)return;
        const response=await fetch(row.url+'api/trades?mode=PAPER&scope=settlement&include_open=true&limit=3&run_id='+encodeURIComponent(row.run_id),{cache:'no-store',signal:AbortSignal.timeout(5000)});
        if(!response.ok)throw Error('Trade history unavailable');
        const trades=await response.json();
        const retainedTrades=new Map([...card.trades.querySelectorAll('.fleet-trade')].map(item=>[item.dataset.tradeId,item]));
        card.trades.replaceChildren(text('h3','Recent trades'));
        if(!trades.rows.length)card.trades.append(text('p','No filled purchases yet.','muted'));
        for(const trade of trades.rows){
          const b=trade.body,key=trade.run_id+':'+(b.trade_id||trade.opportunity_id),signature=JSON.stringify(trade);
          const retained=retainedTrades.get(key);
          if(retained?.dataset.signature===signature){card.trades.append(retained);continue;}
          const item=text('div','','fleet-trade');item.dataset.tradeId=key;item.dataset.signature=signature;
          if(b.source==='LIVE_FALLBACK')item.append(text('small','Live fallback · confirmed exchange fills','muted'));
          item.append(text('small',trade.market,'fleet-ticker'),text('p',fmt(b.bought,0)+' '+(b.side||'').toUpperCase()+' at '+cents(b.entry)),text('small',new Date((b.opened??trade.timestamp)*1000).toLocaleString()));
          item.append(text('strong',b.status==='OPEN'?'OPEN · result pending':money(b.net_pnl)+' net',b.status==='OPEN'?'muted':b.net_pnl>=0?'pnl-positive':'pnl-negative'));
          card.trades.append(item);
        }
        card.recentTradeVersion=row.recent_trade_version;
        card.tradesDelayed=Boolean(trades.stale);
        if(trades.stale)markTradesDelayed(card);
      }catch(error){markTradesDelayed(card);}
    }));

  }catch(error){if(fleetMode){$('fleet-status').textContent='Overview unavailable · saved P&L and trades may be stale.';for(const card of fleetCards.values()){markTradesDelayed(card);card.updateLive('',null);card.bought.replaceChildren(text('p','Purchase totals unavailable','muted'));card.tradeButton.disabled=true;for(const button of card.quickBuys)button.disabled=true;card.quickSell.disabled=true;card.tradeButton.dataset.ticker='';card.price.textContent='—';card.contract.replaceChildren(text('p','Live contract unavailable','muted'));}}}
  finally{if(!shuttingDown)setTimeout(refreshFleet,2000);}
}
refreshFleet();
