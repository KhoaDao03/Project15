import json,sqlite3,csv,collections
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
from zoneinfo import ZoneInfo
p=Path(Path('/tmp/project15-minute-report').read_text());tz=ZoneInfo('America/New_York');assets=['BTC','ETH','SOL','XRP','DOGE','BNB','HYPE'];bins=[('10–9 min',540,600),('9–8 min',480,540),('8–7 min',420,480)]
with sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True) as db:
 closes={ticker:json.loads(body)['close_time'] for ticker,body in db.execute('select ticker,body from live_controls')}
rows=[];missing=[];allrows=[];near=[]
for a in assets:
 for r in json.loads((p/f'{a}-history.json').read_text()):
  b=r['body'];assert b['source']=='LIVE_FALLBACK';opened=b['opened'];allrows.append((a,r));close=closes.get(r['market'])
  if close is None:missing.append(r['market']);continue
  seconds=close-opened
  for label,lo,hi in bins:
   if lo<seconds<=hi:
    row=dict(asset=a,market=r['market'],entry_time_et=datetime.fromtimestamp(opened,tz).isoformat(),seconds_remaining=seconds,window=label,contracts=b['bought'],net_pnl=str(b['net_pnl']),fees=str(b['fees']),exit_reason=b['exit_reason']);rows.append(row)
    if min(abs(seconds-x) for x in [420,480,540,600])<1:near.append(row)
assert not missing,missing

def stats(rr):
 pnl=[D(x['net_pnl']) for x in rr];return dict(trades=len(rr),wins=sum(v>0 for v in pnl),losses=sum(v<0 for v in pnl),breakeven=sum(v==0 for v in pnl),net_pnl=str(sum(pnl,D(0))),fees=str(sum((D(x['fees']) for x in rr),D(0))))
summary={label:stats([r for r in rows if r['window']==label]) for label,_,_ in bins}
result=dict(source='All currently visible completed UI trades; no archived/cleared trades',snapshot=json.loads((p/'snapshot.json').read_text()),visible_trades=len(allrows),visible_entry_start_et=datetime.fromtimestamp(min(r['body']['opened'] for a,r in allrows),tz).isoformat(),visible_entry_end_et=datetime.fromtimestamp(max(r['body']['opened'] for a,r in allrows),tz).isoformat(),matched_entry_start_et=min(r['entry_time_et'] for r in rows),matched_entry_end_et=max(r['entry_time_et'] for r in rows),boundary_rule='lower seconds excluded; upper seconds included; close time minus first filled buy order creation time',summary=summary,total=stats(rows),by_asset={a:{label:stats([r for r in rows if r['asset']==a and r['window']==label]) for label,_,_ in bins} for a in assets},near_boundary=near)
(p/'summary.json').write_text(json.dumps(result,indent=2))
with (p/'trades.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(sorted(rows,key=lambda r:r['entry_time_et']))
(p/'analyze.py').write_text(Path(__file__).read_text())
print(json.dumps({k:v for k,v in result.items() if k!='near_boundary'},indent=2))
