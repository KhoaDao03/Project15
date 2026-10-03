import json,sqlite3,collections,datetime,gzip,time,csv,math
from pathlib import Path
from decimal import Decimal as D
from zoneinfo import ZoneInfo
P=Path(__file__).parent;ROOT=Path('/root/Project15');now=time.time()
def stamp(x):return datetime.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def et(t):return datetime.datetime.fromtimestamp(t,ZoneInfo('America/New_York')).isoformat()
with sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro',uri=True) as db:
 db.execute('BEGIN');orders=[json.loads(r[0]) for r in db.execute('select body from manual_orders')];closes={t:json.loads(b)['close_time'] for t,b in db.execute('select ticker,body from live_controls')}
settles={}
for m in json.loads(Path('data/cloud/manifest.json').read_text()):
 with sqlite3.connect(f"file:data/cloud/{m['data_dir']}/paper.db?mode=ro",uri=True) as db:
  for t,b in db.execute("select market,body from records where kind='settlement'"):
   s=json.loads(b)['result']
   if s in ['yes','no']:settles[t]=s
groups=collections.defaultdict(list)
for r in orders:groups[r['request']['ticker']].append(r)
trades={};excluded=collections.Counter()
for t,rr in groups.items():
 buys=[r for r in rr if r['request']['action']=='buy' and D((r.get('exchange_order') or {}).get('fill_count_fp','0'))>0]
 if not buys:continue
 if any(r.get('origin')!='bot' for r in buys) or len({r['request']['side'] for r in buys})!=1:excluded['mixed_manual_or_sides']+=1;continue
 buys.sort(key=lambda r:r['created_at']);first=buys[0];side=first['request']['side'];qty=cost=sold=proceeds=fees=D(0)
 for r in rr:
  o=r.get('exchange_order') or {};q=D(o.get('fill_count_fp','0'))
  if q<=0:continue
  paid=sum((D(o.get(k,'0')) for k in ['maker_fill_cost_dollars','taker_fill_cost_dollars']),D(0));fees+=sum((D(o.get(k,'0')) for k in ['maker_fees_dollars','taker_fees_dollars']),D(0))
  if r['request']['action']=='buy':qty+=q;cost+=paid
  else:
   assert o['outcome_side']!=side;sold+=q;proceeds+=q-paid
 if sold>qty:excluded['oversold']+=1;continue
 if sold<qty:
  if t not in settles:excluded['open_or_missing_settlement']+=1;continue
  proceeds+=(qty-sold)*D(settles[t]==side)
 opened=stamp(first['exchange_order']['created_time']) if first['exchange_order'].get('created_time') else first['created_at']
 trades[t]=dict(ticker=t,asset=t.split('15M-')[0][2:],side=side,opened=opened,entry_et=et(opened),requested=first['created_at'],remaining=closes.get(t,opened)-opened,qty=float(qty),entry=float(cost/qty),pnl=float(proceeds-cost-fees),fees=float(fees),check_id=first.get('timing',{}).get('execution_check_id'),decision_id=first.get('timing',{}).get('decision_id'),buy_ids=[r['id'] for r in buys],exit_reasons=sorted({r.get('automation_reason','') for r in rr if r['request']['action']=='sell'}))
(P/'trades-all.json').write_text(json.dumps(list(trades.values()),indent=2));print('Trades',len(trades),'losses',sum(r['pnl']<0 for r in trades.values()),'excluded',dict(excluded),flush=True)
checks={};coverage=[];files=0
for mf in sorted(Path('research-logs').glob('*/*/manifest.json')):
 m=json.loads(mf.read_text())
 if m.get('producer')!='live_executor':continue
 status=mf.with_name('status.json');coverage.append(dict(asset=m['asset'],session=m['session'],started_at=m['started_at'],status=json.loads(status.read_text()) if status.exists() else {}))
 targets={t:r for t,r in trades.items() if r['asset']==m['asset'] and r['opened']>=m['started_at']-5}
 if not targets:continue
 for f in sorted(mf.parent.glob('events-*')):
  if not f.name.endswith(('.gz','.gz.part')):continue
  files+=1
  try:
   with gzip.open(f,'rt') as g:
    for line in g:
     if 'decision_check' not in line or ('"accepted":true' not in line and '"accepted": true' not in line):continue
     d=json.loads(line);b=d.get('body',{});t=b.get('market');r=targets.get(t)
     if not r or d.get('kind')!='decision_check' or not b.get('collector_record'):continue
     at=b.get('started_at') or d.get('processed_at') or d.get('captured_at');delta=at-r['requested']
     if not -3<=delta<=2:continue
     exact=b.get('decision_id')==r['check_id'];rank=(not exact,abs(delta))
     if t not in checks or rank<checks[t]['rank']:checks[t]={'rank':rank,'file':str(f),'record':d}
  except (EOFError,OSError,json.JSONDecodeError) as e:coverage[-1].setdefault('read_errors',[]).append({'file':str(f),'error':type(e).__name__})
 print('scan',m['asset'],m['session'],'matched',len(checks),'files',files,flush=True)
(P/'entry-checks.json').write_text(json.dumps(checks));(P/'coverage.json').write_text(json.dumps(coverage,indent=2));print('DONE matched',len(checks),flush=True)
