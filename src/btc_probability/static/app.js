'use strict';
const $ = id => document.getElementById(id);
let allMarkets=[], rows=[], selectedKey='', playing=false, cursor=0;
const number = x => x===null || x===undefined || !Number.isFinite(Number(x)) ? null : Number(x);
const fmt = (x,n=2) => number(x)===null ? 'unavailable' : Number(x).toFixed(n);
async function get(url){const r=await fetch(url);if(!r.ok)throw new Error(`Read failed: HTTP ${r.status}`);return r.json();}
function options(el,values){const prior=el.value;el.replaceChildren(...values.map(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;return o;}));if(values.includes(prior))el.value=prior;}
function value(f){if($('model').value==='research') return f.research_settlement?.result ? f.research_settlement.result.p_yes*f.discount*100 : null;return $('model').value==='terminal' ? (f.terminal ? f.terminal.value_yes*100 : null) : f.display_cents;}
function draw(data){
 const canvas=$('chart'),r=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
 canvas.width=r.width*dpr;canvas.height=r.height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
 const w=r.width,h=r.height,L=45,R=95,T=18,B=35,pw=w-L-R,ph=h-T-B;
 ctx.font='11px system-ui';ctx.fillStyle='#8fa3bd';
 for(let p=0;p<=100;p+=20){let y=T+ph*(1-p/100);ctx.strokeStyle='#233245';ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();ctx.fillText(String(p),8,y+4);}
 if(!data.length)return;
 let dollars=data.flatMap(f=>[number(f.reference?.value),number(f.target)]).filter(x=>x!==null);
 let lo=Math.min(...dollars),hi=Math.max(...dollars);if(!dollars.length){lo=0;hi=1;}let pad=Math.max((hi-lo)*.12,1);lo-=pad;hi+=pad;
 for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;ctx.fillText('$'+v.toFixed(2),w-R+10,T+ph*(1-i/4)+4);}
 let t0=data[0].forecast_time,t1=Math.max(t0+1,data.at(-1).forecast_time);
 for(let i=0;i<=4;i++){const x=L+pw*i/4,t=t0+(t1-t0)*i/4;ctx.fillText(new Date(t*1000).toISOString().slice(11,19),Math.min(w-70,Math.max(0,x-24)),h-9);}
 const steps=data.slice(1).map((r,i)=>r.forecast_time-data[i].forecast_time).filter(t=>t>0).sort((a,b)=>a-b);
 const syntheticGap=Math.max(3,(steps[Math.floor(steps.length/2)]||15)*1.5);
 function line(extract,color,usd=false){ctx.strokeStyle=color;ctx.lineWidth=1.8;ctx.beginPath();let previous=null;
 for(const f of data){const v=number(extract(f));if(v===null){previous=null;continue;}const x=L+pw*(f.forecast_time-t0)/(t1-t0),y=T+ph*(1-(usd?(v-lo)/(hi-lo):v/100));
 const maxGap=f.mode==='synthetic'?syntheticGap:3;
 if(previous===null || f.forecast_time-previous>maxGap)ctx.moveTo(x,y);else ctx.lineTo(x,y);previous=f.forecast_time;}
 ctx.stroke();}
 line(value,'#559bff');line(f=>number(f.quotes[$('quote').value])===null?null:Number(f.quotes[$('quote').value])*100,'#46d798');
 line(f=>f.reference?.value,'#ffa347',true);line(f=>f.target,'#ff5c70',true);
}
function render(){
 const replay=$('view').value==='replay';$('playback').hidden=!replay;
 const idx=replay?Math.min(cursor,rows.length-1):rows.length-1;
 $('cursor').max=Math.max(0,rows.length-1);$('cursor').value=Math.max(0,idx);
 const f=rows[idx];draw(rows.slice(Math.max(0,idx-1000),idx+1));if(!f){$('value').textContent='UNAVAILABLE';$('status').textContent='Waiting for this selection';$('explanation').textContent='No recorded forecast loaded.';return;}
 $('value').textContent=number(value(f))===null?'UNAVAILABLE':fmt(value(f))+'¢';
 $('provenance').textContent=$('model').value==='research'?'ASSUMPTION-BASED settlement research · user tie rule · uncalibrated':$('model').value==='terminal'?'Terminal diagnostic; not settlement probability':f.display_provenance;
 $('remaining').textContent=fmt(f.remaining_seconds,1)+'s';
 const research=$('model').value==='research', researchAvailable=research && f.research_settlement?.available===true;
 const researchReasons=[f.research_settlement?.reason,...(f.volatility?.reasons||[]),...(f.reasons||[]).filter(x=>x!=='ROUNDING_TIE_UNSPECIFIED')].filter(Boolean);
 $('status').textContent=researchAvailable?'RESEARCH AVAILABLE · ASSUMPTION-BASED · UNCALIBRATED':research?'RESEARCH UNAVAILABLE'+(researchReasons.length?' · '+[...new Set(researchReasons)].join(', '):' · No research result recorded'):f.readiness+(f.reasons.length?' · '+f.reasons.join(', '):'');
 $('count').textContent=`${f.known_samples} / ${f.expected_samples}`;$('ages').textContent=`Reference ${fmt(f.input_ages.reference,1)}s · Quote ${fmt(f.quotes.age,1)}s`;
 $('explanation').textContent=research?(researchAvailable?'Research uses the user-defined model-lean tie rule. ':'No assumption-based settlement result is available for this recorded forecast. ')+f.explanation:f.explanation;$('clock').textContent=new Date(f.forecast_time*1000).toISOString();
 $('freshness').textContent=replay?'REPLAY — only inputs received by this recorded forecast were used.':`Latest recorded forecast: ${new Date(f.forecast_time*1000).toISOString()}. ${f.mode==='live'&&Date.now()/1000-f.forecast_time>5?'STALE recording — collector may be stopped.':''}`;
 $('diagnostics').textContent=JSON.stringify({verification:f.verification_readiness,inputs:f.input_readiness,model:f.model_readiness,research_diagnostic:f.research_diagnostic,research_settlement:f.research_settlement,provider:f.volatility.provider,annualized_IV:f.volatility.implied_sigma,realized:f.volatility.realized_sigma,effective_sigma:f.volatility.sigma,source_expiry:f.volatility.source_expiry,reference_source:f.reference?.source,missing_samples:f.missing_samples,numerical_95_interval:f.settlement?.numerical_interval,precision_met:f.settlement?.precision_met,sensitivity:f.sensitivity,calibrated_probability:f.calibrated_p_yes,discount:f.discount,flags:f.volatility.reasons},null,2);
 $('raw').textContent=JSON.stringify(f,null,2);
}
async function refresh(){try{
 const m=await get('/api/markets');allMarkets=m.markets;$('mode').textContent=($('view').value==='replay'?'REPLAY / ':'')+m.mode.toUpperCase()+' · READ ONLY';
 options($('market'),[...new Set(allMarkets.map(x=>x.market))]);if($('view').value==='latest' && allMarkets.length)$('market').value=allMarkets[0].market;options($('provider'),[...new Set(allMarkets.filter(x=>x.market===$('market').value).map(x=>x.provider))]);
 if(!selectedKey && m.default_provider)$('provider').value=m.default_provider;
 const key=$('market').value+'|'+$('provider').value;if(key!==selectedKey){rows=[];cursor=0;selectedKey=key;}
 if($('market').value){const fresh=await get('/api/forecasts?'+new URLSearchParams({market:$('market').value,provider:$('provider').value,after:rows.at(-1)?.seq||0}));rows.push(...fresh);}
 $('error').textContent='';render();
 }catch(e){$('error').textContent=e.message+'; existing chart is stale.';}}
$('market').addEventListener('change',()=>{$('view').value='replay';refresh();});
for(const id of ['provider','model','quote','view'])$(id).addEventListener('change',()=>refresh());
$('cursor').addEventListener('input',()=>{cursor=Number($('cursor').value);render();});$('play').onclick=()=>{playing=!playing;$('play').textContent=playing?'Pause replay':'Play replay';};
setInterval(()=>{if(playing&&$('view').value==='replay'){cursor=Math.min(cursor+1,rows.length-1);render();}},250);
window.addEventListener('resize',render);refresh();setInterval(refresh,2000);
