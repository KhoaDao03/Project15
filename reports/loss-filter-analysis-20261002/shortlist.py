import json,sqlite3,csv,math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).parent;rows=json.loads((P/'features.json').read_text());checks=json.loads((P/'entry-checks.json').read_text());allrows=json.loads((P/'trades-all.json').read_text())
with sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True) as db:orders={r['id']:r for (b,) in db.execute('select body from manual_orders') for r in [json.loads(b)]}
audit=[]
for r in rows:
 order=orders[r['buy_ids'][0]];b=checks[r['ticker']]['record']['body'];d=b['collector_record']['body'];linked=r['exact_check_id'] or d.get('decision_id')==r['decision_id']
 pre_submit=b['started_at']<=order['timing']['submitted_at']
 assert linked and pre_submit,(r['ticker'],linked,pre_submit)
 audit.append({'ticker':r['ticker'],'linked':linked,'check_before_submit':pre_submit})
def stats(rr):return {'trades':len(rr),'wins':sum(r['pnl']>0 for r in rr),'losses':sum(r['pnl']<0 for r in rr),'pnl':round(sum(r['pnl'] for r in rr),4)}
filters=[('HYPE: skip if the preceding 180-second reference return opposes the purchased side',lambda r:r['asset']=='HYPE' and r['momentum_180'] is not None and r['momentum_180']<0),('XRP: skip if >180 seconds remain and the held-side best five bid levels total <5 times planned quantity',lambda r:r['asset']=='XRP' and r['remaining']>180 and r['bid5_cover'] is not None and r['bid5_cover']<5)]
short=[];union=set()
for name,fn in filters:
 chosen=[r for r in rows if fn(r)];union.update(r['ticker'] for r in chosen);assert all(r['pnl']<0 for r in chosen)
 short.append({'rule':name,'excluded':stats(chosen),'historical_pnl_improvement':round(-sum(r['pnl'] for r in chosen),4),'earlier':stats([r for r in chosen if r['split']=='discovery']),'later':stats([r for r in chosen if r['split']=='validation']),'examples':[{k:r[k] for k in ['ticker','entry_et','side','remaining','ask','planned_qty','bid5_cover','momentum_180','pnl','source','config_hash','sigma_multiplier']} for r in chosen]})
win=[r for r in rows if r['asset']=='XRP' and r['bid5_cover'] is not None and r['bid5_cover']<5 and r['pnl']>0]
missing={k:sum(r.get(k) is None for r in rows) for k in ['ask','bid','momentum_180','bid5_cover']}
by_date={}
for r in allrows:
 date=r['entry_et'][:10];by_date.setdefault(date,[]).append(r)
coverage={date:{'all':stats(rr),'with_entry_snapshot':stats([r for r in rows if r['entry_et'][:10]==date])} for date,rr in sorted(by_date.items())}
result={'method':'Exploratory fixed-trade exclusion study. Rules selected after searching 20,527 combinations; chronological counts are not an untouched holdout. Missing features are not imputed. No later replacement buys, fill changes, sizing or portfolio feedback simulated. Money is realized net P&L including fees, not a forecast. Historical source logs retained only in part; no commodity entry snapshots matched. Order journal includes dashboard-cleared history.','sample':stats(rows),'matched_fraction':len(rows)/len(allrows),'missing_features':missing,'shortlist':short,'union_excluded':stats([r for r in rows if r['ticker'] in union]),'union_remaining':stats([r for r in rows if r['ticker'] not in union]),'xrp_liquidity_only_winners':[{k:r[k] for k in ['ticker','entry_et','remaining','bid5_cover','pnl']} for r in win],'coverage_by_date':coverage,'snapshot_link_audit':{'checked':len(audit),'all_linked_and_pre_submit':True}}
(P/'shortlist.json').write_text(json.dumps(result,indent=2))
with (P/'shortlisted-trades.csv').open('w') as f:
 rr=[r for r in rows if r['ticker'] in union];fields=['ticker','entry_et','side','remaining','ask','planned_qty','bid5_cover','momentum_180','pnl'];w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rr)
print(json.dumps({k:v for k,v in result.items() if k not in ['coverage_by_date']},indent=2))
