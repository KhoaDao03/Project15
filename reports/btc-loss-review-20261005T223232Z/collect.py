import json,httpx,sqlite3,time,collections
from pathlib import Path
from datetime import datetime
P=Path(__file__).parent;assets=['BTC']
with sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True) as db:
 db.execute('BEGIN');orders=[json.loads(b) for b, in db.execute('select body from manual_orders')];closes={t:json.loads(b)['close_time'] for t,b in db.execute('select ticker,body from live_controls')}
by=collections.defaultdict(list)
for o in orders:by[o['request']['ticker']].append(o)
settles={}
for m in json.loads(Path('data/cloud/manifest.json').read_text()):
 if m['asset'] not in assets:continue
 with sqlite3.connect(f"file:data/cloud/{m['data_dir']}/paper.db?mode=ro",uri=True) as db:
  for t,b in db.execute("select market,body from records where kind='settlement'"):settles[t]=json.loads(b)['result']
trades={};excluded=collections.Counter();latencies=[]
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=120) as c:
 for a in assets:
  rows=[]
  for offset in range(0,100000,500):
   r=c.get(f'/assets/{a}/api/records',params={'kind':'trade_result','limit':500,'offset':offset});r.raise_for_status();d=r.json();rows+=d['rows']
   if len(rows)>=d['total']:break
  (P/f'{a}-ui-history.json').write_text(json.dumps(rows))
  for r in rows:
   t=r['market'];b=r['body'];rr=by[t];buys=[x for x in rr if x['request']['action']=='buy' and float((x.get('exchange_order') or {}).get('fill_count_fp',0))>0];sells=[x for x in rr if x['request']['action']=='sell' and float((x.get('exchange_order') or {}).get('fill_count_fp',0))>0]
   if b.get('source')!='LIVE_FALLBACK' or len(buys)!=1 or t not in closes or settles.get(t) not in ['yes','no']:excluded['not_single_buy_or_missing_settlement']+=1;continue
   first=buys[0];o=first['exchange_order'];start=first.get('timing',{}).get('acknowledged_at',b['opened']+1)
   if start>=closes[t]:excluded['entry_after_close']+=1;continue
   fees=sum(float(o.get(k,0)) for k in ['maker_fees_dollars','taker_fees_dollars']);exits=[]
   for x in sells:
    tm=x.get('timing',{});det=tm.get('decision_detected_at') or tm.get('decision_at');ack=tm.get('acknowledged_at')
    if det and ack:latencies.append(ack-det)
    exits.append({'reason':x.get('automation_reason'),'qty':float(x['exchange_order']['fill_count_fp']),'proceeds':float(x['exchange_order']['fill_count_fp'])-sum(float(x['exchange_order'].get(k,0)) for k in ['maker_fill_cost_dollars','taker_fill_cost_dollars']),'timing':tm})
   trades[t]=dict(ticker=t,asset=a,side=b['side'],opened=b['opened'],start=start,close=closes[t],qty=b['bought'],cost=b['cost'],buy_fees=fees,actual=b['net_pnl'],settlement=settles[t],exit_reason=b['exit_reason'],exits=exits)
  print(a,len(rows),flush=True)
(P/'trades.json').write_text(json.dumps(trades));(P/'collection.json').write_text(json.dumps({'at':time.time(),'excluded':dict(excluded),'eligible':len(trades),'exit_latencies':latencies}));print('eligible',len(trades),'excluded',excluded,flush=True)
