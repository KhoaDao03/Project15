const $=id=>document.getElementById(id);
const el=(tag,value,cls)=>{const node=document.createElement(tag);node.textContent=value??'—';if(cls)node.className=cls;return node;};
const numeric=value=>typeof value==='number'&&Number.isFinite(value);
const money=value=>numeric(value)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:4}).format(value):'—';
const signedMoney=value=>numeric(value)?(value>0?'+':'')+money(value):'—';
const pnlClass=value=>value>0?'positive':value<0?'negative':'';
const percent=value=>numeric(value)?(value*100).toFixed(1)+'%':'—';
const count=value=>numeric(value)?value.toLocaleString('en-US'):'—';
const symbols=['BTC','ETH','SOL','XRP','GOLD','SILVER','WTI'];
const names=asset=>asset==='WTI'?'OIL / WTI':asset;
const coinPaths={
 BTC:'M10 5v14M13 5v14M7 7h7a3 3 0 0 1 0 6H8m1 0h6a3 3 0 0 1 0 6H7',
 ETH:'M12 2 5 12l7 4 7-4-7-10Zm-7 13 7 7 7-7-7 4-7-4Z',
 SOL:'m6 5-3 3h15l3-3H6Zm-3 6 3 3h15l-3-3H3Zm3 6-3 3h15l3-3H6Z',
 XRP:'M3 3 9 9q3 3 6 0l6-6M3 21l6-6q3-3 6 0l6 6',
 GOLD:'m9 4-2 6h10l-2-6H9ZM4 13l-3 7h10l-2-7H4Zm11 0-2 7h10l-3-7h-5Z',
 SILVER:'m9 4-2 6h10l-2-6H9ZM4 13l-3 7h10l-2-7H4Zm11 0-2 7h10l-3-7h-5Z',
 WTI:'M12 2C10 8 5 10 5 15a7 7 0 0 0 14 0c0-5-5-7-7-13Z'
};
let latest=null;
let selected='BTC',historyMode=false,historyOffset=0,historyTotal=0,historyBusy=false,historyRequest=0;
function coin(asset){
  const node=el('span','','coin '+asset.toLowerCase()),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS('http://www.w3.org/2000/svg','path');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');path.setAttribute('d',coinPaths[asset]);
  const outline=['BTC','XRP'].includes(asset);path.setAttribute('fill',outline?'none':'currentColor');path.setAttribute('stroke',outline?'currentColor':'none');path.setAttribute('stroke-width','1.7');svg.append(path);node.append(svg);return node;
}
function marketButton(asset){const button=el('button','','market-button');button.type='button';button.setAttribute('aria-pressed',String(selected===asset));button.append(coin(asset),el('span',names(asset)));button.addEventListener('click',()=>selectMarket(asset));return button;}
function summarize(assets){
  const sum=key=>assets.length===7&&assets.every(a=>numeric(a[key]))?assets.reduce((n,a)=>n+a[key],0):null;
  const result={};for(const key of ['realized_pnl','completed_trades','wins','losses','breakeven_trades','open_positions'])result[key]=sum(key);
  result.win_rate=result.completed_trades>0&&numeric(result.wins)?result.wins/result.completed_trades:null;
  return result;
}
function leaders(assets){const crypto=assets.filter(a=>symbols.slice(0,4).includes(a.asset));if(crypto.length!==4||crypto.some(a=>!numeric(a.realized_pnl)||!numeric(a.completed_trades))||!crypto.some(a=>a.completed_trades>0))return [];const best=Math.max(...crypto.map(a=>a.realized_pnl));return crypto.filter(a=>a.realized_pnl===best);}
function liveBuyingStatus(data,asset){if(data.stale||!data.live_available||typeof asset.live_policy?.enabled!=='boolean')return ['Unavailable','buying-unknown'];return asset.live_policy.enabled?['ON','buying-on']:['OFF','buying-off'];}
function buyingBadge(data,asset){const [label,cls]=liveBuyingStatus(data,asset);const badge=el('span',label,'badge live-buying '+cls);badge.title='Saved new-buy setting; entries require strategy and health checks. Automatic exits are separate.';return badge;}
function statusBadge(data,asset){return el('span',data.stale?'Updates delayed':asset.healthy?asset.state:'Checks pending','badge '+(data.stale||!asset.healthy?'status-warning':''));}
function entryConfidenceView(data,asset,detail=false){
  const root=el('div','','probability'+(detail?' probability-detail':''));
  const values=detail?el('div','','probability-values'):root;
  const context=detail?el('div','','probability-context'):null;
  if(detail){values.append(el('small','Entry confidence'));root.append(values,context);}
  const markets=asset.markets||[];
  if(!markets.length)values.append(el('span','Unavailable','muted'));
  for(const market of markets){
    const line=el('div','','probability-market'),p=market.probability;
    if(markets.length>1)line.append(el('small',market.ticker));
    const available=!data.stale&&market.fresh&&p?.available===true&&
      ['yes','no'].includes(p.side)&&numeric(p.confidence)&&p.confidence>=0&&p.confidence<=1&&numeric(p.timestamp);
    if(available){
      line.append(el('span',p.side.toUpperCase()+' '+percent(p.confidence)));
      if(detail){
        if(markets.length>1)context.append(el('small',market.ticker));
        context.append(el('small','As of '+new Date(p.timestamp*1000).toLocaleString()));
        if(p.quality_warning)context.append(el('small','Data quality warning','stale'));
      }
    }else line.append(el('span','Unavailable','muted'));
    values.append(line);
  }
  if(detail)root.append(el('small','Capped confidence for the selected side; other entry checks still apply.','probability-note'));
  return root;
}
function selectMarket(asset){selected=asset;historyOffset=0;historyRequest++;if(latest)render(latest);if(historyMode)loadHistory();}
function streakLabel(value){if(!numeric(value))return '—';if(value===0)return 'No streak';return Math.abs(value)+' '+(value>0?(value===1?'win':'wins'):(value===-1?'loss':'losses'));}
function performanceCells(row,a){for(const [key,format] of [['realized_pnl',signedMoney],['completed_trades',count],['wins',count],['losses',count],['win_rate',percent],['current_streak',streakLabel],['open_positions',count]])row.append(el('td',format(a[key]),['realized_pnl','current_streak'].includes(key)?pnlClass(a[key]):''));}
function render(data){
  latest=data;
  $('connection').textContent=data.stale?'Updates delayed':'Connected';$('connection').className=data.stale?'stale':'';
  if(numeric(data.updated_at))$('updated').textContent=new Date(data.updated_at*1000).toLocaleString();
  const assets=symbols.map(asset=>data.assets.find(a=>a.asset===asset)||{asset,markets:[],trades:[]});
  const total=summarize(assets),top=leaders(assets),partial=!data.totals_complete||Object.entries(total).some(([key,value])=>key!=='win_rate'&&value===null);
  const metrics=[['$','Realized P&L',signedMoney(total.realized_pnl),(data.live_only?'Live · ':'')+'After recorded fees'+(partial?' · partial':''),pnlClass(total.realized_pnl)],['▤','Completed trades',count(total.completed_trades),'Recorded history'],['♜','Wins / losses',count(total.wins)+' / '+count(total.losses),'Completed trades'],['◎','Overall win rate',percent(total.win_rate),'Weighted by completed trades'],['▤','Open trades',count(total.open_positions),'Current snapshot'],['♛','Top crypto by P&L',top.map(a=>a.asset).join(' / ')||'—',top.length?signedMoney(top[0].realized_pnl):'No ranked history']];
  $('summary').replaceChildren(...metrics.map(([icon,label,value,note,cls])=>{const metric=el('div','','metric'),text=el('div','');text.append(el('small',label),el('strong',value,cls),el('small',note));metric.append(el('span',icon,'metric-icon'),text);return metric;}));
  const rows=[];
  for(const [label,group] of [['Digital assets',assets.slice(0,4)],['Commodities',assets.slice(4)]]){
    const heading=el('tr','','group-row'),cell=el('td',label.toUpperCase());cell.colSpan=9;heading.append(cell);rows.push(heading);
    group.sort((a,b)=>(numeric(b.realized_pnl)?b.realized_pnl:-Infinity)-(numeric(a.realized_pnl)?a.realized_pnl:-Infinity));
    for(const a of group){const row=el('tr','',selected===a.asset?'selected':''),market=el('td','');market.append(marketButton(a.asset));if(top.some(t=>t.asset===a.asset))market.firstChild.append(el('span',top.length>1?'Joint top':'Top crypto','top-badge'));const rank=numeric(a.realized_pnl)?1+group.filter(b=>numeric(b.realized_pnl)&&b.realized_pnl>a.realized_pnl).length:'—';row.append(el('td',rank),market);performanceCells(row,a);row.addEventListener('click',event=>{if(!event.target.closest('button'))selectMarket(a.asset);});rows.push(row);}
  }
  $('comparison').replaceChildren(...rows);
  const totals=el('tr',''),label=el('td',partial?'TOTAL · partial':'TOTAL (7 markets)');label.colSpan=2;totals.append(label);performanceCells(totals,total);$('totals').replaceChildren(totals);
  $('insight').textContent=(top.length?(top.map(a=>a.asset).join(' and ')+(top.length>1?' share the crypto lead':' leads crypto')+' by recorded realized P&L. '):'Crypto ranking requires available completed history for all four markets. ')+(partial?'Some totals are unavailable or partial. ':'')+'These are recorded trade results, not underlying market-price returns.';
  $('market-status').replaceChildren(...assets.map(a=>{const row=el('tr','',selected===a.asset?'selected':''),market=el('td',''),price=el('td',money(a.price)),status=el('td',''),buying=el('td','');market.append(marketButton(a.asset));if(!a.markets?.length||a.markets.some(m=>!m.fresh))price.append(el('small','⚠ Stale market data','stale'));status.append(statusBadge(data,a));buying.append(buyingBadge(data,a));const probability=el('td','');probability.append(entryConfidenceView(data,a));row.append(market,price,probability,status,buying);row.addEventListener('click',event=>{if(!event.target.closest('button'))selectMarket(a.asset);});return row;}));
  const asset=assets.find(a=>a.asset===selected);$('detail-title').textContent=names(selected)+' / Market detail';$('detail-icon').replaceWith(Object.assign(coin(selected),{id:'detail-icon'}));
  const reference=el('div','','reference-row'),price=el('div',''),status=el('div','');price.append(el('small','Reference price'),el('strong',money(asset.price)));status.append(el('small','Operational status'),el('br',''),statusBadge(data,asset));reference.append(price,status,entryConfidenceView(data,asset,true));
  const source=el('div','','source');source.append(el('small','Source market identifier(s)'),el('span',asset.markets?.map(m=>m.ticker+(m.fresh?'':' · stale')).join(', ')||'Unavailable'));
  const streaks=el('div','','streaks');streaks.append(el('strong','Performance (recorded)'));for(const [name,value] of [['Current win streak',numeric(asset.current_streak)?Math.max(0,asset.current_streak):null],['Current loss streak',numeric(asset.current_streak)?Math.max(0,-asset.current_streak):null],['Longest win streak',asset.longest_win_streak],['Longest loss streak',asset.longest_loss_streak]]){const line=el('div','','streak-row');line.append(el('span',name),el('span',count(value)));streaks.append(line);}$('detail').replaceChildren(reference,source,streaks);
  for(const button of $('market-tabs').children)button.setAttribute('aria-pressed',String(button.dataset.asset===selected));
  $('trades-title').textContent=names(selected)+(historyMode?' · Trade records':' · Latest 5 trades');
  $('trade-scope').textContent=asset.trades_stale?'Trade updates delayed · displayed records may be stale.':'Recent rows shown; all-time totals above cover recorded history.';
  if(!historyMode)renderTrades(asset.trades||[]);

}
function renderTrades(trades){const wrap=el('div','','table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Trade records');const table=el('table',''),head=el('thead',''),headers=el('tr','');for(const label of ['Market / time','Status','Market settled','Side / quantity','Entry','Exit','Fees','Net P&L'])headers.append(el('th',label));head.append(headers);table.append(head);const body=el('tbody','');
  for(const trade of trades){const row=el('tr',''),market=el('td',trade.market,'market');market.append(el('small',numeric(trade.opened??trade.timestamp)?new Date((trade.opened??trade.timestamp)*1000).toLocaleString():'Time unavailable'));row.append(market);const open=trade.status==='OPEN';for(const value of [open?'OPEN':'CLOSED',trade.market_result==='yes'?'YES':trade.market_result==='no'?'NO':'Pending / unknown',(trade.side??'—').toUpperCase()+' / '+(trade.bought??'—'),money(trade.entry),open?'Pending':money(trade.exit),money(trade.fees)])row.append(el('td',value));if(!open&&trade.exit_timestamp)row.children[5].append(el('small',(trade.exit_type==='settlement'?'Settled: ':'Sold / exit: ')+new Date(trade.exit_timestamp*1000).toLocaleString()));row.append(el('td',open?'Pending':signedMoney(trade.net_pnl),open?'':pnlClass(trade.net_pnl)));body.append(row);}
  table.append(body);wrap.append(table);if(!trades.length)wrap.append(el('p','No trades recorded in this view.'));$('trades').replaceChildren(wrap);
}
async function loadHistory(){const request=++historyRequest,asset=selected;historyBusy=true;$('trades').replaceChildren(el('p','Loading recorded history…'));$('history-message').textContent='';updatePagination();try{const response=await fetch('/api/history/'+asset+'?offset='+historyOffset+'&limit=25',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error('History unavailable. Try again.');const data=await response.json();if(request!==historyRequest)return;historyTotal=data.total;renderTrades(data.rows);$('history-message').textContent=data.stale?'History updates delayed.':'Available recorded history · '+historyTotal.toLocaleString()+' trades.';}catch(error){if(request===historyRequest)$('history-message').textContent=error.message;}finally{if(request===historyRequest){historyBusy=false;updatePagination();}}}
function updatePagination(){$('pagination').hidden=!historyMode;$('previous').disabled=historyBusy||historyOffset===0;$('next').disabled=historyBusy||historyOffset+25>=historyTotal;$('page-info').textContent=historyBusy?'Loading…':historyTotal?`${historyOffset+1}–${Math.min(historyOffset+25,historyTotal)} of ${historyTotal}`:'No records';}
for(const asset of symbols){const button=el('button',asset==='WTI'?'OIL':asset);button.dataset.asset=asset;button.type='button';button.addEventListener('click',()=>selectMarket(asset));$('market-tabs').append(button);}
$('browse').addEventListener('click',()=>{historyMode=!historyMode;historyOffset=0;historyRequest++;$('browse').textContent=historyMode?'Latest 5 trades':'Browse all records';$('history-message').textContent='';updatePagination();if(latest)render(latest);if(historyMode)loadHistory();});
$('previous').addEventListener('click',()=>{historyOffset=Math.max(0,historyOffset-25);loadHistory();});$('next').addEventListener('click',()=>{historyOffset+=25;loadHistory();});
async function refresh(){try{const response=await fetch('/api/view',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw new Error('Unavailable');const data=await response.json();data.stale=data.stale||!numeric(data.updated_at)||Date.now()/1000-data.updated_at>20;render(data);}catch{if(latest){latest.stale=true;render(latest);}$('connection').textContent='Updates unavailable · values may be out of date';$('connection').className='stale';}finally{setTimeout(refresh,5000);}}
setInterval(()=>{if(latest&&!latest.stale&&Date.now()/1000-latest.updated_at>20){latest.stale=true;render(latest);}},1000);
refresh();
