"""Illustrative sampled-book sensitivity, not executable backtest results."""
import json
from pathlib import Path
P=Path(__file__).parent
trades=json.loads((P/'trades.json').read_text())
books=json.loads((P/'book-paths.json').read_text())
results=[]
for threshold in [.60,.65,.70,.75]:
    for delay in [.7,2.0]:
        rows=[]
        for trade in trades:
            if trade['ticker'] not in books:continue
            side=trade['side'];q=trade['quantity']
            # Stop sensitivity only before the first confirmed real sale.
            end=min((x['filled_at'] for x in trade['exits']),default=trade['closed_at'])
            start=trade['buy_timing'].get('acknowledged_at',trade['opened']+1)
            points=sorted([b for b in books[trade['ticker']] if start<=b['at']<end and b['valid'] and b['fresh']],key=lambda b:b['at'])
            hit=next((b for b in points if b[side+'_bid'] is not None and b[side+'_bid']<=threshold),None)
            row=dict(ticker=trade['ticker'],actual=trade['pnl'],estimated=trade['pnl'],changed=False)
            if hit:
                # First later retained book, rather than assuming the trigger price fills.
                fill=next((b for b in points if b['at']>=hit['at']+delay),None)
                if not fill or fill['at']-hit['at']-delay>2:
                    row.update(estimated=None,reason='NO_TIMELY_LATER_BOOK')
                else:
                    remaining=q;proceeds=fee=0
                    for price,qty in fill[side+'_top5']:
                        take=min(remaining,qty);remaining-=take;proceeds+=take*price;fee+=.07*take*price*(1-price)
                    if remaining>1e-8:row.update(estimated=None,reason='INSUFFICIENT_RECORDED_DEPTH')
                    else:row.update(estimated=proceeds-fee-q*trade['entry']-trade['buyfees'],changed=True,trigger_at=hit['at'],fill_at=fill['at'],fill_price=proceeds/q)
            rows.append(row)
        complete=all(r['estimated'] is not None for r in rows)
        results.append(dict(threshold=threshold,delay=delay,complete=complete,baseline=sum(r['actual'] for r in rows),estimate=sum(r['estimated'] for r in rows) if complete else None,changed=sum(r['changed'] for r in rows),rows=rows))
(P/'stop-sensitivity.json').write_text(json.dumps(dict(warning='Sampled books and incomplete capture; no queue priority, cancellation model, partial previous sales, intervening books or replacement entries. Fees approximate. Unchanged positions retain actual outcomes. Diagnostic only.',results=results),indent=2)+'\n')
for r in results:print({k:v for k,v in r.items() if k!='rows'})
