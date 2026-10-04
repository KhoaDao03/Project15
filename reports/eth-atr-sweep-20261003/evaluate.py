"""Exploratory sampled-book ATR sweep at fixed current ETH rules."""
import bisect, collections, csv, gzip, json, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from btc15.strategies.settlement_edge.bleep import safety_clamp
from btc15.strategies.settlement_edge.rules import FeeAccumulator

P=Path(__file__).parent
SETTLES=json.loads((P/'settlements.json').read_text())
MULTS=[round(x/100,2) for x in range(50,201,5)]
def probability(p,m,op):
    if p['remaining_samples']==0:return p['p_yes']
    z=math.copysign(p['safety_ratio'],p['base_p_up']-.5)*p['sigma_multiplier']/m
    t=1/(1+.2316419*abs(z))
    tail=math.exp(-.5*z*z)/math.sqrt(2*math.pi)*t*(.319381539+t*(-.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))))
    base=1-tail if z>=0 else tail
    up=min(.98,max(.02,(1-p['indicator_weight'])*base+p['indicator_weight']*min(.95,max(.05,.5+.12*p['lean']))))
    if p['safety_clamp_enabled']:up=safety_clamp(up,abs(z),.78)
    return up if op in ['>=','>'] else 1-up

def execute(levels,buy=False):
    left=10;value=fee=0;acc=FeeAccumulator(precision='0.0001')
    for p,q in levels:
        price=round(1-p,8) if buy else p
        used=min(q,left);left-=used;value+=used*price
        fee+=acc.charge(price,used,.07,action='buy' if buy else 'sell')
        if left<1e-8:return value,fee
    return None

excluded=collections.Counter();selected={};validation=collections.Counter();max_error=0
with gzip.open(P/'observations.jsonl.gz','rt') as f:
    for line in f:
        g=json.loads(line);ticker=g['ticker'];close=g['close'];start=close-600
        if ticker not in SETTLES:excluded['missing_settlement']+=1;continue
        ev=sorted([r for r in g['eval'] if start<=r['t']<close-1],key=lambda r:r['t'])
        books=sorted([r for r in g['book'] if start-5<=r[0]<close],key=lambda r:r[0])
        valid=[b for b in books if b[1]]
        if not ev or not valid:excluded['empty_window']+=1;continue
        egap=max(b-a for a,b in zip([start]+[r['t'] for r in ev],[r['t'] for r in ev]+[close-1]))
        bgap=max(b-a for a,b in zip([start]+[r[0] for r in valid],[r[0] for r in valid]+[close]))
        # Full common entry-to-close window: no cherry-picking paths by outcome or multiplier.
        if egap>10 or bgap>5:excluded['incomplete_window']+=1;continue
        overlapping_gap=False
        for gap in g['gaps']:
            body=gap['body'];lo=body.get('previous_capture_at');hi=body.get('next_capture_at')
            if lo is None or hi is None or (lo<=close and hi>=start-3600):overlapping_gap=True;break
        if overlapping_gap:excluded['recording_gap_in_window_or_warmup']+=1;continue
        if any(not b[1] for b in books if b[0]>=start):excluded['invalid_book_in_window']+=1;continue
        eligible=[]
        for r in ev:
            if set(r['codes'])-{'MIN_PROBABILITY','SPREAD','ENTRY_WINDOW'}:continue
            p=r['p'];side=r['side'];b=r['book']
            if not p or p.get('model')!='bleep-reference-atr-finish-v5' or side not in ['yes','no'] or not b:continue
            err=abs(probability(p,p['sigma_multiplier'],r['op'])-p['p_yes']);max_error=max(max_error,err);validation['checked']+=1
            if err>1e-8:validation['errors']+=1;continue
            ask=b.get(side+'_ask');bid=b.get(side+'_bid')
            if ask is None or bid is None or not .8<=ask<=.96 or bid>ask or (r['quality'] or 0)<85:continue
            threshold=.85 if close-r['t']>420 else .83
            cap=min(.98,max(.02,min(.99,max(.01,(bid+ask)/2))+.06))
            if cap+1e-12<threshold:continue
            r['threshold']=threshold;eligible.append(r)
        g={'ticker':ticker,'session':g['session'],'close':close,'eval':eligible,'books':valid,'times':[b[0] for b in valid],'eval_gap':egap,'book_gap':bgap}
        # Choose by coverage alone if sessions overlap.
        if ticker not in selected or (bgap,egap)<(selected[ticker]['book_gap'],selected[ticker]['eval_gap']):selected[ticker]=g

def trade(g,m,delay=1.,stop=.5):
    books=g['books'];times=g['times'];close=g['close']
    for r in g['eval']:
        py=probability(r['p'],m,r['op']);side=r['side'];conf=py if side=='yes' else 1-py
        if conf+1e-12<r['threshold']:continue
        i=bisect.bisect_left(times,max(r['t'],r['observed'])+delay)
        if i>=len(books) or times[i]>=close-1 or times[i]-(max(r['t'],r['observed'])+delay)>2:continue
        own=2 if side=='yes' else 3;opp=3 if side=='yes' else 2
        b=books[i]
        if not b[opp] or not b[own]:continue
        ask=round(1-b[opp][0][0],8);bid=b[own][0][0]
        if not .8<=ask<=.96 or bid>ask or min(.98,(bid+ask)/2+.06)+1e-12<r['threshold']:continue
        entry=execute(b[opp],True)
        if entry is None or entry[0]/10>.96:continue
        cost,buyfee=entry;exit_at=close;stopped=False;sellfee=0
        proceeds=10 if SETTLES[g['ticker']]==side else 0
        for j in range(i,len(books)):
            held=books[j][own]
            if held and held[0][0]>stop:
                continue
            k=bisect.bisect_left(times,times[j]+delay)
            if k>=len(books):break
            if times[k]-(times[j]+delay)>2:return {'unavailable':True}
            sell=execute(books[k][own])
            if sell is None:return {'unavailable':True}
            proceeds,sellfee=sell;exit_at=times[k];stopped=True;break
        return {'ticker':g['ticker'],'atr':m,'entry_at':times[i],'exit_at':exit_at,'side':side,'entry_price':cost/10,'pnl':proceeds-cost-buyfee-sellfee,'fees':buyfee+sellfee,'stopped':stopped,'session':g['session']}
    return None

rows=[];alltrades=[];common=[]
for g in sorted(selected.values(),key=lambda g:g['close']):
    variants={(m,d,s):trade(g,m,d,s) for m in MULTS for d,s in [(1.,.5),(.5,.5),(3.,.5),(1.,.55)]}
    if any(r and r.get('unavailable') for r in variants.values()):excluded['incomplete_execution_any_candidate']+=1;continue
    common.append(g)
    for (m,d,s),r in variants.items():
        if r:alltrades.append(r|{'delay':d,'stop':s})
split=sorted(g['close'] for g in common)[int(.7*len(common))] if common else 0
def summarize(rr):
    rr=sorted(rr,key=lambda r:r['exit_at']);equity=peak=mdd=0
    for r in rr:
        equity+=r['pnl'];peak=max(peak,equity);mdd=max(mdd,peak-equity)
    return {'trades':len(rr),'pnl':round(equity,4),'max_drawdown':round(mdd,4),'wins':sum(r['pnl']>1e-8 for r in rr),'losses':sum(r['pnl']< -1e-8 for r in rr),'stops':sum(r['stopped'] for r in rr)}
for m in MULTS:
    rr=[r for r in alltrades if r['atr']==m and r['delay']==1 and r['stop']==.5]
    rows.append({'atr':m,**summarize(rr),'earlier70':summarize([r for r in rr if r['exit_at']<split]),'later30':summarize([r for r in rr if r['exit_at']>=split]),'delay05':summarize([r for r in alltrades if r['atr']==m and r['delay']==.5]),'delay3':summarize([r for r in alltrades if r['atr']==m and r['delay']==3]),'stop55':summarize([r for r in alltrades if r['atr']==m and r['stop']==.55])})
rows.sort(key=lambda r:(-r['pnl'],r['max_drawdown'],r['atr']))
et=lambda t:datetime.fromtimestamp(t,ZoneInfo('America/New_York')).isoformat()
summary={'ranking':rows,'common_markets':len(common),'coverage_eligible':len(selected),'excluded_session_windows':dict(excluded),'reconstruction':dict(validation),'max_reconstruction_error':max_error,'window_et':[et(min(g['close'] for g in common)-600),et(max(g['close'] for g in common))] if common else [],'split_et':et(split),'quantity':10,'stop':.5,'delay':1,'candidate_range':[.5,2,.05]}
(P/'results.json').write_text(json.dumps(summary,indent=2))
(P/'coverage.json').write_text(json.dumps([{k:g[k] for k in ['ticker','session','close','eval_gap','book_gap']} for g in common],indent=2))
with (P/'trades.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(alltrades[0]));w.writeheader();w.writerows(alltrades)
print(json.dumps(summary,indent=2))
