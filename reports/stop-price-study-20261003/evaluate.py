import json,math,statistics,csv,bisect,collections
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from btc15.strategies.settlement_edge.rules import FeeAccumulator
P=Path(__file__).parent;trades=json.loads((P/'trades.json').read_text());paths={}
for f in P.glob('*-paths.json'):paths.update(json.loads(f.read_text()))
thresholds=list(range(5,81,5));delays=[.5,1.,3.];rows=[];coverage=[];excluded=collections.Counter()
cut=datetime.fromisoformat('2026-09-25T00:00:00+00:00').timestamp()
def simulate_raw(r,pts,stop,delay):
 hit=next((x for x in pts if x[1]<=stop/100),None)
 if hit is None:return {'pnl':(r['qty'] if r['settlement']==r['side'] else 0)-r['cost']-r['buy_fees'],'stopped':False,'fill':None}
 due=hit[0]+delay
 if due>=r['close']:return {'pnl':(r['qty'] if r['settlement']==r['side'] else 0)-r['cost']-r['buy_fees'],'stopped':False,'fill':None}
 i=bisect.bisect_left([x[0] for x in pts],due)
 if i==len(pts) or pts[i][0]-due>2:return None
 fill=pts[i];q=r['qty'];proceeds=fees=0;acc=FeeAccumulator(precision='0.0001')
 for price,depth in fill[2]:
  qty=min(q,depth);q-=qty;proceeds+=qty*price;fees+=acc.charge(price,qty,.07,action='sell')
  if q<1e-8:break
 if q>1e-8:return None
 return {'pnl':proceeds-fees-r['cost']-r['buy_fees'],'stopped':True,'fill':proceeds/r['qty'],'trigger_at':hit[0],'fill_at':fill[0]}
def simulate(r,pts,stop,delay):
 v=simulate_raw(r,pts,stop,delay)
 if v is None:return None
 end=v.get('fill_at',r['close'])
 times=[r['start']]+[x[0] for x in pts if x[0]<=end]+[end]
 gap=max(b-a for a,b in zip(times,times[1:]))
 if gap>5:return None
 v['max_gap']=gap
 return v
for t,r in trades.items():
 if r['opened']<cut or r['exit_reason'] not in ['SETTLEMENT','HARD_STOP']:excluded['earlier_profit_taking_regime']+=1;continue
 pts=[x for x in paths.get(t,[]) if r['start']<=x[0]<r['close']];times=[r['start']]+[x[0] for x in pts]+[r['close']];gap=max(b-a for a,b in zip(times,times[1:]));coverage.append(dict(ticker=t,points=len(pts),max_gap=gap,actual=r['actual'],asset=r['asset']))
 if not pts:excluded['no_path']+=1;continue
 results={f'{d}:{s}':simulate(r,pts,s,d) for d in delays for s in thresholds}
 if any(results[f'{d}:{s}'] is None for d in delays for s in range(40,71,5)):excluded['incomplete_path_or_execution_40_to70']+=1;continue
 results={k:v for k,v in results.items() if v is not None}
 rows.append(dict(ticker=t,asset=r['asset'],opened=r['opened'],qty=r['qty'],actual=r['actual'],actual_exit=r['exit_reason'],settlement_winner=r['side']==r['settlement'],max_gap=gap,results=results,no_stop=(r['qty'] if r['settlement']==r['side'] else 0)-r['cost']-r['buy_fees']))
rows.sort(key=lambda r:r['opened']);split=int(len(rows)*.7)
for i,r in enumerate(rows):r['sample']='earlier70' if i<split else 'later30'
def summarize(rr,delay):
 out=[]
 for s in thresholds:
  eligible=[r for r in rr if f'{delay}:{s}' in r['results']]
  if len(eligible)!=len(rr):continue
  vv=[r['results'][f'{delay}:{s}'] for r in rr];pn=[v['pnl'] for v in vv];base=[r['results'][f'{delay}:55']['pnl'] for r in rr]
  out.append(dict(stop=s,trades=len(rr),net_pnl=round(sum(pn),4),delta_vs_modeled55=round(sum(pn)-sum(base),4),wins=sum(x>0 for x in pn),losses=sum(x<0 for x in pn),stopped=sum(v['stopped'] for v in vv),settlement_winners_stopped=sum(v['stopped'] and r['settlement_winner'] for v,r in zip(vv,rr)),actual_winners_turned_losses=sum(r['actual']>0 and v['pnl']<0 for r,v in zip(rr,vv)),avg_pnl_per_contract_cents=round(100*statistics.mean(v['pnl']/r['qty'] for r,v in zip(rr,vv)),4) if rr else None))
 return out
out={'eligible_trades':len(trades),'covered_trades':len(rows),'excluded':dict(excluded),'period_et':[datetime.fromtimestamp(rows[i]['opened'],ZoneInfo('America/New_York')).isoformat() for i in [0,-1]] if rows else [],'actual_pnl':sum(r['actual'] for r in rows),'actual_wins':sum(r['actual']>0 for r in rows),'actual_losses':sum(r['actual']<0 for r in rows),'no_stop_pnl':sum(r['no_stop'] for r in rows),'all':{str(d):summarize(rows,d) for d in delays},'holdout':{sample:summarize([r for r in rows if r['sample']==sample],1.) for sample in ['earlier70','later30']},'strict2s':summarize([r for r in rows if all(r['results'][f'1.0:{s}']['max_gap']<=2 for s in range(40,71,5))],1.),'by_asset':{a:summarize([r for r in rows if r['asset']==a],1.) for a in ['BTC','ETH','SOL','XRP','DOGE','BNB','HYPE']},'baseline_validation':{'absolute_total_error':sum(abs(r['actual']-r['results']['1.0:55']['pnl']) for r in rows),'mean_absolute_per_contract_cents':statistics.mean(100*abs(r['actual']-r['results']['1.0:55']['pnl'])/r['qty'] for r in rows) if rows else None}}
(P/'results.json').write_text(json.dumps(out,indent=2));(P/'paired-trades.json').write_text(json.dumps(rows));(P/'path-coverage.json').write_text(json.dumps(coverage))
with (P/'comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(out['all']['1.0'][0]));w.writeheader();w.writerows(out['all']['1.0'])
print(json.dumps({k:v for k,v in out.items() if k not in ['by_asset','strict2s']},indent=2))
