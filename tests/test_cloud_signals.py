import asyncio
import builtins
import json
import time

import pytest
from test_collection import fake_client, fake_socket
from test_position_management import scenario  # noqa: F401

from btc15.config import Settings
from btc15.operation import health
from btc15.runner import collect
from btc15.storage import parquet_modules


def test_signal_collector_restarts_without_paper_or_raw(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    for _ in range(2):
        fake_socket(monkeypatch, [dict(type="ticker", msg={})])
        run = asyncio.run(
            collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                managed_run="live-signals",
                live_signals=True,
                duration=0.1,
            )
        )
        assert run == "live-signals"
    assert not (tmp_path / "raw").exists()
    for kind in ("order", "fill", "raw_source", "execution_rejection"):
        assert not store.list(kind=kind, run_id=run)
    status = store.list(kind="status", run_id=run, newest_first=True)[0]["body"]
    assert status["live_signals"] and not status["paper_execution"]
    assert status["paper_worker"] is None


def test_signal_candidate_has_no_paper_execution(scenario):  # noqa: F811
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.execute = False
    engine.signal_only = True
    engine.process(fixture.clock[0], "signal", "heartbeat", {})
    assert next(iter(engine.latest.values()))["decision"] == "TRADE_CANDIDATE"
    assert not engine.executor.orders
    assert not engine.store.list(kind="execution_rejection", run_id=engine.run_id)


def test_signal_settlement_retains_official_result_without_fills(scenario, raw, series):  # noqa: F811
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.execute = False
    engine.signal_only = engine.signal_settlements = True
    market = next(iter(engine.markets.values()))
    final = dict(raw, status="finalized", result="yes")
    evidence = dict(source="kalshi_rest", market=final, series=series)
    engine.settle(market.ticker, "yes", market.close_time + 1, evidence=evidence)
    assert engine.store.list(kind="settlement", run_id=engine.run_id)[0]["body"]["result"] == "yes"
    assert engine.store.state(engine.run_id, market.ticker) == "CLOSED"
    assert not engine.store.list(kind="fill", run_id=engine.run_id)


def test_cloud_manifest_matches_services_and_refuses_overwrite(tmp_path):
    import runpy
    from pathlib import Path

    from btc15.fleet import load_members

    prepare = runpy.run_path("scripts/prepare_cloud.py")["prepare"]
    path = prepare(tmp_path / "cloud")
    members = load_members(path)
    for asset, member in members.items():
        assert member["run_id"] == f"{asset}-live-signals"
        assert member["live_only"]
        assert Path(member["data_dir"]).name == asset
    before = json.loads(path.read_text())
    with pytest.raises(FileExistsError):
        prepare(path.parent)
    assert json.loads(path.read_text()) == before


def test_live_signal_health_requires_fresh_data(store):
    now = time.time()
    body = dict(
        connected=True,
        clock_ok=True,
        exchange_open=True,
        live_signals=True,
        paper_execution=False,
        reference_age=0.1,
        processing_lag=0.01,
    )
    store.publish_record("status", body, "signals", "PAPER", now)
    assert health(store, "signals", now)["healthy"]
    assert not health(store, "signals", now + 10)["healthy"]
    body["live_signals"] = False
    store.publish_record("status", body, "signals", "PAPER", now)
    assert not health(store, "signals", now)["healthy"]


def test_signal_collector_refuses_paper_portfolio(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    from btc15.engine import Engine

    Engine(store, config, run_id="old-paper")
    with pytest.raises(ValueError, match="own database"):
        asyncio.run(
            collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                managed_run="signals",
                live_signals=True,
                duration=0.1,
            )
        )


def test_parquet_extra_has_actionable_error(monkeypatch):
    original = builtins.__import__

    def without_arrow(name, *args, **kwargs):
        if name.startswith("pyarrow"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_arrow)
    with pytest.raises(RuntimeError, match="--extra research"):
        parquet_modules()


def test_signal_restart_recovers_result_for_market_absent_from_discovery(
    store, config, tmp_path, raw, series, monkeypatch
):
    from btc15 import runner
    from btc15.engine import Engine

    run = "signals"
    engine = Engine(store, config, run_id=run, execute=False, signal_only=True, signal_settlements=True)
    ticker = raw["ticker"]
    store.add("market", dict(raw=dict(raw), series=series), run, "PAPER", time.time(), ticker)
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        engine.state(ticker, state, time.time())
    final = dict(raw, status="finalized", result="yes")
    fake_client(monkeypatch, final, series)

    async def empty_discovery(self):
        return series, []

    monkeypatch.setattr(runner.KalshiClient, "discover", empty_discovery)
    fake_socket(monkeypatch, [])
    asyncio.run(
        collect(
            Settings(data_dir=str(tmp_path)), config, store, managed_run=run, live_signals=True, duration=0.1
        )
    )
    assert store.list(kind="settlement", run_id=run)[0]["body"]["result"] == "yes"
    assert store.state(run, ticker) == "CLOSED"
