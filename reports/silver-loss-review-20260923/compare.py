"""Fixed observed-trade exclusions, not strategy replay or an optimized policy."""
import json
from pathlib import Path

P=Path(__file__).parent
checks=json.loads((P/'entry-checks.json').read_text())
trades={r['ticker']:r for r in json.loads((P/'trades.json').read_text())}
rows=[]
for ticker, records in checks.items():
    accepted=[x['record'] for x in records if x['record']['body'].get('accepted') and x['record']['body'].get('collector_record')]
    record=min(accepted,key=lambda r:abs(r['processed_at']-trades[ticker]['opened']))
    b=record['body']; d=b['collector_record']['body']; book=b['book_snapshot']['market']['book']; side=trades[ticker]['side']
    row={k:trades[ticker][k] for k in ['ticker','pnl','opened_utc','entry']}
    row.update(confidence=d['conservative_probability'],ask=book[side+'_ask'],spread=book[side+'_ask']-book[side+'_bid'],remaining=d['seconds_remaining'],net_ev=d['net_ev'],bid_qty=book[side+'_top5'][0][1],model_age=record['processed_at']-d['model_evaluated_at'],sigma_multiplier=d['probability'].get('sigma_multiplier'),decision_id=d.get('decision_id'))
    rows.append(row)
def summary(rs):
    return dict(n=len(rs),wins=sum(r['pnl']>0 for r in rs),losses=sum(r['pnl']<0 for r in rs),pnl=round(sum(r['pnl'] for r in rs),4))
filters={'baseline':lambda r:True,'EV >= 0':lambda r:r['net_ev']>=0,'EV >= .01':lambda r:r['net_ev']>=.01}
for v in [.85,.86,.88,.9]:filters[f'confidence >= {v}']=lambda r,v=v:r['confidence']>=v
for v in [.01,.02,.03]:filters[f'spread <= {v}']=lambda r,v=v:r['spread']<=v+1e-9
for v in [15,30,60,120]:filters[f'remaining > {v}s']=lambda r,v=v:r['remaining']>v
filters['confidence >= .85 AND spread <= .02']=lambda r:r['confidence']>=.85 and r['spread']<=.02000001
filters['confidence >= .85 AND remaining >30s']=lambda r:r['confidence']>=.85 and r['remaining']>30
results={k:summary([r for r in rows if fn(r)]) for k,fn in filters.items()}
(P/'entry-comparison.json').write_text(json.dumps(dict(method='Nearest accepted recorded live entry check to actual buy request. Fixed actual trades, no replacement entries or portfolio feedback; model EV is uncalibrated.',rows=rows,filters=results),indent=2)+'\n')
print(json.dumps(results,indent=2))
