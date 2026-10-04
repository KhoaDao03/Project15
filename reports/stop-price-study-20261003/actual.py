import json,statistics,csv
from pathlib import Path
from datetime import datetime
P=Path(__file__).parent;rows=list(json.loads((P/'trades.json').read_text()).values());cut=datetime.fromisoformat('2026-09-25T00:00:00+00:00').timestamp();rows=[r for r in rows if r['opened']>=cut and r['exit_reason'] in ['SETTLEMENT','HARD_STOP']];paired=json.loads((P/'paired-trades.json').read_text());covered={r['ticker'] for r in paired};summary={};export=[]
for a in ['ALL','BTC','ETH','SOL','XRP','DOGE','BNB','HYPE']:
 rr=[r for r in rows if a=='ALL' or r['asset']==a];st=[r for r in rr if r['exit_reason']=='HARD_STOP'];prices=[sum(e['proceeds'] for e in r['exits'])/r['qty'] for r in st];ns=lambda r:(r['qty'] if r['side']==r['settlement'] else 0)-r['cost']-r['buy_fees']
 summary[a]={'trades':len(rr),'actual':round(sum(r['actual'] for r in rr),4),'actual_losses':sum(r['actual']<0 for r in rr),'settlement_losers':sum(r['side']!=r['settlement'] for r in rr),'stops':len(st),'stops_recover_to_win':sum(r['side']==r['settlement'] for r in st),'no_stop':round(sum(ns(r) for r in rr),4),'no_stop_delta':round(sum(ns(r)-r['actual'] for r in rr),4),'stop_mean_fill':statistics.mean(prices) if prices else None,'stop_median_fill':statistics.median(prices) if prices else None,'stop_below50':sum(p<.5 for p in prices),'stop_below40':sum(p<.4 for p in prices),'covered':sum(r['ticker'] in covered for r in rr)}
for r in rows:
 export.append({k:r[k] for k in ['ticker','asset','opened','qty','actual','exit_reason','settlement','side']}|{'no_stop_pnl':round((r['qty'] if r['side']==r['settlement'] else 0)-r['cost']-r['buy_fees'],4),'covered':r['ticker'] in covered})
with (P/'actual-trades.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(export[0]));w.writeheader();w.writerows(export)
(P/'actual-stop-analysis.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
