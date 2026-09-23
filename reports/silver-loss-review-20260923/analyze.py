"""Read-only audit of recorded silver fills; no orders or strategy edits."""
import collections
import csv
import datetime as dt
import gzip
import json
import sqlite3
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path('/root/Project15')
OUT = Path(__file__).parent
def db(path):
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    c.execute('BEGIN')
    return c
def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()

settlements = {}
for path in sorted((ROOT/'data').rglob('*.db')):
    if 'silver' not in str(path).lower():
        continue
    with db(path) as c:
        for market, at, body in c.execute("select market,timestamp,body from records where kind='settlement'"):
            b = json.loads(body)
            if market in settlements:
                assert settlements[market]['result'] == b['result'], market
            settlements[market] = dict(result=b['result'], at=at)
with db(ROOT/'data/cloud/manual-orders.sqlite') as c:
    orders = [json.loads(b) for b, in c.execute("select body from manual_orders where json_extract(body,'$.request.ticker') like 'KXSILVER%'")]
groups = collections.defaultdict(list)
for r in orders:
    groups[r['request']['ticker']].append(r)
trades = []
for ticker, rows in sorted(groups.items()):
    fills = [r for r in rows if D((r.get('exchange_order') or {}).get('fill_count_fp','0')) > 0]
    buys = [r for r in fills if r['request']['action']=='buy']
    if not buys:
        continue
    assert all(r.get('origin')=='bot' for r in buys)
    side = buys[0]['request']['side']
    bought = sold = cost = proceeds = fees = buyfees = D(0)
    exits = []
    for r in fills:
        o = r['exchange_order']; action = r['request']['action']; q = D(o['fill_count_fp'])
        assert r['request']['side']==side
        assert o['outcome_side']==(side if action=='buy' else ('no' if side=='yes' else 'yes'))
        paid = sum((D(o[k]) for k in ('maker_fill_cost_dollars','taker_fill_cost_dollars')), D(0))
        fee = sum((D(o[k]) for k in ('maker_fees_dollars','taker_fees_dollars')), D(0))
        fees += fee
        if action=='buy':
            bought += q; cost += paid; buyfees += fee
        else:
            sold += q; proceeds += q-paid
            exits.append(dict(reason=r.get('automation_reason'), price=float((q-paid)/q),quantity=float(q),at=r['created_at'],filled_at=dt.datetime.fromisoformat(o['last_update_time'].replace('Z','+00:00')).timestamp(),timing=r.get('timing',{})))
    assert sold <= bought
    result = settlements.get(ticker,{}).get('result')
    remaining = bought-sold
    closed = remaining==0 or result in ('yes','no')
    payout = remaining if result==side else D(0)
    opened = min(r['created_at'] for r in buys)
    # Kalshi ticker time is Eastern; derive from settlement/market later where available.
    trade = dict(ticker=ticker,side=side,opened=opened,opened_utc=utc(opened),quantity=float(bought),entry=float(cost/bought),sold=float(sold),exit=float(proceeds/sold) if sold else None,fees=float(fees),pnl=float(proceeds+payout-cost-fees) if closed else None,result=result,exits=exits,buy_ids=[r['id'] for r in buys],buy_timing=buys[0].get('timing',{}),hold_pnl=float((bought if result==side else D(0))-cost-buyfees) if result else None,resting_rejected=sum(r.get('resting_take_profit',False) and r['state']=='rejected' for r in rows))
    trades.append(trade)
    trade['buyfees']=float(buyfees)
    trade['closed_at']=settlements[ticker]['at'] if remaining and result else max((x['filled_at'] for x in exits),default=None)
(OUT/'trades.json').write_text(json.dumps(trades,indent=2)+'\n')
closed = [r for r in trades if r['pnl'] is not None]
def summary(rows):
    wins = [r for r in rows if r['pnl']>0]; losses=[r for r in rows if r['pnl']<0]
    return dict(trades=len(rows),wins=len(wins),losses=len(losses),net=round(sum(r['pnl'] for r in rows),5),gains=round(sum(r['pnl'] for r in wins),5),loss_total=round(sum(r['pnl'] for r in losses),5))
print('TOTAL',summary(closed),flush=True)
for day in sorted({r['opened_utc'][:10] for r in closed}):
    print(day,summary([r for r in closed if r['opened_utc'].startswith(day)]),flush=True)
for r in closed:
    if r['pnl']<0: print('LOSS',r['ticker'],r['opened_utc'],r['entry'],r['exit'],r['pnl'],'eventual winner',r['side']==r['result'],flush=True)

# Extract recorded live checks near each actual entry from finalized executor segments.
if '--accounting-only' in sys.argv:
    sys.exit(0)
by_market = {r['ticker']:r for r in trades}
matched = {}
for session in sorted((ROOT/'research-logs/SILVER').iterdir()):
    manifest = json.loads((session/'manifest.json').read_text())
    if manifest.get('producer')!='live_executor': continue
    for path in sorted(session.glob('*.jsonl.gz')):
        with gzip.open(path,'rb') as stream:
            for line in stream:
                if b'"decision_check"' not in line: continue
                r=json.loads(line); b=r['body']; ticker=b.get('market')
                if ticker not in by_market: continue
                t=by_market[ticker]['opened']; at=r.get('processed_at') or r['captured_at']
                if not t-5 <= at <= t+3: continue
                matched.setdefault(ticker,[]).append(dict(session=session.name,record=r))
    print('scanned',session.name,'matched',len(matched),flush=True)
(OUT/'entry-checks.json').write_text(json.dumps(matched,indent=2)+'\n')
(OUT/'summary.json').write_text(json.dumps(summary(closed),indent=2)+'\n')
