const $=id=>document.getElementById(id);
const el=(tag,value,cls)=>{const node=document.createElement(tag);node.textContent=value??'—';if(cls)node.className=cls;return node;};
const numeric=value=>typeof value==='number'&&Number.isFinite(value);
const money=value=>numeric(value)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(value):'—';
const signed=value=>numeric(value)?(value>0?'+':'')+money(value):'—';
const percent=value=>numeric(value)?(value*100).toFixed(1)+'%':'—';
const count=value=>numeric(value)?value.toLocaleString('en-US'):'—';
const pnlClass=value=>value>0?'positive':value<0?'negative':'';
const symbols=['BTC','ETH','SOL','XRP','BNB','HYPE','DOGE','GOLD','SILVER','WTI'];
const name=asset=>asset==='WTI'?'OIL / WTI':asset;
const zone='America/New_York';
const timestamp=value=>numeric(value)?new Date(value*1000).toLocaleString('en-US',{timeZone:zone}):'—';
const dateLabel=value=>new Date(value+'T12:00:00Z').toLocaleDateString('en-US',{timeZone:'UTC',month:'short',day:'numeric',year:'numeric'});
const shiftDay=(day,amount)=>{const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+amount);return d.toISOString().slice(0,10);};
const theme=matchMedia('(prefers-color-scheme: dark)');
function updateIcon(){$('tab-icon').href=theme.matches?'/logo.svg':'/section-logo.svg';}
theme.addEventListener('change',updateIcon);updateIcon();
let performanceData=null,selectedDay='',comparisonDay='',tradeAsset='BTC',tradeBasis='closed',offset=0,totalTrades=0,tradesBusy=false,tradeRequest=0,performanceBusy=false,shownTradeKey='',chartSeries=[];
function totals(assets){
  const result={};
  for(const key of ['realized_pnl','completed_trades','wins','losses','breakeven_trades','opened_trades','open_positions','unknown_pnl']){
    result[key]=symbols.every(a=>numeric(assets[a]?.[key]))?symbols.reduce((sum,a)=>sum+assets[a][key],0):null;
  }
  result.win_rate=result.completed_trades>0&&numeric(result.wins)&&result.unknown_pnl===0?result.wins/result.completed_trades:null;
  return result;
}
function dayAssets(day){return performanceData.days.find(d=>d.date===day)?.assets||{};}
function difference(a,b){return numeric(a)&&numeric(b)?a-b:null;}
function metric(label,value,note='',cls=''){
  const box=el('div','','metric');box.append(el('small',label),el('strong',value,cls));if(note)box.append(el('small',note));return box;
}
function cumulative(day){let sum=0;for(const d of performanceData.days){if(d.date>day)break;const pnl=totals(d.assets).realized_pnl;if(!numeric(pnl))return null;sum+=pnl;}return sum;}
function renderPerformance(){
  const data=performanceData,all=totals(data.all_time),day=totals(dayAssets(selectedDay)),previous=totals(dayAssets(comparisonDay));
  $('performance-content').hidden=false;
  const warnings=[];
  if(data.stale)warnings.push('History updates delayed. Showing the last available records.');
  if(data.missing_assets.length)warnings.push('Incomplete market history: '+data.missing_assets.join(', ')+'. Totals are unavailable.');
  if(all.unknown_pnl>0)warnings.push(all.unknown_pnl+' completed trades have unavailable P&L. Affected P&L totals and win rates are unavailable.');
  if(data.excluded_undated_trades)warnings.push(data.excluded_undated_trades+' completed trades have no usable closure date. They are included in all-time results but excluded from daily results.');
  $('performance-message').textContent=warnings.join(' ');
  $('connection').textContent=data.stale?'History delayed':'History connected';$('connection').className=data.stale?'stale':'';
  $('updated').textContent=timestamp(data.updated_at);
  $('all-time-summary').replaceChildren(metric('Realized P&L',signed(all.realized_pnl),'After recorded fees',pnlClass(all.realized_pnl)),metric('Completed trades',count(all.completed_trades)),metric('Wins / losses',count(all.wins)+' / '+count(all.losses)),metric('Overall win rate',percent(all.win_rate)),metric('Open trades',count(all.open_positions),'Current history snapshot'),metric('History starts',dateLabel(data.days[0].date),'New York dates'));
  for(const id of ['selected-day','compare-day']){$(id).min=data.days[0].date;$(id).max=data.today;}
  $('selected-day').value=selectedDay;$('compare-day').value=comparisonDay;
  for(const [kind,value] of [['selected',selectedDay],['compare',comparisonDay]]){$(kind+'-jump-value').textContent=dateLabel(value);$(kind+'-jump').setAttribute('aria-label',(kind==='selected'?'Selected day':'Comparison day')+': '+dateLabel(value)+'. Open calendar');}
  renderDateStrip('selected',selectedDay);renderDateStrip('compare',comparisonDay);
  $('previous-day').disabled=selectedDay<=data.days[0].date;$('next-day').disabled=selectedDay>=data.today;
  $('day-state').textContent=dateLabel(selectedDay)+(selectedDay===data.today?' · In progress':' · Recorded results')+' compared with '+dateLabel(comparisonDay)+(comparisonDay===data.today?' · In progress':'');
  const delta=difference(day.realized_pnl,previous.realized_pnl);
  $('daily-summary').replaceChildren(metric('Comparison-day realized P&L',signed(previous.realized_pnl),dateLabel(comparisonDay),pnlClass(previous.realized_pnl)),metric('Selected-day realized P&L',signed(day.realized_pnl),dateLabel(selectedDay),pnlClass(day.realized_pnl)),metric('Change between days',signed(delta),'Selected day minus comparison day',pnlClass(delta)),metric('Cumulative through selected day',signed(cumulative(selectedDay)),count(day.completed_trades)+' closed · '+count(day.opened_trades)+' opened',pnlClass(cumulative(selectedDay))));
  $('market-dates').textContent='Selected: '+dateLabel(selectedDay)+' · Comparison: '+dateLabel(comparisonDay)+' · America/New_York';
  const daily=dayAssets(selectedDay),compare=dayAssets(comparisonDay);
  const rows=symbols.map(asset=>marketRow(asset,data.all_time[asset]||{},daily[asset]||{},compare[asset]||{}));
  $('performance-markets').replaceChildren(...rows);$('performance-totals').replaceChildren(marketRow(null,all,day,previous));
  let running=0,prior=null;
  const series=[];
  const historyRows=data.days.map(d=>{const stats=totals(d.assets);running=numeric(running)&&numeric(stats.realized_pnl)?running+stats.realized_pnl:null;series.push({date:d.date,value:running});const row=el('tr','',d.date===selectedDay?'selected':''),cell=el('td',''),button=el('button',dateLabel(d.date)+(d.date===data.today?' · In progress':''));button.type='button';button.setAttribute('aria-pressed',String(d.date===selectedDay));button.addEventListener('click',()=>chooseDay(d.date));row.addEventListener('click',event=>{if(!event.target.closest('button'))chooseDay(d.date);});cell.append(button);row.append(cell,el('td',signed(stats.realized_pnl),pnlClass(stats.realized_pnl)),el('td',signed(difference(stats.realized_pnl,prior)),pnlClass(difference(stats.realized_pnl,prior))),el('td',signed(running),pnlClass(running)),el('td',count(stats.completed_trades)));prior=stats.realized_pnl;return row;});
  $('daily-log').replaceChildren(...historyRows.reverse());renderChart(series);
}
function marketRow(asset,all,day,compare){
  const row=el('tr','',asset===tradeAsset?'selected':''),cell=el('td','');
  if(asset){const button=el('button',name(asset),'market-button');button.type='button';button.setAttribute('aria-pressed',String(asset===tradeAsset));button.addEventListener('click',()=>{tradeAsset=asset;$('trade-asset').value=asset;offset=0;renderPerformance();loadTrades();});row.addEventListener('click',event=>{if(!event.target.closest('button'))button.click();});cell.append(button);}else cell.textContent='TOTAL';
  row.append(cell);
  const delta=difference(day.realized_pnl,compare.realized_pnl);
  for(const [value,cls] of [[signed(all.realized_pnl),pnlClass(all.realized_pnl)],[count(all.completed_trades)],[percent(all.win_rate)],[signed(day.realized_pnl),pnlClass(day.realized_pnl)],[signed(compare.realized_pnl),pnlClass(compare.realized_pnl)],[signed(delta),pnlClass(delta)],[count(day.completed_trades)],[count(day.wins)+' / '+count(day.losses)],[percent(day.win_rate)]])row.append(el('td',value,cls));
  return row;
}
let inspectedChartDate='';
function renderChart(series){
  chartSeries=series;
  const points=series.filter(p=>numeric(p.value));
  if(!points.length||points.length!==series.length){$('pnl-chart').replaceChildren(el('p','Chart unavailable while market totals are incomplete.','muted'));return;}
  const width=Math.max(300,Math.min(900,$('pnl-chart').clientWidth)),height=280,left=76,right=width-16,top=40,bottom=212;
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('role','group');svg.setAttribute('tabindex','0');svg.setAttribute('aria-label','Daily cumulative P&L chart. Use left and right arrow keys to inspect dates, and Enter to select a day.');svg.setAttribute('aria-describedby','chart-readout');
  const node=(tag,attrs,text)=>{const n=document.createElementNS(svg.namespaceURI,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,value);if(text)n.textContent=text;svg.append(n);return n;};
  const low=Math.min(0,...points.map(p=>p.value));let high=Math.max(0,...points.map(p=>p.value));if(high===low)high=low+1;
  const x=i=>points.length===1?(left+right)/2:left+i/(points.length-1)*(right-left),y=v=>bottom-(v-low)/(high-low)*(bottom-top);
  const tickMoney=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:Math.abs(value)>=10000?'compact':'standard',maximumFractionDigits:2}).format(value);
  node('text',{x:0,y:18,class:'pnl-axis-title'},'Cumulative P&L (USD)');
  for(let i=0;i<3;i++){const value=low+(high-low)*i/2,py=y(value);node('line',{x1:left,x2:right,y1:py,y2:py,class:'pnl-grid'});node('text',{x:left-8,y:py+4,'text-anchor':'end',class:'pnl-label'},tickMoney(value));}
  node('line',{x1:left,x2:left,y1:top,y2:bottom,class:'pnl-axis'});node('line',{x1:left,x2:right,y1:bottom,y2:bottom,class:'pnl-axis'});
  node('polyline',{points:points.map((p,i)=>x(i)+','+y(p.value)).join(' '),class:'pnl-line'});
  for(const i of new Set([0,...(width>500?[Math.floor((points.length-1)/2)]:[]),points.length-1])){
    node('line',{x1:x(i),x2:x(i),y1:bottom,y2:bottom+5,class:'pnl-axis'});
    node('text',{x:x(i),y:235,'text-anchor':points.length===1?'middle':i===0?'start':i===points.length-1?'end':'middle',class:'pnl-label'},new Date(points[i].date+'T12:00:00Z').toLocaleDateString('en-US',{timeZone:'UTC',month:'short',day:'numeric'}));
  }
  node('text',{x:(left+right)/2,y:264,'text-anchor':'middle',class:'pnl-axis-title'},'Date (America/New_York)');
  points.forEach((p,i)=>node('circle',{cx:x(i),cy:y(p.value),r:3,class:'pnl-dot'}));
  const guide=node('line',{x1:left,x2:left,y1:top,y2:bottom,class:'pnl-guide'}),dot=node('circle',{cx:left,cy:bottom,r:5,class:'pnl-active-dot'});
  const hit=node('rect',{x:left-10,y:top-10,width:right-left+20,height:bottom-top+20,fill:'transparent',class:'pnl-hit'});
  const readout=el('div','','chart-readout');readout.id='chart-readout';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');
  const detail=el('div',''),date=el('strong',''),values=el('span','');detail.append(date,values);const select=el('button','View this day');select.type='button';readout.append(detail,select);
  let index=Math.max(0,points.findIndex(p=>p.date===(inspectedChartDate||selectedDay)));
  function inspect(i){index=Math.max(0,Math.min(points.length-1,i));const point=points[index];inspectedChartDate=point.date;guide.setAttribute('x1',x(index));guide.setAttribute('x2',x(index));dot.setAttribute('cx',x(index));dot.setAttribute('cy',y(point.value));date.textContent=dateLabel(point.date)+(point.date===performanceData.today?' · In progress':'');values.textContent='Cumulative: '+signed(point.value)+' USD · Day: '+signed(totals(dayAssets(point.date)).realized_pnl)+' USD';select.setAttribute('aria-label','View trades and performance for '+dateLabel(point.date));}
  function pointerIndex(event){const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;const matrix=svg.getScreenCTM();if(!matrix)return index;const px=point.matrixTransform(matrix.inverse()).x;return points.length===1?0:Math.round((px-left)/(right-left)*(points.length-1));}
  hit.addEventListener('pointermove',event=>{const i=Math.max(0,Math.min(points.length-1,pointerIndex(event)));if(i!==index)inspect(i);});
  hit.addEventListener('click',event=>{inspect(pointerIndex(event));chooseDay(points[index].date);});
  svg.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End','Enter',' '].includes(event.key)){event.preventDefault();if(event.key==='Enter'||event.key===' '){chooseDay(points[index].date);$('pnl-chart').querySelector('svg').focus({preventScroll:true});}else inspect(event.key==='Home'?0:event.key==='End'?points.length-1:index+(event.key==='ArrowLeft'?-1:1));}});
  select.addEventListener('click',()=>chooseDay(points[index].date));inspect(index);
  $('pnl-chart').replaceChildren(svg,readout,el('p','Hover to inspect · Tap a point or choose “View this day” to select it · Arrow keys move between dates','chart-help'));
}
let calendarKind='selected',calendarMonth='',calendarFocus='';
function openCalendar(kind){
  calendarKind=kind;calendarFocus=kind==='selected'?selectedDay:comparisonDay;calendarMonth=calendarFocus.slice(0,7)+'-01';
  $('calendar-purpose').textContent=kind==='selected'?'Selected day':'Compare with';renderCalendar();$('date-calendar').showModal();
  $('calendar-days').querySelector(`[data-date="${calendarFocus}"]`)?.focus();
}
function renderCalendar(){
  const first=performanceData.days[0].date,last=performanceData.today,current=calendarKind==='selected'?selectedDay:comparisonDay;
  const month=new Date(calendarMonth+'T12:00:00Z'),year=month.getUTCFullYear(),index=month.getUTCMonth(),length=new Date(Date.UTC(year,index+1,0)).getUTCDate();
  $('calendar-month').textContent=month.toLocaleDateString('en-US',{timeZone:'UTC',month:'long',year:'numeric'});
  $('calendar-month-prev').disabled=calendarMonth.slice(0,7)<=first.slice(0,7);$('calendar-month-next').disabled=calendarMonth.slice(0,7)>=last.slice(0,7);
  const cells=[];
  for(let i=0;i<month.getUTCDay();i++){const blank=el('span','');blank.setAttribute('aria-hidden','true');cells.push(blank);}
  for(let n=1;n<=length;n++){
    const day=calendarMonth.slice(0,8)+String(n).padStart(2,'0'),button=el('button',String(n));button.type='button';button.dataset.date=day;button.disabled=day<first||day>last;
    button.setAttribute('aria-label',dateLabel(day));button.setAttribute('aria-pressed',String(day===current));if(day===last)button.setAttribute('aria-current','date');
    button.addEventListener('click',()=>pickCalendarDay(day));
    button.addEventListener('keydown',event=>{const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];if(delta===undefined)return;event.preventDefault();const next=shiftDay(day,delta);if(next<first||next>last)return;calendarFocus=next;if(next.slice(0,7)!==calendarMonth.slice(0,7)){calendarMonth=next.slice(0,7)+'-01';renderCalendar();}$('calendar-days').querySelector(`[data-date="${next}"]`)?.focus();});
    cells.push(button);
  }
  $('calendar-days').replaceChildren(...cells);
}
function pickCalendarDay(day){
  $('date-calendar').close();if(calendarKind==='selected')chooseDay(day);else{comparisonDay=day;renderPerformance();}$(calendarKind+'-jump').focus({preventScroll:true});
}
for(const kind of ['selected','compare'])$(kind+'-jump').addEventListener('click',()=>openCalendar(kind));
for(const id of ['calendar-close','calendar-cancel'])$(id).addEventListener('click',()=>$('date-calendar').close());
$('calendar-today').addEventListener('click',()=>pickCalendarDay(performanceData.today));
for(const [direction,delta] of [['prev',-1],['next',1]])$('calendar-month-'+direction).addEventListener('click',()=>{const d=new Date(calendarMonth+'T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+delta);calendarMonth=d.toISOString().slice(0,10);renderCalendar();});
$('date-calendar').addEventListener('click',event=>{if(event.target!==$('date-calendar'))return;const r=$('date-calendar').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('date-calendar').close();});
const dateWindows={selected:null,compare:null};
function renderDateStrip(kind,value,paging=false){
  const first=performanceData.days[0].date,last=performanceData.today,latestStart=shiftDay(last,-6);
  let start=dateWindows[kind];
  if(!start||(!paging&&$(kind+'-strip').dataset.value!==value&&(value<start||value>shiftDay(start,6))))start=shiftDay(value,-3);
  start=start<first?first:start;
  if(start>latestStart)start=latestStart<first?first:latestStart;
  dateWindows[kind]=start;
  const strip=$(kind+'-strip'),signature=[start,last,value].join(':');
  $(kind+'-strip-prev').disabled=start<=first;$(kind+'-strip-next').disabled=shiftDay(start,6)>=last;
  strip.dataset.value=value;
  if(strip.dataset.signature===signature)return;
  strip.dataset.signature=signature;
  const buttons=[];
  for(let i=0;i<7;i++){
    const day=shiftDay(start,i);if(day>last)break;
    const button=el('button','','date-chip');button.type='button';button.dataset.date=day;
    button.setAttribute('aria-label',dateLabel(day)+(day===last?' · Today':''));button.setAttribute('aria-pressed',String(day===value));
    const date=new Date(day+'T12:00:00Z');
    button.append(el('small',date.toLocaleDateString('en-US',{timeZone:'UTC',weekday:'short'})),el('strong',String(date.getUTCDate())),el('small',date.toLocaleDateString('en-US',{timeZone:'UTC',month:'short'})+(day===last?' · Today':'')));
    button.addEventListener('click',()=>{if(kind==='selected')chooseDay(day);else{comparisonDay=day;renderPerformance();}strip.querySelector('[aria-pressed=true]')?.focus({preventScroll:true});});buttons.push(button);
  }
  const left=strip.scrollLeft;strip.replaceChildren(...buttons);strip.scrollLeft=left;
  if(!paging){const active=strip.querySelector('[aria-pressed=true]');if(active){const left=active.offsetLeft-strip.offsetLeft;if(left<strip.scrollLeft)strip.scrollLeft=left;else if(left+active.offsetWidth>strip.scrollLeft+strip.clientWidth)strip.scrollLeft=left+active.offsetWidth-strip.clientWidth;}}
}
for(const kind of ['selected','compare'])for(const [direction,amount] of [['prev',-7],['next',7]])$(kind+'-strip-'+direction).addEventListener('click',()=>{dateWindows[kind]=shiftDay(dateWindows[kind],amount);renderDateStrip(kind,kind==='selected'?selectedDay:comparisonDay,true);});
function chooseDay(day){
  if(!performanceData||!day||day<performanceData.days[0].date||day>performanceData.today){$('selected-day').value=selectedDay;return;}
  selectedDay=day;comparisonDay=day>performanceData.days[0].date?shiftDay(day,-1):day;offset=0;renderPerformance();loadTrades();
}
function pagination(){$('day-trades-prev').disabled=tradesBusy||offset===0;$('day-trades-next').disabled=tradesBusy||offset+25>=totalTrades;$('day-trades-page').textContent=tradesBusy?'Loading…':totalTrades?`${offset+1}–${Math.min(offset+25,totalTrades)} of ${totalTrades}`:'No records';}
async function loadTrades(){
  const request=++tradeRequest,asset=tradeAsset,day=selectedDay,basis=tradeBasis;tradesBusy=true;const key=[asset,day,basis,offset].join(':');if(key!==shownTradeKey){$('day-trades').replaceChildren();shownTradeKey=key;}$('day-trades-message').textContent='Loading trades…';$('day-trades-title').textContent=name(asset)+' · '+dateLabel(day);$('day-trades-scope').textContent=(basis==='closed'?'Trades closed on this day; these contribute to daily realized P&L.':'Trades opened on this day; later closes contribute to their closure day’s P&L.')+' All times America/New_York.';pagination();
  try{
    const response=await fetch('/api/history/'+asset+'?'+new URLSearchParams({day,basis,offset,limit:25}),{cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw Error(response.status===429?'History is busy. Try selecting the day again shortly.':'Trade history unavailable. Try selecting the day again.');
    const data=await response.json();if(request!==tradeRequest)return;totalTrades=data.total;
    const rows=data.rows.map(trade=>{const row=el('tr','');for(const value of [trade.market,timestamp(trade.opened),timestamp(trade.exit_timestamp),trade.status,(trade.side||'—').toUpperCase()+' / '+count(trade.bought??trade.quantity),money(trade.entry),trade.status==='OPEN'?'Pending':money(trade.exit),money(trade.fees)])row.append(el('td',value));row.append(el('td',trade.status==='OPEN'?'Pending':signed(trade.net_pnl),trade.status==='OPEN'?'':pnlClass(trade.net_pnl)));return row;});
    $('day-trades').replaceChildren(...rows);$('day-trades-message').textContent=(data.stale?'History updates delayed. ':'')+(data.total?'':'No trades '+(basis==='closed'?'closed':'opened')+' on this day.');
  }catch(error){if(request===tradeRequest){totalTrades=0;$('day-trades-message').textContent=error.message;}}
  finally{if(request===tradeRequest){tradesBusy=false;pagination();}}
}
async function refreshPerformance(){
  if(performanceBusy)return;performanceBusy=true;
  try{const response=await fetch('/api/performance',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw Error('Performance history is unavailable. Please retry.');const data=await response.json();if(!data.days?.length)throw Error('No recorded history is available yet.');performanceData=data;if(!selectedDay){selectedDay=data.today;comparisonDay=selectedDay>data.days[0].date?shiftDay(selectedDay,-1):selectedDay;}renderPerformance();$('retry-performance').hidden=true;await loadTrades();}
  catch(error){$('performance-message').textContent=error.message+(performanceData?' Previously loaded results may be out of date.':'');$('connection').textContent='History unavailable';$('connection').className='stale';$('retry-performance').hidden=false;}
  finally{performanceBusy=false;}
}
for(const asset of symbols){const option=el('option',name(asset));option.value=asset;$('trade-asset').append(option);}
$('selected-day').addEventListener('change',()=>chooseDay($('selected-day').value));
$('compare-day').addEventListener('change',()=>{const day=$('compare-day').value;if(day&&day>=performanceData.days[0].date&&day<=performanceData.today){comparisonDay=day;renderPerformance();}else $('compare-day').value=comparisonDay;});
$('previous-day').addEventListener('click',()=>chooseDay(shiftDay(selectedDay,-1)));$('next-day').addEventListener('click',()=>chooseDay(shiftDay(selectedDay,1)));$('today').addEventListener('click',()=>chooseDay(performanceData.today));
$('trade-asset').addEventListener('change',()=>{tradeAsset=$('trade-asset').value;offset=0;renderPerformance();loadTrades();});$('trade-basis').addEventListener('change',()=>{tradeBasis=$('trade-basis').value;offset=0;loadTrades();});
$('day-trades-prev').addEventListener('click',()=>{offset=Math.max(0,offset-25);loadTrades();});$('day-trades-next').addEventListener('click',()=>{offset+=25;loadTrades();});$('retry-performance').addEventListener('click',refreshPerformance);
window.addEventListener('resize',()=>{if(chartSeries.length)renderChart(chartSeries);});
refreshPerformance();setInterval(refreshPerformance,60000);
