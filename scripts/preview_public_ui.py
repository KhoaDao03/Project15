"""Isolated public UI preview. Synthetic records only; no trading connections."""
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

STATIC = Path(__file__).resolve().parents[1] / "src/btc15/public_static"
ASSETS = ("BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def sample_trades(asset, total=38):
    return [dict(
        market=f"DEMO-{asset}-15M-{i:03}", timestamp=1789992000-i*900,
        opened=1789992000-i*900, status="CLOSED", side="yes" if i % 3 == 0 else "no",
        bought=10, entry=.91, exit=.99, fees=.035, net_pnl=.765,
        market_result="yes" if i % 3 == 0 else "no", exit_timestamp=1789992120-i*900,
        exit_type="settlement" if i % 4 == 0 else "sale",
    ) for i in range(total)]


def sample_view():
    assets = []
    for i, asset in enumerate(ASSETS):
        completed = [260, 170, 280, 300, 8, 30, 42][i]
        wins = [230, 152, 245, 265, 7, 22, 37][i]
        assets.append(dict(
            asset=asset, healthy=i < 4, price=[84320.25, 2685.4, 115.07, 1.4989, None, None, None][i],
            state="EVALUATING", realized_pnl=[36.5, -14.25, -12.15, 21.35, 1.85, -13.25, .65][i],
            completed_trades=completed, wins=wins, losses=completed-wins-1, breakeven_trades=1,
            win_rate=wins/completed, current_streak=[12, -2, 4, 7, 3, -1, 6][i],
            longest_win_streak=24-i, longest_loss_streak=3, open_positions=0,
            markets=[dict(ticker=f"DEMO-{asset}-15M", fresh=i < 4)],
            live_policy=dict(enabled=i < 4, contracts=10, revision=1),
            trades=sample_trades(asset, 5), trades_stale=False,
        ))
    return dict(assets=assets, realized_pnl=sum(a['realized_pnl'] for a in assets),
                totals_complete=True, live_only=True, live_available=True, stale=False, updated_at=time.time())


@app.get('/api/view')
def view():
    return sample_view()


@app.get('/api/stop')
def stop():
    return dict(enabled=False, status='idle', message='Preview only: owner mutations are unavailable.', assets={})


@app.get('/api/history/{asset}')
def history(asset: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
    if asset not in ASSETS:
        raise HTTPException(404)
    rows = sample_trades(asset)
    return dict(rows=rows[offset:offset+limit], total=len(rows), stale=False)


@app.get('/')
def index():
    return HTMLResponse(STATIC.joinpath('index.html').read_text().replace(
        'All-time recorded performance, market status, and trade history.',
        'LOCAL PREVIEW · Synthetic data · All-time performance, market status, and trade history.'))


@app.get('/{filename}')
def static(filename: str):
    if filename not in ('viewer.js', 'viewer.css', 'logo.svg'):
        raise HTTPException(404)
    return FileResponse(STATIC / filename)


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8015)
