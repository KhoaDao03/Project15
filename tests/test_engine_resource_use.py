import copy
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from btc15 import engine as module
from btc15.domain import Book
from btc15.engine import Engine
from btc15.storage import Store
from btc15.strategies.settlement_edge.model import Tick


def make_engine(path, config, markets, series, now, ticks):
    store = Store("sqlite:///" + str(path))
    engine = Engine(
        store,
        config,
        mode="BACKTEST",
        run_id="comparison",
        execute=False,
        signal_only=True,
        record_evaluations=False,
    )
    engine.healthy = engine.clock_ok = engine.exchange_open = True
    engine.series_fees = series
    engine.series_fee_changes = []
    engine.ticks = list(ticks)
    for market in markets:
        engine.markets[market.ticker] = market
        book = Book()
        book.snapshot(dict(yes_dollars_fp=[[".40", "100"]], no_dollars_fp=[[".40", "100"]]), now)
        engine.books[market.ticker] = book
        engine.fee_changes[market.event_ticker] = []
        for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
            engine.state(market.ticker, state, now)
    return engine


@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("history", ["normal", "gaps", "duplicate", "insufficient"])
def test_shared_features_match_independent_markets_exactly(
    tmp_path, config, market, series, now, monkeypatch, history, seeded
):
    config = replace(config, sustained_lead_enabled=seeded, bleep_exchange_seed_enabled=seeded)
    markets = [replace(market, ticker=market.ticker + f"-{i}") for i in range(3)]
    ticks = [
        Tick(now - 3599 + i, now - 3599 + i, market.spec.strike + 100 + math.sin(i / 13)) for i in range(3600)
    ]
    if history == "gaps":
        del ticks[200:220]
    elif history == "duplicate":
        ticks.insert(100, ticks[99])
    elif history == "insufficient":
        ticks = ticks[-1:]
    multi = make_engine(tmp_path / "multi.db", config, markets, series, now, ticks)
    solos = [
        make_engine(tmp_path / f"solo-{i}.db", config, [m], series, now, ticks) for i, m in enumerate(markets)
    ]
    if seeded:
        last_minute = int(now // 60) - 61
        for engine in [multi, *solos]:
            engine.bleep_seed = dict(last_minute=last_minute, provider="fixture")
            engine.bleep_candles = {
                m: (
                    m,
                    market.spec.strike,
                    market.spec.strike + 2,
                    market.spec.strike - 2,
                    market.spec.strike + 1,
                )
                for m in range(last_minute - 99, last_minute + 1)
            }
    original = module.features
    calls = []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(module, "features", counted)
    try:
        for i, at in enumerate((now, now + 1, now - 1)):
            # Clock changes, new samples, gaps and errors must not reuse another event's features.
            if i == 1:
                for engine in [multi, *solos]:
                    engine.ticks.append(Tick(at, at, market.spec.strike + 102))
            before = len(calls)
            multi.process(at, f"event-{i}", "cfbenchmarks_value", {})
            used = len(calls) - before
            assert used == (3 if history == "duplicate" or (history == "insufficient" and i != 1) else 1)
            for solo in solos:
                solo.process(at, f"event-{i}", "cfbenchmarks_value", {})
                ticker = next(iter(solo.markets))
                assert multi.latest[ticker] == solo.latest[ticker]
        # Decoration/record consumers cannot modify another market's nested feature result.
        a, b = list(multi.latest.values())[:2]
        if a["features"]:
            before = copy.deepcopy(b["features"])
            a["features"]["isolation_test"] = {"changed": True}
            for value in a["features"].values():
                if isinstance(value, dict):
                    value["nested_isolation_test"] = True
            assert b["features"] == before
    finally:
        for engine in [multi, *solos]:
            engine.store.engine.dispose()


def test_confirmed_settlement_releases_derived_state_but_keeps_late_event_evidence(
    store, config, market, book, now, monkeypatch
):
    engine = Engine(store, config, execute=False, record_evaluations=False)
    engine.markets[market.ticker] = market
    engine.books[market.ticker] = book
    ticker = market.ticker
    caches = [
        engine._model_cache,
        engine._lead_history,
        engine._decision_keys,
        engine._signal_quote_inputs,
        engine._management_gaps,
        engine.last_evaluation,
    ]
    for cache in caches:
        cache[ticker] = ["derived"]
    engine.latest[ticker] = dict(timestamp=now, features=dict(old=True))
    engine.latest["newer"] = dict(timestamp=now + 1)
    engine._research_models[ticker] = "model-link"
    engine._research_books[ticker] = {"snapshot_id": "book-link"}
    monkeypatch.setattr(engine.executor, "settle", lambda *a, **kw: "BLOCKED")
    assert engine.settle(ticker, "yes", market.close_time + 1) == "BLOCKED"
    assert all(ticker in cache for cache in caches)
    monkeypatch.setattr(engine.executor, "settle", lambda *a, **kw: "SETTLED")
    assert engine.settle(ticker, "yes", market.close_time + 1) == "SETTLED"
    assert all(ticker not in cache for cache in caches)
    assert ticker not in engine.latest
    assert engine.markets[ticker] is market and engine.books[ticker] is book
    assert engine._research_models[ticker] == "model-link"
    assert engine._research_books[ticker] == {"snapshot_id": "book-link"}


@pytest.mark.parametrize("obligation", ["position", "order", "quarantine"])
def test_cache_cleanup_never_discards_obligations(store, config, obligation):
    engine = Engine(store, config, execute=False)
    engine._model_cache["market"] = "keep"
    target = {
        "position": engine.executor.positions,
        "order": engine.executor.orders,
        "quarantine": engine.executor.quarantines,
    }[obligation]
    target["market"] = SimpleNamespace(active=True)
    engine._release_settled_cache("market")
    assert engine._model_cache["market"] == "keep"


def test_settled_latest_survives_until_newer_evaluation(tmp_path, config, market, series, now):
    engine = make_engine(
        tmp_path / "latest.db", config, [market], series, now, [Tick(now, now, market.spec.strike)]
    )
    engine.latest["settled"] = dict(timestamp=now - 1)
    engine._release_settled_cache("settled")
    assert engine.latest["settled"]["timestamp"] == now - 1
    engine.process(now, "newer", "heartbeat", {})
    assert "settled" not in engine.latest
    assert market.ticker in engine.latest
    engine.store.engine.dispose()
