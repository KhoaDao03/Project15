import json,csv,sqlite3,collections,datetime,pathlib
from decimal import Decimal as D
from zoneinfo import ZoneInfo
P=pathlib.Path(__file__).parent;window=json.loads((P/'window.json').read_text());start,end=window['start'],window['end'];tz=ZoneInfo('America/New_York')
def stamp(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def local(t):return datetime.datetime.fromtimestamp(t,tz).isoformat()
c=sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True);c.execute('BEGIN');orders=[json.loads(r[0]) for r in c.execute('select body from manual_orders')];closes={t:json.loads(b)['close_time'] for t,b in c.execute('select ticker,body from live_controls')};c.close()
buys=collections.defaultdict(list)
for r in orders:
 o=r.get('exchange_order') or {};q=D(o.get('fill_count_fp') or '0')
 if r.get('origin')=='bot' and r['request']['action']=='buy' and q>0:buys[r['request']['ticker']].append(r)
manifest=json.load(open('data/cloud/manifest.json'));ui={};settles={};sources=collections.Counter()
for m in manifest:
 a=m['asset'];rows=json.loads((P/f'{a}-history.json').read_text())
 for r in rows:
  if r['market'] in ui:raise ValueError('Duplicate UI market')
  ui[r['market']]=(a,r)
 db=sqlite3.connect(f"file:data/cloud/{m['data_dir']}/paper.db?mode=ro",uri=True)
 for ticker,raw in db.execute("select market,body from records where kind='market' and timestamp>=?",(start-900,)):
  b=json.loads(raw);close=b.get('raw',{}).get('close_time')
  if close:
   close=stamp(close)
   if ticker in closes:assert abs(close-closes[ticker])<.001
   closes[ticker]=close
 for ticker,raw in db.execute("select market,body from records where kind='settlement'"):
  b=json.loads(raw);settles[ticker]=b['result']
 db.close()
rows=[];missing=[];allrecent=0;unmatched_recent=[]
for ticker,bb in buys.items():
 opened=min(stamp(r['exchange_order']['created_time']) if r['exchange_order'].get('created_time') else r['created_at'] for r in bb)
 if not start<=opened<end:continue
 allrecent+=1
 if ticker not in ui:unmatched_recent.append(ticker)
 if ticker not in closes:missing.append(ticker);continue
 remaining=closes[ticker]-opened
 if not 420<remaining<=600:continue
 if ticker not in ui:raise ValueError('Missing early UI trade '+ticker)
 a,r=ui[ticker];b=r['body'];sources[b.get('source')]+=1
 quantity=sum((D(x['exchange_order']['fill_count_fp']) for x in bb),D(0));cost=sum((D(x['exchange_order'].get(k) or '0') for x in bb for k in ['maker_fill_cost_dollars','taker_fill_cost_dollars']),D(0))
 assert abs(quantity-D(str(b['bought'])))<D('.00001');assert abs(cost-D(str(b['cost'])))<D('.00001')
 # Sum exchange-confirmed sells/fees independently of dashboard reporting.
 proceeds=D(0);fees=D(0);sold=D(0)
 for x in orders:
  if x['request'].get('ticker')!=ticker:continue
  o=x.get('exchange_order') or {};q=D(o.get('fill_count_fp') or '0')
  if not q:continue
  fees+=sum((D(o.get(k) or '0') for k in ['maker_fees_dollars','taker_fees_dollars']),D(0))
  if x['request']['action']=='sell':
   assert o['outcome_side']!=b['side'];sold+=q;proceeds+=q-sum((D(o.get(k) or '0') for k in ['maker_fill_cost_dollars','taker_fill_cost_dollars']),D(0))
 closed=b.get('status')=='CLOSED' and b.get('net_pnl') is not None and r['timestamp']<=end
 if closed:
  if quantity>sold:
   assert ticker in settles
   proceeds+=(quantity-sold)*D(int(settles[ticker]==b['side']))
  net=proceeds-cost-fees
  assert abs(net-D(str(b['net_pnl'])))<D('.00001'),(ticker,net,b['net_pnl'])
  assert abs(fees-D(str(b['fees'])))<D('.00001')
 else:net=None
 rows.append(dict(asset=a,market=ticker,entry_time_et=local(opened),entry_timestamp=opened,seconds_remaining=remaining,side=b['side'],contracts=str(quantity),cost=str(cost),fees=str(fees),net_pnl=str(net) if net is not None else '',status='CLOSED' if closed else 'OPEN_AT_CUTOFF',exit_reason=b.get('exit_reason',''),close_timestamp=closes[ticker]))
assert not missing,missing
summary=[]
for a in [m['asset'] for m in manifest]:
 rr=[r for r in rows if r['asset']==a and r['status']=='CLOSED'];pnl=[D(r['net_pnl']) for r in rr];summary.append(dict(asset=a,trades=len(rr),wins=sum(v>0 for v in pnl),losses=sum(v<0 for v in pnl),breakeven=sum(v==0 for v in pnl),net_pnl=str(sum(pnl,D(0))),fees=str(sum((D(r['fees']) for r in rr),D(0))),open=sum(r['asset']==a and r['status']!='CLOSED' for r in rows)))
with (P/'trades.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(sorted(rows,key=lambda r:r['entry_timestamp']))
result=dict(start_et=local(start),end_et=local(end),rule='420 < market close minus first filled buy order exchange creation <= 600 seconds',summary=summary,total_net_pnl=str(sum((D(r['net_pnl']) for r in rows if r['status']=='CLOSED'),D(0))),all_recent_filled_bot_markets=allrecent,journal_markets_missing_from_ui=unmatched_recent,missing_close_times=missing,sources=dict(sources),near_boundary=[r for r in rows if min(abs(r['seconds_remaining']-420),abs(r['seconds_remaining']-600))<2])
(P/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
