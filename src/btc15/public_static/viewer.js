const el=(tag,value,cls)=>{const node=document.createElement(tag);node.textContent=value??'—';if(cls)node.className=cls;return node;};
const money=value=>typeof value==='number'?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:4}).format(value):'—';
const pnlClass=value=>value>0?'positive':value<0?'negative':'';
const percent=value=>typeof value==='number'&&Number.isFinite(value)?(value*100).toFixed(1)+'%':'—';
function render(data){
  renderOwner(data);
  const connection=document.getElementById('connection');
  connection.textContent=data.stale?'Updates delayed · showing last snapshot':'Connected';
  connection.className=data.stale?'stale':'';
  document.getElementById('basis').textContent=(data.live_only?'Live':'Recorded')+' realized P&L'+(data.totals_complete?'':' · partial total');
  const total=document.getElementById('pnl');total.textContent=money(data.realized_pnl);total.className=pnlClass(data.realized_pnl);
  document.getElementById('updated').textContent='Snapshot: '+new Date(data.updated_at*1000).toLocaleString();
  const cards=data.assets.map(asset=>{
    const card=el('article',''),heading=el('div','','heading');
    heading.append(el('h3',asset.asset),el('span',asset.healthy?asset.state:'Data / trading checks pending',asset.healthy?'positive':'stale'));card.append(heading);
    const stats=el('div','','stats');
    for(const [label,value] of [['Reference',money(asset.price)],['Realized P&L',money(asset.realized_pnl)],['Wins',asset.wins],['Losses',asset.losses],['Open trades',asset.open_positions]]){
      const stat=el('div','');stat.append(el('span',label),el('strong',value));stats.append(stat);
    }card.append(stats);
    const streak=asset.current_streak,hasStreak=typeof streak==='number'&&Number.isFinite(streak);
    const performance=el('div','','stats');
    for(const [label,value] of [['Win rate',percent(asset.win_rate)],['Current win streak',hasStreak?Math.max(0,streak):'—'],['Current loss streak',hasStreak?Math.max(0,-streak):'—'],['Longest win streak',asset.longest_win_streak],['Longest loss streak',asset.longest_loss_streak]]){
      const stat=el('div','');stat.append(el('span',label),el('strong',value));performance.append(stat);
    }card.append(performance);
    for(const market of asset.markets)card.append(el('p',market.ticker+(market.fresh?'':' · stale market data')));
    card.append(el('h4','Latest 5 trades'+(asset.trades_stale?' · updates delayed':'')));
    const wrap=el('div','','table-wrap'),table=el('table',''),head=el('thead',''),headers=el('tr','');
    for(const label of ['Market / time','Status','Market settled','Side / quantity','Entry','Exit','Fees','Net P&L'])headers.append(el('th',label));head.append(headers);table.append(head);
    const body=el('tbody','');
    for(const trade of asset.trades){
      const row=el('tr',''),market=el('td',trade.market,'market');
      market.append(el('small',new Date((trade.opened??trade.timestamp)*1000).toLocaleString()));row.append(market);
      const open=trade.status==='OPEN';
      const settled=trade.market_result==='yes'?'YES':trade.market_result==='no'?'NO':'Pending / unknown';
      for(const value of [open?'OPEN':'CLOSED',settled,(trade.side??'—').toUpperCase()+' / '+(trade.bought??'—'),money(trade.entry),open?'Pending':money(trade.exit),money(trade.fees)])row.append(el('td',value));
      row.append(el('td',open?'Pending':money(trade.net_pnl),open?'':pnlClass(trade.net_pnl)));body.append(row);
    }table.append(body);wrap.append(table);card.append(wrap);
    if(!asset.trades.length)card.append(el('p','No trades recorded yet.'));
    return card;
  });document.getElementById('assets').replaceChildren(...cards);
}
async function refresh(){
  try{const response=await fetch('/api/view',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw new Error('Unavailable');render(await response.json());}
  catch{if(ownerLatest)ownerLatest.stale=true;updateLocks();const status=document.getElementById('connection');status.textContent='Updates unavailable · displayed values may be out of date';status.className='stale';}
  finally{setTimeout(refresh,5000);}
}
refresh();


let ownerToken='', ownerDeadline=0, ownerLatest=null, ownerBusy=false, ownerBlocked=true;
let assetStopStates={};
const stopBlocked=asset=>['stopping','stopped','unknown'].includes(assetStopStates[asset]?.status);
let draftRevision=-1, draftTicker='', draftDirty=false;
const stopMessage=document.getElementById('stop-message');
const liveMessage=document.getElementById('live-message');
function unlocked(){return ownerToken&&performance.now()<ownerDeadline;}
function updateLocks(){
  if(!unlocked()){ownerToken='';document.getElementById('unlock-status').textContent='Locked · enter the passcode to make changes.';}
  else document.getElementById('unlock-status').textContent='Unlocked · '+Math.ceil((ownerDeadline-performance.now())/1000)+' seconds remaining';
  document.getElementById('live-fields').disabled=!unlocked()||ownerBusy||ownerBlocked||!ownerLatest||ownerLatest.stale||!ownerLatest.live_available||!draftTicker||stopBlocked(document.getElementById('live-asset').value);
  const selected=document.getElementById('stop-asset').value;
  document.getElementById('asset-stop-fields').disabled=!unlocked()||ownerBusy||ownerBlocked||stopBlocked(selected);
  document.getElementById('asset-stop-message').textContent=assetStopStates[selected]?.message||'';
  document.getElementById('stop-fields').disabled=!unlocked()||ownerBusy||ownerBlocked;
}
function renderOwner(data){
  ownerLatest=data;
  const asset=data.assets.find(a=>a.asset===document.getElementById('live-asset').value);
  if(!asset){draftTicker='';updateLocks();return;}
  const policy=asset.live_policy||{},revision=policy.revision??0;
  document.getElementById('live-current').textContent='Saved: '+(policy.enabled?'new buys ON':'new buys OFF')+' · '+(policy.contracts??'not set')+' contracts per entry';
  if(policy.loss_guard&&!policy.enabled&&revision>draftRevision){draftDirty=false;document.getElementById('live-enabled').checked=false;}
  if(policy.loss_guard)document.getElementById('live-current').textContent+=' · '+policy.loss_guard.reason;
  if(!draftDirty&&revision>=draftRevision){
    draftRevision=revision;draftTicker=asset.markets.find(m=>m.fresh)?.ticker||'';
    document.getElementById('live-enabled').checked=policy.enabled===true;
    document.getElementById('live-contracts').value=policy.contracts??10;
  }updateLocks();
}
document.getElementById('live-asset').addEventListener('change',()=>{draftDirty=false;draftRevision=-1;document.getElementById('live-confirm').checked=false;if(ownerLatest)renderOwner(ownerLatest);});
for(const id of ['live-enabled','live-contracts'])document.getElementById(id).addEventListener('input',()=>{draftDirty=true;document.getElementById('live-confirm').checked=false;});
async function ownerPost(path,body){
  if(!unlocked()){updateLocks();throw new Error('Enter the passcode again.');}
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+ownerToken},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  const data=await response.json();
  if(response.status===401){ownerToken='';updateLocks();}
  if(!response.ok)throw new Error(data.detail||'Action failed. Refresh before retrying.');
  return data;
}
document.getElementById('unlock-form').addEventListener('submit',async event=>{
  event.preventDefault();if(ownerBusy)return;
  const input=document.getElementById('owner-passcode'),passcode=input.value;input.value='';
  ownerBusy=true;ownerToken='';updateLocks();
  const started=performance.now();
  try{
    const response=await fetch('/api/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode}),signal:AbortSignal.timeout(10000)});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||'Unlock failed');
    ownerToken=data.token;ownerDeadline=started+data.expires_in*1000;
  }catch(error){liveMessage.textContent=error.message;}
  finally{ownerBusy=false;updateLocks();}
});
document.getElementById('live-form').addEventListener('submit',async event=>{
  event.preventDefault();if(ownerBusy)return;
  if(!document.getElementById('live-confirm').checked){liveMessage.textContent='Confirm the real-money settings before saving.';return;}
  const enabled=document.getElementById('live-enabled').checked;
  const payload={asset:document.getElementById('live-asset').value,ticker:draftTicker,revision:draftRevision,enabled,contracts:Number(document.getElementById('live-contracts').value),confirm:enabled?'ENABLE_REAL_TRADING':''};
  ownerBusy=true;updateLocks();
  try{
    const result=await ownerPost('/api/control',payload);draftDirty=false;draftRevision=result.revision;
    liveMessage.textContent='Saved: '+result.contracts+' contracts per entry. '+(result.enabled?'New live buys enabled.':'New buys disabled; automatic exits continue.');
  }catch(error){liveMessage.textContent=error.message+' Refresh and review the saved settings before retrying.';draftDirty=false;draftRevision=-1;}
  finally{ownerBusy=false;document.getElementById('live-confirm').checked=false;updateLocks();}
});
async function refreshStop(){
  try{
    const response=await fetch('/api/stop',{cache:'no-store',signal:AbortSignal.timeout(8000)});
    if(!response.ok)throw new Error('Unavailable');const state=await response.json();
    document.getElementById('owner-section').hidden=!state.enabled;
    assetStopStates=state.assets||{};
    ownerBlocked=['stopping','stopped','unknown'].includes(state.status);
    if(state.message)stopMessage.textContent=state.message;
  }catch{ownerBlocked=true;stopMessage.textContent='Shutdown status unavailable. Check the private dashboard.';}
  finally{updateLocks();setTimeout(refreshStop,2000);}
}
document.getElementById('stop-form').addEventListener('submit',async event=>{
  event.preventDefault();if(ownerBusy)return;ownerBusy=true;updateLocks();
  try{const data=await ownerPost('/api/stop',{confirm:document.getElementById('stop-confirm').checked});ownerBlocked=true;stopMessage.textContent=data.message;}
  catch(error){stopMessage.textContent=error.message;}
  finally{ownerBusy=false;document.getElementById('stop-confirm').checked=false;updateLocks();}
});
setInterval(updateLocks,250);
refreshStop();

document.getElementById('stop-asset').addEventListener('change',()=>{document.getElementById('asset-stop-confirm').checked=false;updateLocks();});
document.getElementById('asset-stop-form').addEventListener('submit',async event=>{
  event.preventDefault();if(ownerBusy)return;
  const asset=document.getElementById('stop-asset').value;
  ownerBusy=true;updateLocks();
  try{assetStopStates[asset]=await ownerPost('/api/stop',{asset,confirm:document.getElementById('asset-stop-confirm').checked});}
  catch(error){assetStopStates[asset]={status:'failed',message:error.message};}
  finally{ownerBusy=false;document.getElementById('asset-stop-confirm').checked=false;updateLocks();}
});
