import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from btc15.config import Strategy
from btc15.dashboard import create_app
from btc15.models import identity


def test_snapshot_requests_avoid_history_and_keep_rows_on_failure(store, monkeypatch):
    store.add("run", dict(model=identity(Strategy())), "current", "PAPER", 1)
    store.add(
        "trade_result",
        dict(cost=8, bought=10, proceeds=10, fees=0.1, net_pnl=1.9, side="yes", opened=2),
        "current",
        "PAPER",
        3,
        "market",
        "trade",
    )
    app = create_app(store, run_id="current", cache_recent_trades=True)
    url = "/api/trades?mode=PAPER&scope=settlement&run_id=current&include_open=true&limit=3"
    with TestClient(app) as client:
        assert client.get(url).status_code == 503
        app.state.refresh_recent_trades()
        initial = client.get(url).json()
        assert initial["rows"][0]["market"] == "market"
        assert initial["stale"] is False
        original = store.list

        def fail(*args, **kwargs):
            raise OSError("history temporarily unavailable")

        monkeypatch.setattr(store, "list", fail)
        assert client.get(url).json() == initial
        app.state.refresh_recent_trades()
        assert client.get(url).json() == initial  # Unchanged history needs no full reads.
        revision = store.history_revision
        monkeypatch.setattr(store, "history_revision", fail)
        app.state.refresh_recent_trades()
        stale = client.get(url).json()
        assert stale["rows"] == initial["rows"]
        assert stale["updated_at"] == initial["updated_at"]
        assert stale["stale"] is True
        monkeypatch.setattr(store, "list", original)
        monkeypatch.setattr(store, "history_revision", revision)
        app.state.refresh_recent_trades()
        assert client.get(url).json()["stale"] is False
        version = app.state.recent_trade_version
        store.add(
            "trade_result",
            dict(cost=8, bought=10, proceeds=10, fees=0.1, net_pnl=1.9, side="yes", opened=4),
            "current", "PAPER", 5, "next-market", "next-trade",
        )
        app.state.refresh_recent_trades()
        refreshed = client.get(url).json()
        assert len(refreshed["rows"]) == 2
        assert app.state.recent_trade_version == version + 1
        app.state.refresh_recent_trades()
        assert app.state.recent_trade_version == version + 1


def test_delayed_notice_preserves_rows_and_is_not_duplicated():
    source = Path("src/btc15/static/app.js").read_text()
    function = source[
        source.index("function markTradesDelayed(") : source.index("async function refreshFleet(")
    ]
    program = (
        """
const assert=require('node:assert/strict');
function text(tag,value,classes){return {textContent:value,classes};}
const trade={classes:'fleet-trade',textContent:'existing trade'};
const children=[trade];
const card={trades:{querySelector(selector){return children.find(c=>c.classes.split(' ').includes(selector.slice(1)));},append(item){children.push(item);}}};
"""
        + function
        + """
markTradesDelayed(card);markTradesDelayed(card);
assert.equal(children.length,2);assert.equal(children[0],trade);
assert.match(children[1].textContent,/Update delayed/);
"""
    )
    subprocess.run(["node", "-e", program], check=True)
