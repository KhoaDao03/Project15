import json,statistics,collections,csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).parent
rs=sorted(json.loads((P/'features.json').read_text()),key=lambda r:r['opened']);checks=json.loads((P/'entry-checks.json').read_text());now=1791239553.8285236
for r in rs:
 b=checks[r['ticker']]['record']['body'];pol=r['buy_timing'].get('backlog_entry_policy') or {};r['bypass']=bool(b.get('backlog_entry_bypass') or pol.get('initial') or pol.get('submission'));r['stop_price']=b['control'].get('stop_price');r['date']=datetime.fromtimestamp(r['opened'],ZoneInfo('America/New_York')).isoformat();r['research_bad']=bool((b.get('health') or {}).get('status',{}).get('research_logging',{}).get('error'))
def summary(rr):
 return dict(n=len(rr),wins=sum(r['pnl']>0 for r in rr),losses=sum(r['pnl']<0 for r in rr),pnl=round(sum(r['pnl'] for r in rr),4))
print('BASE',summary(rs),'exact',sum(r['exact_check_id'] for r in rs))
for k in sorted(set(r['date'][:10] for r in rs)):print('DAY',k,summary([r for r in rs if r['date'].startswith(k)]))
rules={'early >7min':lambda r:r['remaining']>420,'last 60sec':lambda r:r['remaining']<=60,'last 120sec':lambda r:r['remaining']<=120,'price >=94c':lambda r:r['entry']>=.94-1e-9,'price >=95c':lambda r:r['entry']>=.95-1e-9,'confidence <88%':lambda r:r['confidence']<.88,'confidence <90%':lambda r:r['confidence']<.90,'negative edge':lambda r:r['edge']<0,'momentum60 against':lambda r:r['momentum_60']<0,'momentum180 against':lambda r:r['momentum_180']<0,'bid5 cover <1':lambda r:r['bid5_cover']<1,'bid5 cover <2':lambda r:r['bid5_cover']<2,'bid5 cover <5':lambda r:r['bid5_cover']<5,'book age >1sec':lambda r:r['book_age']>1,'model age >1sec':lambda r:r['model_age']>1,'backlog bypass':lambda r:r['bypass'],'research error':lambda r:r['research_bad']}
results=[]
for name,pred in rules.items():
 removed=[];missing=0
 for r in rs:
  try:
   if pred(r):removed.append(r)
  except TypeError:missing+=1
 x=dict(rule=name,removed=summary(removed),improvement=round(-sum(r['pnl'] for r in removed),4),first48=summary([r for r in removed if r['opened']<now-86400]),last24=summary([r for r in removed if r['opened']>=now-86400]),missing=missing,tickers=[r['ticker'] for r in removed]);results.append(x);print('FILTER',json.dumps({k:v for k,v in x.items() if k!='tickers'}))
stops=[]
for r in rs:
 for e in r['exits']:
  if e['reason']!='HARD_STOP':continue
  t=e['timing'];st=dict(ticker=r['ticker'],pnl=r['pnl'],stop=r['stop_price'],bid=t.get('stop_bid'),fill=e['proceeds']/e['qty'],qty=e['qty'],recovered=r['side']==r['settlement'])
  for k,a,b in [('quote_age','stop_detected_at','quote_received_at'),('publish_lag','quote_published_at','quote_received_at'),('detect_submit','submitted_at','stop_detected_at'),('detect_ack','acknowledged_at','stop_detected_at')]:st[k]=t[a]-t[b] if t.get(a) and t.get(b) else None
  stops.append(st)
print('STOPS',len(stops),'recovered',sum(s['recovered'] for s in stops))
for k in ['fill','bid','quote_age','publish_lag','detect_submit','detect_ack']:
 vs=sorted(s[k] for s in stops if s[k] is not None);print('STOPSTAT',k,'mean',statistics.mean(vs),'median',statistics.median(vs),'p90',vs[int(.9*(len(vs)-1))],'max',max(vs))
print('FILL below40',sum(s['fill']<.4 for s in stops),'below stop',sum(s['fill']<s['stop']-1e-8 for s in stops),'below bid',sum(s['fill']<s['bid']-1e-8 for s in stops),'bid versus fill dollars',sum((s['bid']-s['fill'])*s['qty'] for s in stops))
for r in sorted(rs,key=lambda r:r['pnl'])[:10]: print('WORST',json.dumps({k:r[k] for k in ['ticker','date','pnl','entry','remaining','confidence','edge','momentum_60','momentum_180','bid5_cover','book_age','model_age','bypass','stop_price']}))
for outcome in [True,False]:
 rr=[r for r in rs if (r['pnl']>0)==outcome];print('OUTCOME',outcome,'n',len(rr),'avgpnl',statistics.mean(r['pnl'] for r in rr),'qty',collections.Counter(r['qty'] for r in rr))
(P/'filter-results.json').write_text(json.dumps(results,indent=2));(P/'stop-analysis.json').write_text(json.dumps(stops,indent=2));(P/'features.json').write_text(json.dumps(rs,indent=2))
keys=['ticker','date','side','qty','entry','pnl','remaining','confidence','edge','momentum_60','momentum_180','bid5_cover','book_age','model_age','bypass','stop_price','settlement']
with (P/'trade-analysis.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(rs)
