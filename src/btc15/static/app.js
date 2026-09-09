'use strict';
const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});
const describeReason=r=>{const labels={PROCESSING_LAG:'Processing is behind the live feed; execution blocked',STRATEGY_DISABLED:'Strategy entries are disabled',ENTRY_WINDOW:'Outside the configured entry window',EXISTING_ENTRY:'An entry has already been attempted for this market',STALE_REFERENCE:'Official reference data is stale',STALE_BOOK:'Order book is stale or invalid',MIN_EV:'Expected value is below the minimum',MIN_EDGE:'Net edge is below the minimum',UNVERIFIED_FEES:'Fee metadata has not been verified',MODEL_UNAVAILABLE:'Settlement model unavailable',RISK_LIMIT:'Risk budget exhausted'};return (labels[r.code]||r.code.replaceAll('_',' '))+(r.actual===null||r.actual===undefined?'':typeof r.actual==='number'?' · observed '+fmt(r.actual,3):' · '+String(r.actual));};
const pct=v=>v===null||v===undefined?'—':fmt(v*100,1)+'%';
const money=v=>v===null||v===undefined?'—':'$'+fmt(v);
const text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
let shuttingDown=false;
let active='monitor', offset=0, generation=0, liveReference=null;
async function get(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error('Request failed: '+r.status);return r.json();}
function query(){return new URLSearchParams({mode:$('mode').value,...($('run').value?{run_id:$('run').value}:{})});}
function tiles(id,values){$(id).replaceChildren(...values.map(([label,value,note])=>{const e=text('div','','tile');e.append(text('div',label,'label'),text('strong',value));if(note)e.append(text('small',note));return e;}));}
function metrics(id,values){$(id).replaceChildren(...values.map(([k,v])=>{const e=text('div','','metric');e.append(text('span',k),text('span',v));return e;}));}
function tab(name){if(name==='trades')offset=0;active=name;document.querySelectorAll('.tab').forEach(e=>e.hidden=e.id!==name);$('replay').hidden=true;document.querySelectorAll('nav button').forEach(e=>e.classList.toggle('selected',e.dataset.tab===name));$('page-title').textContent={monitor:'Live overview',trades:'Opportunity memory',analytics:'Results & accuracy',strategies:'Strategies'}[name];refresh();}
document.querySelectorAll('nav button').forEach(e=>e.onclick=()=>tab(e.dataset.tab));
async function runs(){const mode=$('mode').value;const rows=await get('/api/runs?mode='+mode);if(mode!==$('mode').value)return;const selected=$('run').value,selectedLabel=$('run').selectedOptions[0]?.textContent;$('run').replaceChildren(new Option('Latest / all runs',''),...rows.map(r=>new Option((r.body.model?.model_name||'BTC15 Settlement Edge')+' · '+(r.body.model?.model_version||'v1')+' · '+r.run_id+' · '+new Date(r.timestamp*1000).toLocaleString(),r.run_id)));if(selected){if(!rows.some(r=>r.run_id===selected))$('run').add(new Option(selectedLabel,selected));$('run').value=selected;}}
$('mode').onchange=async()=>{generation++;$('run').value='';refreshOfficial();await runs();offset=0;if(active==='replay')tab('trades');else refresh();};$('run').onchange=()=>{generation++;offset=0;if(active==='replay')tab('trades');else refresh();};
$('reload').onclick=()=>{offset=0;refresh();};$('more').onclick=()=>{offset+=100;refresh();};$('record-kind').onchange=()=>{offset=0;refresh();};$('decision-filter').onchange=()=>{offset=0;refresh();};$('search').onchange=()=>{offset=0;refresh();};$('close-replay').onclick=()=>tab('trades');
function chart(id,series,domain=null){const svg=$(id);svg.replaceChildren();const ns='http://www.w3.org/2000/svg';const el=(tag,attrs,value)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(value!==undefined)e.textContent=value;svg.append(e);return e;};const points=series.flatMap(s=>s.values).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));if(!points.length){el('text',{x:30,y:100,fill:'#92a6a9'},'No observations available');return;}const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=domain?domain[0]:Math.min(...ys),ymax=domain?domain[1]:Math.max(...ys);const X=x=>55+(x-xmin)/(xmax-xmin||1)*510,Y=y=>195-(y-ymin)/(ymax-ymin||1)*155;for(let i=0;i<=4;i++){const v=ymin+(ymax-ymin)*i/4;el('line',{x1:55,x2:565,y1:Y(v),y2:Y(v),stroke:'#304043'});el('text',{x:2,y:Y(v)+4,fill:'#92a6a9','font-size':10},fmt(v));}for(const s of series){const p=s.values.filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));el('polyline',{points:p.map(([x,y])=>X(x)+','+Y(y)).join(' '),fill:'none',stroke:s.color||'#94e1c0','stroke-width':2});}el('text',{x:55,y:222,fill:'#92a6a9','font-size':10},series.map(s=>s.label).join(' / '));}
async function refresh(){if(shuttingDown)return;const gen=++generation;try{$('mode-label').textContent=$('mode').value+' RESEARCH';$('error').textContent='';if(active==='monitor'){const [health,data,strategies]=await Promise.all([get('/api/health'),get('/api/evaluation?'+query()),get('/api/strategies?mode='+$('mode').value)]);if(gen!==generation)return;renderStrategies('strategy-overview',strategies);$('strategy-overview-status').textContent=strategies.rows.length+' active strategy configurations · '+$('mode').value+' · cards show latest saved evaluations across runs';const b=data.record?.body;$('evaluation-title').textContent='Evaluation details · '+(b?.model?.model_name||(b?'BTC15 Settlement Edge':'no selection'));$('evaluation-status').textContent=(!health.collector_fresh&&health.collector_startup_error?health.collector_startup_error:data.message)+(data.evaluation_age===null?'':' Last evaluated '+fmt(Math.max(0,data.evaluation_age),0)+' seconds ago.');const collector=health.collector;const member=collector?.mode===$('mode').value?collector?.models?.find(m=>m.run_id===($('run').value||data.record?.run_id)):null;const c=collector?.mode===$('mode').value&&(!$('run').value||$('run').value===collector.run_id)?collector:null;$('health').textContent=health.collector_fresh&&collector?.connected&&(c||member)?'Collector connected · '+collector.mode:'Collector offline or stale';$('clock').textContent='Backend · '+new Date(health.server_time*1000).toLocaleTimeString();tiles('main-tiles',[[$('mode').value==='BACKTEST'?'Evaluation BRTI':'Live BRTI',money($('mode').value==='BACKTEST'?b?.features?.reference:liveReference),$('mode').value==='BACKTEST'?'Reference saved with evaluation':'5 Hz display · model reference below'],['Strike',money(b?.settlement_spec?.strike),'Contract threshold'],['P(YES)',pct(b?.probability?.p_yes),'Uncalibrated model'],['Model quality',fmt(b?.quality?.score,0)+'/100','Separate from probability']]);$('market-name').textContent=b?b.ticker+' · recorded '+new Date(b.timestamp*1000).toLocaleTimeString():'No market observations yet';$('recommendation').textContent=b?.decision||'WAIT';$('reasons').replaceChildren(...(b?.reasons?.length?b.reasons.map(r=>text('div',describeReason(r),'reason')):[text('div',b?'All configured entry filters passed; execution performs its own checks.':'Waiting for recorded market data.','muted')]));metrics('decision-metrics',[['Reference used in evaluation',money(b?.features?.reference)],['Model age at decision',b?.model_age_seconds==null?'—':fmt(b.model_age_seconds*1000,0)+' ms'],['Conservative P(side)',pct(b?.conservative_probability)],['Uncertainty',pct(b?.probability?.uncertainty)],['Net EV / contract',money(b?.net_ev)],['Raw edge',pct(b?.raw_edge)],['Fee estimate',money(b?.estimated_fees)],['Slippage allowance',money(b?.expected_slippage)],['Signed distance',money(b?.signed_distance)],['Time remaining at evaluation',fmt(b?.seconds_remaining,0)+' s']]);if($('mode').value==='BACKTEST')renderQuotes(b?.book,b?b.ticker+' · saved evaluation':'No recorded quotes',true);else if(!livePrices)renderQuotes(null,'Waiting for live quotes');metrics('features',[['Regime',b?.features?.regime||'—'],['Spread',pct(b?.book?.spread)],['ATR',money(b?.features?.atr)],['Stochastic RSI',pct(b?.features?.stochastic_rsi)],['YES depth',fmt(b?.book?.yes_depth)],['NO depth',fmt(b?.book?.no_depth)],['60s momentum',pct(b?.features?.momentum_60)],['Bollinger position',pct(b?.features?.bollinger?.position)]]);metrics('positions', [['Mode',c?.mode||(member?collector.mode:'No matching collector')],['Collector',health.collector_fresh&&(c||member)?'Current':'Offline / stale'],['Worst-case exposure',money(c?.exposure)],['Daily realized P&L',money(c?.daily?.pnl)],['Kill switch',(c?.halted??member?.halted)?'HALTED':(c||member)?'Inactive':'—'],...(member&&!c?[['Strategy',member.model.model_name],['Open positions',fmt(member.open_positions,0)],['Realized P&L',money(member.realized_pnl)],['Entries',member.entries_active?'Enabled':'Inactive']]:[]),...Object.entries(c?.positions||{}).map(([ticker,p])=>[ticker,p.side.toUpperCase()+' · '+fmt(p.quantity)+' contracts at '+money(p.cost/p.bought)])]);}
else if(active==='trades'){
  const q=query();q.set('search',$('search').value);q.set('decision',$('decision-filter').value);q.set('offset',offset);
  const completed=$('record-kind').value==='trades';
  if(!completed)q.set('group_by_market','true');
  const d=await get((completed?'/api/trades?':'/api/records?')+q);if(gen!==generation)return;
  $('decision-filter-label').hidden=completed;
  if(offset===0)$('trade-rows').replaceChildren();
  $('record-count').textContent=fmt(d.total,0)+(completed?' completed trades':' markets · '+fmt(d.evaluations,0)+' evaluations')+' match your filters · loaded '+new Date().toLocaleTimeString();
  for(const r of d.rows)$('trade-rows').append(completed?activityCard(r,true):marketGroup(r,q));
  if(!d.total)$('trade-rows').append(text('article','No records match this view. Try another run, mode, or filter. Live execution is disabled.','empty-state'));
  $('more').hidden=offset+d.rows.length>=d.total;
}
else if(active==='strategies'){const [d,catalog]=await Promise.all([get('/api/strategy'),get('/api/strategies?mode='+$('mode').value)]);if(gen!==generation)return;renderStrategy(d);renderStrategies('strategy-library',catalog);}
else if(active==='analytics'){const d=await get('/api/analytics?'+query());if(gen!==generation)return;tiles('analytics-tiles',[['Settled trades',fmt(d.trades,0)],['Net P&L',money(d.net_pnl)],['Brier score',fmt(d.calibration.brier,4)],['Calibration error',pct(d.calibration.ece)],['Win rate',pct(d.win_rate)],['Max drawdown',money(d.max_drawdown)],['Fill rate',pct(d.fill_rate)],['Calibration markets',fmt(d.calibration.n,0)]]);chart('calibration-chart',[{label:'Perfect calibration',color:'#71888b',values:[[0,0],[1,1]]},{label:'Observed accuracy',values:d.calibration.buckets.map(b=>[b.predicted,b.actual])}],[0,1]);chart('pnl-chart',[{label:'Closed trades / net dollars',values:d.cumulative_pnl.map((y,x)=>[x,y])}]);$('breakdowns').textContent=JSON.stringify({rejections:d.rejections,pnl_groups:d.pnl_groups,limitations:d.limitations},null,2);$('analytics-json').href='/api/analytics?'+query();}}
catch(e){$('error').textContent=e.message;}}
async function replay(id){const gen=++generation;try{const d=await get('/api/replay/'+encodeURIComponent(id));if(gen!==generation)return;active='replay';document.querySelectorAll('.tab').forEach(e=>e.hidden=true);$('replay').hidden=false;$('replay-title').textContent=d.opportunity.market+' · '+d.opportunity.mode;const b=d.opportunity.body,path=d.path;chart('reference-chart',[{label:'BRTI',values:path.map(r=>[r.timestamp,r.body.features?.reference])},{label:'Strike',color:'#dea771',values:path.map(r=>[r.timestamp,r.body.settlement_spec.strike])}]);chart('probability-chart',[{label:'P(YES)',values:path.map(r=>[r.timestamp,r.body.probability?.p_yes])},{label:'Conservative YES',color:'#71888b',values:path.map(r=>[r.timestamp,r.body.probability?.conservative_yes])}],[0,1]);chart('price-chart',[{label:'Time / YES ask',values:path.map(r=>[r.timestamp,r.body.book.yes_ask])}],[0,1]);renderExplanation(b);metrics('replay-metrics',[['Evaluated at',new Date(d.opportunity.timestamp*1000).toLocaleString()],['Side considered',b.side?.toUpperCase()||'Undetermined'],['Estimated YES probability',pct(b.probability?.p_yes)],['Entry price / contract',money(b.expected_fill_price)],['Estimated net value / contract',money(b.net_ev)],['Official reference at evaluation',money(b.features?.reference)]]);$('replay-summary').textContent=JSON.stringify({opportunity_id:id,decision:b.decision,probability:b.probability,quality:b.quality,reasons:b.reasons,results:d.timeline.filter(r=>['trade_result','settlement'].includes(r.kind)).map(r=>r.body)},null,2);$('replay-json').href='/api/replay/'+id;$('timeline').replaceChildren(...d.timeline.map(r=>{const e=text('div','','timeline-row');e.append(text('time',new Date(r.timestamp*1000).toLocaleTimeString()),text('strong',({transition:'System state changed',order:'Order recorded',fill:'Order filled',trade_result:'Trade completed',settlement:'Market settled',exit_intent:'Exit requested',execution_rejection:'Execution blocked'})[r.kind]||r.kind),technicalDetails(r.body));return e;}));}catch(e){$('error').textContent=e.message;}}
let lastRuns=0;
async function poll(){
  try {
    if(!shuttingDown&&!document.hidden){
      if(Date.now()-lastRuns>15000){await runs();lastRuns=Date.now();}
      if(active==='monitor'||active==='analytics')await refresh();
    }
  } catch(e){$('error').textContent=e.message;}
  finally{setTimeout(poll,active==='monitor'?1000:5000);}
}
poll();


function updateLiveReference(value){
  liveReference=value;
  updateContractContext();
  if($('mode').value==='BACKTEST')return;
  const tile=$('main-tiles').firstElementChild;
  if(tile){tile.querySelector('.label').textContent='Live BRTI';tile.querySelector('strong').textContent=money(value);tile.querySelector('small').textContent=value===null?'Waiting for fresh reference':'5 Hz display · model reference below';}
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
      for(const [key,label] of [['strike','Strike price'],['distance','Bitcoin vs. strike'],['countdown','Time to close']]){
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
    const values={title:m.title||'Bitcoin 15-minute contract',feed:m.fresh===false?'Quotes unavailable':m.fresh===true?'Streaming':'REST snapshot',strike:money(m.floor_strike),footer:'Closes '+new Date(m.close_time).toLocaleTimeString()+' · Volume '+fmt(m.volume_fp,0)+' contracts'};
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
    fields.distance.textContent=delta===null?'—':(delta>0?'+':delta<0?'−':'')+money(Math.abs(delta))+' ('+(delta>0?'+':delta<0?'−':'')+pct(Math.abs(delta)/strike)+')';
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
const marketStream=new EventSource('/api/market-stream');
marketStream.onmessage=event=>{
  const data=JSON.parse(event.data);lastMarketEvent=performance.now();
  marketClockOffset=data.server_time*1000-Date.now();
  const s=data.snapshot;
  livePrices=!!(data.fresh&&s?.markets?.length);
  const receipt=data.reference;
  const ref=receipt?.reference_5hz;
  // Display only: tolerate up to 500 ms of source clock lead; local freshness stays strict.
  const freshReference=receipt?.connected&&data.server_time-receipt.published_at>=0&&data.server_time-receipt.published_at<2&&ref&&data.server_time-ref.received>=0&&data.server_time-ref.received<2&&ref.value!=null&&Number.isFinite(Number(ref.value))&&ref.source_ts_ms!=null&&data.server_time-Number(ref.source_ts_ms)/1000>=-0.5&&data.server_time-Number(ref.source_ts_ms)/1000<2;
  if(!livePrices)clearLiveQuotes();
  updateLiveReference(freshReference?Number(ref.value):null);
  $('live-reference').textContent=freshReference?money(ref.value):'—';
  $('reference-status').textContent=freshReference?'Live · 5 Hz reference':ref?'Reference stale · '+fmt(Math.max(0,data.server_time-ref.received),1)+' s old':'Awaiting first reference';
  if(!livePrices){$('official-status').textContent='Processed quotes unavailable or stale · reference display is separate from strategy readiness';return;}
  const age=Math.max(0,(data.server_time-s.published_at)*1000);
  $('official-status').textContent='Streaming Kalshi quotes · processing lag '+fmt(s.processing_lag*1000,0)+' ms · snapshot age '+fmt(age,0)+' ms'+(s.clock_ok?'':' · clock check failed');
  if($('mode').value!=='BACKTEST'){const market=s.markets[0];renderQuotes(market.fresh?market.book:null,market.ticker+' · '+(market.fresh?'live · updated '+new Date(s.published_at*1000).toLocaleTimeString(undefined,{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}):'quotes stale / awaiting snapshot'));}
  renderMarkets(s.markets.map(m=>({...m,yes_bid_dollars:m.book.yes_bid,yes_ask_dollars:m.book.yes_ask,no_bid_dollars:m.book.no_bid,no_ask_dollars:m.book.no_ask})));
};
marketStream.onerror=()=>{livePrices=false;clearLiveQuotes();$('official-status').textContent='Live connection interrupted · reconnecting; displayed prices may be stale';$('live-reference').textContent='—';$('reference-status').textContent='Reference unavailable · reconnecting';};
setInterval(()=>{if((livePrices||liveReference!==null)&&performance.now()-lastMarketEvent>2500){livePrices=false;clearLiveQuotes();$('official-status').textContent='Live feed delayed · displayed prices may be stale';$('live-reference').textContent='—';$('reference-status').textContent='Reference unavailable · reconnecting';}},500);
refreshOfficial();
setInterval(()=>{if(active==='monitor'&&!livePrices)refreshOfficial();},15000);

function decisionLabel(b){return b.decision==='TRADE_CANDIDATE'?'Passed entry checks':b.decision==='NO_TRADE'?'Skipped entry':'Waiting for data';}
function simpleReason(r){
  const labels={PROCESSING_LAG:'Processing is behind the live feed; execution blocked',STRATEGY_DISABLED:'Strategy entries are disabled',ENTRY_WINDOW:'Outside the entry time window',EXISTING_ENTRY:'An entry was already attempted for this market',MARKET_OPEN:'Market is not open for entry',FAVORED_SIDE:'No side meets the strategy requirements',MIN_PRICE:'Entry price is below the allowed range',MAX_PRICE:'Entry price is above the allowed range',MIN_PROBABILITY:'Estimated probability is too low',MIN_EDGE:'Estimated advantage is too small after costs',MIN_EV:'Estimated value is too low after costs',SPREAD:'The gap between buy and sell prices is too wide',LIQUIDITY:'Too few contracts are available',MODEL_QUALITY:'Model quality is below the required level',REGIME:'Market volatility is too extreme',STALE_REFERENCE:'Waiting for fresh official reference data',STALE_BOOK:'Waiting for a fresh, valid order book',MODEL_UNAVAILABLE:'Waiting for enough data to evaluate',UNVERIFIED_FEES:'Trading fees have not been verified',RISK_LIMIT:'The risk limit prevents another entry'};
  return labels[r.code]||String(r.code||'Unknown check').toLowerCase().replaceAll('_',' ');
}
function technicalDetails(body){const d=document.createElement('details');d.append(text('summary','Technical details'),text('pre',JSON.stringify(body,null,2)));return d;}
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
  strategySettings=data.config;$('strategy-enabled').checked=data.config.enabled;
  const labels={entry_window_start:'Start considering entries (seconds before close)',no_new_entry:'Stop new entries (seconds before close)',min_entry_price:'Minimum entry price ($ / contract)',max_entry_price:'Maximum entry price ($ / contract)',min_probability:'Minimum probability (0–1)',min_quality:'Minimum model quality (0–100)',min_edge:'Minimum edge ($ / contract)',min_ev:'Minimum expected value ($ / contract)',max_spread:'Maximum bid–ask spread ($)',min_liquidity:'Minimum available contracts',evaluation_interval:'Model calculation interval (seconds)',bankroll:'Paper bankroll ($)',fixed_contracts:'Contracts per entry',max_contracts:'Maximum contracts per entry',max_trade_dollars:'Maximum cost per trade ($)',max_open_exposure:'Maximum open exposure ($)',max_daily_loss:'Maximum daily loss ($)',max_daily_trades:'Maximum daily trades',take_profit:'Take-profit price ($; blank disables)'};
  const main=text('div','','strategy-fields'),advanced=document.createElement('details'),extra=text('div','','strategy-fields');advanced.append(text('summary','Advanced model, execution and risk settings'),extra);
  for(const [key,value] of Object.entries(data.config)){
    if(key==='enabled')continue;
    const label=text('label',labels[key]||key.replaceAll('_',' '));const input=document.createElement('input');input.name=key;
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
  try{const response=await fetch('/api/strategy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Settings were rejected');strategySettings=values;$('strategy-status').textContent=data.message;}
  catch(e){$('strategy-status').textContent='Not saved: '+e.message;}
  finally{button.disabled=false;}
};


function selectStrategyRun(runId, name, target){
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
    summary.replaceChildren(text('div',data.mode==='BACKTEST'?'All-time replay net P&L':'Lifetime realized net P&L · '+data.mode,'label'),pnlValue(total.net_pnl),text('p',fmt(total.completed_trades,0)+' completed trades · all recorded strategies, including inactive and historical configurations','muted'),text('small',data.mode==='BACKTEST'?'Sum of separate replay results; repeated datasets may be included. This is not one portfolio.':'After recorded trading fees. Open positions are excluded. All history retained in this database; independent strategy portfolios are summed.','muted'));
  }
  if(id==='strategy-overview')$('lifetime-summary').append(performanceMetrics(data.lifetime));
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
    card.append(text('p',entry.feed_status==='current'?'Evaluations current · '+(entry.paper_execution?'paper execution on':'observation only'):entry.feed_status==='waiting_or_stale'?'Waiting for a fresh evaluation':'Strategy stopped · showing saved history','strategy-state'));
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
      for(const [label,value] of [['Side',b.side?.toUpperCase()||'—'],['Conservative P(side)',pct(b.conservative_probability)],['Net EV / contract',money(b.net_ev)]]){
        const cell=text('div','');cell.append(text('small',label),text('strong',value));values.append(cell);
      }
      card.append(values,text('p',b.reasons?.length?describeReason(b.reasons[0]):'Entry checks passed; an evaluation does not confirm a fill.','activity-reason'));
      card.append(text('small','Saved '+fmt(Math.max(0,data.server_time-r.timestamp),0)+' seconds ago · '+r.run_id,'muted'));
    }else card.append(text('p',entry.entries_enabled===false?'No saved evaluations in this mode. This strategy starts inactive; the model-paper workflow evaluates active momentum strategies.':'No saved evaluations in this mode. Select another mode or collect data for this strategy.','empty-state'));
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
  if(!window.confirm('Shut down the bot and dashboard? New entries will stop, unfilled orders will be cancelled, and open paper positions will be saved without liquidation. Resume the same paper run later to continue position management and settlement.'))return;
  const button=$('shutdown'),status=$('shutdown-status');
  shuttingDown=true;generation++;button.disabled=true;status.hidden=false;
  status.textContent='Requesting safe shutdown…';
  try{
    const response=await fetch('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
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
  details.append(text('summary','How these metrics are calculated'),text('p','Drawdown and streaks follow completed trades by timestamp (record ID breaks ties). Break-even trades reset streaks and count toward the win-rate sample. Open exposure uses remaining inventory at entry cost, including allocated entry fees; it excludes unfilled orders. Combined totals interleave independent strategies and runs. Backtest metrics describe replay records, not a continuous portfolio.','muted'));
  root.append(details);
  return root;
}
