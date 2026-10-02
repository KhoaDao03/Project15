import json,csv,collections,datetime,statistics,sqlite3,pathlib
P=pathlib.Path('reports/atr-review-20260930'); A=['ETH','XRP','DOGE','BNB','HYPE']; since=datetime.datetime(2026,9,30,7,33,tzinfo=datetime.UTC).timestamp(); out=[];stats={}
c=sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True)
orders=collections.defaultdict(list)
for raw, in c.execute('select body from manual_orders'):
 b=json.loads(raw);q=b.get('request',{}); ticker=q.get('ticker');o=b.get('exchange_order',{}); filled=float(o.get('fill_count_fp') or o.get('fill_count') or 0)
 if q.get('action')=='buy' and filled>0:
  orders[ticker].append(dict(q=filled,cost=float(o.get('taker_fill_cost_dollars') or 0)+float(o.get('maker_fill_cost_dollars') or 0),fee=float(o.get('taker_fees_dollars') or 0)+float(o.get('maker_fees_dollars') or 0),origin=b.get('origin'),t=b.get('created_at')))
def stat(v):
 w=[r for r in v if r['pnl']>0];l=[r for r in v if r['pnl']<0]
 avg=lambda vv,k: statistics.mean(r[k] for r in vv) if vv else None
 W=avg(w,'net_per_contract');L=avg(l,'net_per_contract'); n=len(v);p=len(w)/n if n else 0;z=1.96;den=1+z*z/n if n else 1;center=(p+z*z/(2*n))/den if n else 0; half=z*((p*(1-p)/n+z*z/(4*n*n))**.5)/den if n else 0
 return dict(n=n,wins=len(w),losses=len(l),win_rate=p,win_rate_95=[center-half,center+half],net_pnl=sum(r['pnl'] for r in v),mean_entry=avg(v,'entry'),mean_winning_net=W,mean_losing_net=L,mean_net_per_contract=avg(v,'net_per_contract'),break_even_win_rate=-L/(W-L) if W is not None and L is not None else None,loss_erases_wins=-L/W if W and L else None,fees=sum(r['fees'] for r in v),contracts=sum(r['contracts'] for r in v),stop_count=sum('STOP' in r['exit_reason'] for r in v),entry_94_plus=sum(r['entry']>=.94-1e-8 for r in v))
for a in A:
 rows=json.loads((P/f'{a}-ui-history.json').read_text())['rows'];ar=[]
 for row in rows:
  b=row['body'];q=b['bought'];r=dict(asset=a,market=row['market'],opened=b['opened'],opened_utc=datetime.datetime.fromtimestamp(b['opened'],datetime.UTC).isoformat(),side=b['side'],contracts=q,entry=b['cost']/q,cost=b['cost'],fees=b['fees'],pnl=b['net_pnl'],net_per_contract=b['net_pnl']/q,exit_reason=b.get('exit_reason',''),market_result=b.get('market_result'),settled_at=row['timestamp']);ar.append(r);out.append(r)
 stats[a]={'all':stat(ar),'since_no_spread':stat([r for r in ar if r['opened']>=since]),'entry_bins':{label:stat([r for r in ar if low<=r['entry']<high]) for label,low,high in [('80-85',.8,.85),('85-90',.85,.90),('90-94',.90,.94),('94-97',.94,.97)]}}
 checks=[]
 for r in ar:
  oo=orders[r['market']];checks.append((abs(sum(o['q'] for o in oo)-r['contracts'])<1e-6 and abs(sum(o['cost'] for o in oo)-r['cost'])<1e-5))
 stats[a]['journal_reconciled']=sum(checks);stats[a]['journal_mismatch']=len(checks)-sum(checks)
 print(a,json.dumps(stats[a]['all']), 'reconciled',sum(checks),'/',len(checks))
with (P/'trades.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
(P/'trade-summary.json').write_text(json.dumps(stats,indent=2))
