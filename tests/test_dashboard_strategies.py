from dataclasses import asdict, replace

from fastapi.testclient import TestClient

from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.models import activate, identity, register
from btc15.strategies.momentum import Momentum, volatility_model


def test_catalog_hides_inactive_models_without_writing(store, tmp_path):
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        rows = client.get("/api/strategies").json()["rows"]
        assert len(rows) == 1
        assert [r["entries_enabled"] for r in rows] == [True]
        assert all(r["record"] is None and r["runs"] == [] for r in rows)
        assert client.get("/api/strategies?mode=INVALID").status_code == 422
    assert not store.list(limit=None)


def test_catalog_separates_models_modes_versions_and_configurations(store, tmp_path):
    configs = [Strategy(), Momentum(), volatility_model(), replace(Momentum(), model_version="v2")]
    for config in configs[1:]:
        activate(store, register(store, config), True)
    for i, config in enumerate(configs):
        for mode in ("PAPER", "BACKTEST"):
            run = f"{mode}-{i}"
            store.add("run", dict(model=identity(config), config=asdict(config)), run, mode, i)
            store.add("opportunity", dict(decision="NO_TRADE", model=identity(config)), run, mode, 10 + i)
    # Legacy control configurations must not be merged with the current control.
    store.add("run", dict(versions=dict(config="old-hash")), "legacy", "PAPER", 0)
    store.add("opportunity", dict(decision="NO_TRADE"), "legacy", "PAPER", 100)
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        rows = client.get("/api/strategies").json()["rows"]
        assert len(rows) == 4
        for i, row in enumerate(rows[:4]):
            assert row["model"] == identity(configs[i])
            assert row["record"]["run_id"] == f"PAPER-{i}"
            assert row["runs"][0]["run_id"] == f"PAPER-{i}"
        assert rows[3]["entries_enabled"] is True
        assert all(r["model"]["config_hash"] != "old-hash" for r in rows)
        assert store.list(kind="run", run_id="legacy")
        assert store.list(kind="opportunity", run_id="legacy")
        activate(store, "conservative-confirmed-momentum:v2", False)
        assert len(client.get("/api/strategies").json()["rows"]) == 3
        activate(store, "conservative-confirmed-momentum:v2", True)
        replay = client.get("/api/strategies?mode=BACKTEST").json()["rows"]
        assert len(replay) == 4
        assert all(r["record"]["mode"] == "BACKTEST" for r in replay)
        assert all(r["record"] is None for r in client.get("/api/strategies?mode=LIVE").json()["rows"])


def test_latest_evaluation_across_runs_and_shared_collector(store, tmp_path, monkeypatch):
    monkeypatch.setattr("btc15.dashboard.time.time", lambda: 1000)
    config = Momentum()
    activate(store, register(store, config), True)
    for run, started, evaluated in [("group/momentum", 1, 999), ("older", 0, 990)]:
        store.add("run", dict(model=identity(config)), run, "PAPER", started)
        store.add("opportunity", dict(decision="NO_TRADE"), run, "PAPER", evaluated)
    store.add(
        "status", dict(connected=True, models=[dict(run_id="group/momentum")]), "group/control", "PAPER", 999
    )
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        row = client.get("/api/strategies").json()["rows"][1]
        assert row["record"]["run_id"] == "group/momentum"
        assert len(row["runs"]) == 2
        selected = client.get("/api/evaluation", params=dict(run_id="group/momentum")).json()
        assert selected["collector_fresh"]
        assert selected["record"]["run_id"] == "group/momentum"
        assert not client.get("/api/evaluation?run_id=older").json()["collector_fresh"]


def test_strategy_feed_status_requires_current_membership_and_recent_evaluation(store, tmp_path):
    import time

    now = time.time()
    models = [Strategy(), Momentum(), volatility_model()]
    for config in models[1:]:
        activate(store, register(store, config), True)
    for i, config in enumerate(models):
        store.add("run", dict(model=identity(config)), f"feed-{i}", "PAPER", now - 20)
        store.add("opportunity", {}, f"feed-{i}", "PAPER", now - (10 if i == 2 else 1))
    store.add(
        "status",
        dict(
            connected=True,
            paper_execution=False,
            models=[dict(model=identity(config), run_id=f"feed-{i}") for i, config in enumerate(models[:1])],
        ),
        "feed-0",
        "PAPER",
        now,
    )
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        rows = client.get("/api/strategies").json()["rows"]
        assert [r["feed_status"] for r in rows] == ["current", "stopped", "stopped"]
        assert not any(r["paper_execution"] for r in rows)
        store.add(
            "status",
            dict(
                connected=True,
                paper_execution=True,
                models=[dict(model=identity(config), run_id=f"feed-{i}") for i, config in enumerate(models)],
            ),
            "feed-0",
            "PAPER",
            now + 0.01,
        )
        rows = client.get("/api/strategies").json()["rows"]
        assert [r["feed_status"] for r in rows] == ["current", "current", "waiting_or_stale"]
        assert all(r["paper_execution"] for r in rows)


def test_lifetime_pnl_keeps_modes_and_configurations_separate(store, tmp_path):
    control = identity(Strategy())
    for run in ("first", "resumed"):
        store.add("run", dict(model=control), run, "PAPER", 1)
    store.add("run", dict(model={**control, "config_hash": "historical"}), "old", "PAPER", 1)
    for run, mode, pnl in [
        ("first", "PAPER", 12.5),
        ("resumed", "PAPER", -2.25),
        ("old", "PAPER", -3),
        ("first", "BACKTEST", 100),
    ]:
        store.add("trade_result", dict(net_pnl=pnl, gross_pnl=999), run, mode, 2)
    # An open fill and an evaluation are not realized profit.
    store.add("fill", dict(action="buy", quantity=10, price=0.6, fee=0.1), "first", "PAPER", 3)
    store.add("opportunity", dict(net_ev=100), "first", "PAPER", 3)
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        data = client.get("/api/strategies?run_id=first").json()
        assert data["lifetime"]["net_pnl"] == 7.25
        assert data["lifetime"]["completed_trades"] == 3
        assert data["rows"][0]["lifetime"]["net_pnl"] == 10.25
        assert data["rows"][0]["lifetime"]["completed_trades"] == 2
        backtest = client.get("/api/strategies?mode=BACKTEST").json()["lifetime"]
        assert backtest["net_pnl"] == 100 and backtest["completed_trades"] == 1
        live = client.get("/api/strategies?mode=LIVE").json()["lifetime"]
        assert live["net_pnl"] == 0 and live["completed_trades"] == 0
