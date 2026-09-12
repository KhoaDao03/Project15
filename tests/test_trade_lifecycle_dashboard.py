import pytest
from fastapi.testclient import TestClient

from btc15.config import Strategy
from btc15.dashboard import create_app
from btc15.models import identity


def test_trade_lifecycle_partial_fills_close_and_scope(store):
    for run in ("run", "other"):
        store.add("run", dict(model=identity(Strategy())), run, "PAPER", 1)

    def fill(action, quantity, price, fee, timestamp, run="run"):
        store.add(
            "fill",
            dict(action=action, side="no", quantity=quantity, price=price, fee=fee, trade_id="op"),
            run,
            "PAPER",
            timestamp,
            "market",
            "op",
        )

    with TestClient(create_app(store, config=Strategy())) as client:
        url = "/api/trades?include_open=true&run_id=run"
        assert client.get(url).json()["total"] == 0
        fill("buy", 2, 0.85, 0.02, 2)
        fill("buy", 3, 0.86, 0.03, 3)
        fill("buy", 1, 0.9, 0.01, 3, "other")
        data = client.get(url).json()
        assert data["total"] == 1
        b = data["rows"][0]["body"]
        assert b["status"] == "OPEN" and b["net_pnl"] is None
        assert b["market_result"] is None
        assert b["quantity"] == 5 and b["entry"] == pytest.approx(0.856)
        assert client.get("/api/trades?run_id=run").json()["total"] == 0
        assert client.get(url + "&search=missing").json()["total"] == 0
        fill("sell", 2, 0.95, 0.01, 4)
        b = client.get(url).json()["rows"][0]["body"]
        assert b["quantity"] == 3 and b["proceeds"] == pytest.approx(1.9)
        assert b["fees"] == pytest.approx(0.06) and b["net_pnl"] is None
        fill("sell", 3, 0.96, 0.01, 5)
        store.add(
            "trade_result",
            dict(
                trade_id="op",
                side="no",
                bought=5,
                quantity=0,
                cost=4.28,
                proceeds=4.78,
                fees=0.07,
                net_pnl=0.43,
                opened=2,
                reason="INVALIDATION",
            ),
            "run",
            "PAPER",
            5,
            "market",
            "op",
        )
        data = client.get(url).json()
        assert data["total"] == 1
        b = data["rows"][0]["body"]
        assert b["status"] == "CLOSED" and b["net_pnl"] == 0.43
        assert b["market_result"] is None
        # A profitable sale is independent of the eventual market outcome.
        store.add("settlement", dict(result="yes"), "other", "PAPER", 6, "market")
        assert client.get(url).json()["rows"][0]["body"]["market_result"] is None
        store.add("settlement", dict(result="no"), "run", "PAPER", 7, "market")
        b = client.get(url).json()["rows"][0]["body"]
        assert b["market_result"] == "no" and b["net_pnl"] == 0.43
        assert client.get("/api/trades?run_id=run").json()["rows"][0]["body"]["market_result"] == "no"
        assert client.get("/api/trades?include_open=true").json()["total"] == 2
        assert client.get("/api/trades?include_open=true&mode=BACKTEST").json()["total"] == 0
