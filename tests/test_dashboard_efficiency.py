from fastapi.testclient import TestClient
from sqlalchemy import event

from btc15 import dashboard
from btc15.config import Strategy
from btc15.models import identity


def test_run_picker_excludes_large_source_and_config(store):
    store.add(
        "run",
        dict(
            model=identity(Strategy()),
            versions=dict(config="v1"),
            source_snapshot={"source.py": "x" * 1_000_000},
            config={"paths": 4000},
        ),
        "run",
        "PAPER",
        1,
    )
    with TestClient(dashboard.create_app(store)) as client:
        response = client.get("/api/runs?mode=PAPER")
    assert response.status_code == 200 and len(response.content) < 2000
    row = response.json()[0]
    assert row["run_id"] == "run" and row["body"]["model"]["model_id"] == "settlement-edge"
    assert "source_snapshot" not in row["body"] and "config" not in row["body"]
    assert len(store.list(kind="run")[0]["body"]["source_snapshot"]["source.py"]) == 1_000_000


def test_latest_evaluations_are_batched(store):
    for i in range(20):
        for t in (1, 2):
            store.add("opportunity", dict(decision="NO_TRADE"), str(i), "PAPER", t)
    store.publish_record("evaluation", dict(decision="NO_TRADE"), "0", "PAPER", 3)
    queries = []

    def count(conn, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(store.engine, "before_cursor_execute", count)
    try:
        rows = store.latest_evaluations([str(i) for i in range(20)], "PAPER")
    finally:
        event.remove(store.engine, "before_cursor_execute", count)
    assert len(queries) == 2
    assert len(rows) == 20 and rows["0"]["timestamp"] == 3
    assert all(rows[str(i)]["timestamp"] == 2 for i in range(1, 20))
    assert store.latest_evaluations(["0"], "BACKTEST") == {}


def test_lifetime_cache_invalidates_on_fills_and_results(store, monkeypatch):
    store.add("run", dict(model=identity(Strategy())), "run", "PAPER", 1)
    calls = []
    original = dashboard.lifetime_performance

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(dashboard, "lifetime_performance", counted)
    with TestClient(dashboard.create_app(store, config=Strategy())) as client:
        first = client.get("/api/strategies?mode=PAPER").json()
        n = len(calls)
        assert n > 0 and first["lifetime"]["open_exposure"] == 0
        client.get("/api/strategies?mode=PAPER")
        assert len(calls) == n
        store.add(
            "fill", dict(action="buy", quantity=1, price=0.9, fee=0.006), "run", "PAPER", 2, "market", "op"
        )
        filled = client.get("/api/strategies?mode=PAPER").json()
        assert len(calls) > n and filled["lifetime"]["open_exposure"] == 0.906
        n = len(calls)
        store.add("trade_result", dict(net_pnl=0.094), "run", "PAPER", 3, "market", "op")
        settled = client.get("/api/strategies?mode=PAPER").json()
        assert len(calls) > n and settled["lifetime"]["net_pnl"] == 0.094
        assert settled["lifetime"]["open_exposure"] == 0
        n = len(calls)
        client.get("/api/strategies?mode=PAPER")
        assert len(calls) == n
        assert client.get("/api/strategies?mode=BACKTEST").json()["lifetime"]["net_pnl"] == 0


def test_old_evaluation_distinguishes_completed_trade(store, monkeypatch):
    monkeypatch.setattr(dashboard.time, "time", lambda: 1000)
    store.add("run", dict(model=identity(Strategy())), "run", "PAPER", 1)
    store.add(
        "opportunity",
        dict(ticker="market", model=identity(Strategy()), decision="NO_TRADE"),
        "run",
        "PAPER",
        744,
        "market",
    )
    store.publish_record("status", dict(connected=True), "run", "PAPER", 999)
    monkeypatch.setattr(store, "state", lambda *a: "CLOSED")
    with TestClient(dashboard.create_app(store, config=Strategy())) as client:
        d = client.get("/api/evaluation?mode=PAPER&run_id=run").json()
        assert d["evaluation_age"] == 256
        assert "trading is complete" in d["message"]
        monkeypatch.setattr(store, "state", lambda *a: "EVALUATING")
        d = client.get("/api/evaluation?mode=PAPER&run_id=run").json()
        assert "no fresh evaluation" in d["message"]
