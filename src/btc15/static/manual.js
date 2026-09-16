'use strict';
(()=>{
  const el=id=>document.getElementById('manual-'+id);
  let context=null, prepared=null, busy=false, blocked=true, contextVersion=0,ticketGeneration=0;
  const unresolved=row=>['submitting','accepted','unknown'].includes(row.state);
  const amount=v=>v==null?'—':'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4});
  const cents=v=>v==null?'—':(Number(v)*100).toFixed(2)+'¢';
  function message(value){el('message').textContent=value;}
  function controls(){
    for(const action of ['buy','sell'])el(action).disabled=busy||blocked||!context||context.ticker!==el('market').value;
    el('confirm').disabled=busy||blocked||!prepared;
    for(const id of ['market','side','count','price','refresh'])el(id).disabled=busy;
  }
  function invalidate(){prepared=null;el('review').hidden=true;controls();}
  async function api(path,options={}){
    const response=await fetch('/api/manual/'+path,{cache:'no-store',signal:AbortSignal.timeout(30000),...options});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Request could not be validated');
    return data;
  }
  let balanceBusy=false,balanceTimer=null;
  async function refreshRealBalance(){
    if(balanceBusy)return;
    balanceBusy=true;clearTimeout(balanceTimer);
    document.getElementById('real-account').hidden=false;
    const button=document.getElementById('real-balance-refresh');button.disabled=true;
    try{
      const data=await api('balance');
      document.getElementById('real-balance').textContent=amount(data.available_cash_dollars);
      document.getElementById('real-balance-status').textContent='Kalshi primary account · cash only · updated '+new Date(data.server_time*1000).toLocaleTimeString();
    }catch(e){
      document.getElementById('real-balance').textContent='—';
      document.getElementById('real-balance-status').textContent=e.message;
    }finally{
      balanceBusy=false;button.disabled=false;
      balanceTimer=setTimeout(refreshRealBalance,30000);
    }
  }
  document.getElementById('real-balance-refresh').onclick=refreshRealBalance;
  if(typeof fleetMode!=='undefined'&&fleetMode)refreshRealBalance();
  else window.addEventListener('fleet-ready',refreshRealBalance,{once:true});
  function quoteText(){
    if(!context)return;
    const side=el('side').value,q=context.quotes[side];
    el('account').textContent='Real balance '+amount(context.balance_cents/100)+' · Held: '+context.holdings.yes+' YES / '+context.holdings.no+' NO';
    el('quotes').textContent=side.toUpperCase()+' bid '+cents(q.bid)+' · ask '+cents(q.ask)+' · snapshot '+new Date(context.server_time*1000).toLocaleTimeString();
  }
  async function activity(){
    blocked=true;controls();
    const data=await api('status');
    blocked=!data.configured||data.orders.some(unresolved);
    const root=el('orders');root.replaceChildren();
    if(!data.configured)message('Configure Kalshi API credentials on the server before trading.');
    if(!data.orders.length)root.append(document.createTextNode('No real orders submitted from this dashboard.'));
    for(const row of data.orders.slice(0,10)){
      const div=document.createElement('div');div.className='manual-order';
      const title=document.createElement('strong');title.textContent=row.request.action.toUpperCase()+' '+row.request.count+' '+row.request.side.toUpperCase()+' · '+row.request.ticker;
      const detail=document.createElement('p'),order=row.exchange_order;
      detail.textContent=row.state.toUpperCase()+' · '+(row.message||'Awaiting confirmation')+(order?' · Filled '+order.fill_count_fp+' / '+row.request.count:'')+(row.fill_notification?' · Fill stream: '+row.fill_notification.count_fp+' / '+row.request.count:'');
      const id=document.createElement('small');id.textContent='Order reference '+row.id;
      div.append(title,detail,id);
      if(unresolved(row)){
        const button=document.createElement('button');button.type='button';button.textContent='Check order status';
        button.onclick=async()=>{button.disabled=true;try{await api('orders/'+row.id);await activity();await loadContext();}catch(e){message(e.message);button.disabled=false;}};
        div.append(button);
      }
      root.append(div);
    }
    controls();
  }
  async function loadContext(){
    const version=++contextVersion;context=null;invalidate();
    el('account').textContent='Checking real account and current contract…';el('quotes').textContent='';
    try{
      const ticker=el('market').value;
      if(!ticker)throw Error('No current contract available. Close and reopen when a contract is available.');
      const data=await api('market?ticker='+encodeURIComponent(ticker));
      if(version!==contextVersion)return;
      context=data;quoteText();controls();
    }catch(e){if(version===contextVersion){el('account').textContent='Account/market unavailable';message(e.message);}}
  }
  window.openManualTicket=async(requestedTicker=null,quick=null)=>{
    if(busy)return;
    const ticket=++ticketGeneration;
    const previous=el('market').value,selected=requestedTicker||previous;
    context=null;++contextVersion;blocked=true;invalidate();
    el('market').replaceChildren();
    for(const market of window.manualMarketChoices||[]){
      const option=document.createElement('option');option.value=market.ticker;option.textContent=market.asset+' · '+market.ticker;el('market').append(option);
    }
    const available=[...el('market').options].some(o=>o.value===selected);
    if(available)el('market').value=selected;
    if(el('market').value!==previous)el('price').value='';
    el('dialog').showModal();message('');invalidate();
    if(requestedTicker&&!available){el('market').value='';el('account').textContent='Contract unavailable';message('This contract is no longer available. Close the ticket and select the current contract.');return;}
    if(quick){el('side').value=quick.side;el('count').value=quick.count;el('price').value='';}
    try{
      await activity();
      if(ticket!==ticketGeneration||!el('dialog').open)return;
      await loadContext();
      if(ticket!==ticketGeneration||!el('dialog').open)return;
      if(quick&&context&&!blocked){
        const ask=context.quotes[quick.side].ask;
        if(quick.action==='buy'&&ask==null){message('No ask is available for this side. Refresh before buying.');return;}
        el('price').value=quick.action==='sell'?'1':'95';
        el(quick.action).click();
      }
    }catch(e){message(e.message);}
  };
  el('open').onclick=()=>window.openManualTicket();
  el('close').onclick=()=>el('dialog').close();
  el('dialog').addEventListener('close',()=>{++ticketGeneration;++contextVersion;invalidate();});
  el('form').onsubmit=e=>e.preventDefault();
  el('market').onchange=()=>{++ticketGeneration;el('price').value='';loadContext();};
  el('side').onchange=()=>{++ticketGeneration;invalidate();el('price').value='';quoteText();};
  for(const id of ['count','price'])el(id).oninput=()=>{++ticketGeneration;invalidate();};
  el('refresh').onclick=async()=>{message('');try{await activity();await loadContext();}catch(e){message(e.message);}};
  el('cancel').onclick=invalidate;
  for(const action of ['buy','sell'])el(action).onclick=()=>{
    if(!context||busy||blocked)return;
    if(Date.now()/1000-context.server_time>30){message('Refresh account and quotes before reviewing an order.');return;}
    if(!el('price').value){
      const quote=context.quotes[el('side').value][action==='buy'?'ask':'bid'];
      if(action==='buy')el('price').value='95';
      else if(quote!=null)el('price').value=(Number(quote)*100).toFixed(2);
    }
    if(!el('form').reportValidity())return;
    const count=Number(el('count').value),limit=Number(el('price').value),side=el('side').value;
    if(action==='sell'&&count>Number(context.holdings[side])){message('Sell quantity exceeds your real '+side.toUpperCase()+' holdings.');return;}
    prepared={client_order_id:crypto.randomUUID(),ticker:context.ticker,action,side,count,limit_cents:limit,confirm:'REAL_MONEY'};
    el('review-text').textContent=action.toUpperCase()+' '+count+' '+side.toUpperCase()+' on '+context.ticker+' at '+limit+'¢ '+(action==='buy'?'or lower. Maximum purchase cost ':'or higher. Minimum proceeds if all fill ')+amount(count*limit/100)+' before fees. Real money · '+(action==='buy'?'Cheapest available offers up to this maximum; all '+count+' contracts fill or none.':'Partial fills possible; remainder cancels.');
    el('review').hidden=false;message('');controls();
  };
  el('confirm').onclick=async()=>{
    if(!prepared||busy||blocked)return;
    if(Date.now()/1000-context.server_time>30){invalidate();message('Review expired. Refresh account and quotes.');return;}
    const order=prepared;busy=true;controls();message('Submitting real order…');
    try{
      const result=await api('orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(order)});
      message(result.state.toUpperCase()+' · '+(result.message||''));
    }catch(e){message('Order outcome may be unconfirmed. '+e.message+' Check manual order activity before trying again.');}
    finally{
      busy=false;invalidate();refreshRealBalance();
      try{await activity();await loadContext();}catch(e){blocked=true;controls();message('Could not refresh order activity. Reopen the ticket and check status before submitting another order.');}
    }
  };
})();
