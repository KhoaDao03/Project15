import math
from dataclasses import replace

import pytest

from btc15.domain import Book, D
from btc15.models import ModelGroup, activate, comparison, definitions, register
from btc15.strategies.momentum import Momentum, evaluate, observations, regime, volatility_model
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import fee_bound


def signal(
    market,
    now,
    *,
    c=None,
    side="yes",
    move=0.002,
    ask=0.60,
    spread=0.03,
    edge=0.20,
    confirm=None,
    age=30,
    remaining=300,
    source="CF Benchmarks BRTI",
    vol="NORMAL",
):
    c = c or Momentum()
    now = market.close_time - remaining
    sign = 1 if side == "yes" else -1
    tick = Tick(now, now, float(D(market.spec.strike) * (1 + D(sign) * D(move))))
    b = Book(received=now, source_time=now, valid=True)
    setattr(b, side, {D(ask) - D(spread): D(10)})
    setattr(b, "no" if side == "yes" else "yes", {1 - D(ask): D(10)})
    p = float(D(ask) + D(fee_bound(ask, c)) + D(edge))
    return evaluate(
        market,
        b,
        tick,
        dict(
            return_180=sign * 0.001 if confirm is None else confirm,
            candle_age=age,
            reference_source=source,
            volatility_regime=vol,
            volatility_samples=40,
        ),
        {"conservative_" + side: p, "p_" + side: p},
        dict(reasons=[]),
        now,
        c,
    )


def codes(d):
    return {r["code"] for r in d["reasons"]}


@pytest.mark.parametrize("side", ["yes", "no"])
def test_symmetric_qualifying_signal(market, now, side):
    d = signal(market, now, side=side)
    assert d["decision"] == "TRADE_CANDIDATE"
    assert d["side"] == side
    assert d["net_ev"] == pytest.approx(0.20)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("move,accepted", [(0.0014999, False), (0.0015, True), (0.0015001, True)])
def test_move_boundary(market, now, side, move, accepted):
    assert ("INSUFFICIENT_MOVE" not in codes(signal(market, now, side=side, move=move))) == accepted


@pytest.mark.parametrize(
    "field,value,code,accepted",
    [
        ("ask", 0.5199, "PRICE_TOO_LOW", False),
        ("ask", 0.52, "PRICE_TOO_LOW", True),
        ("ask", 0.80, "PRICE_TOO_HIGH", True),
        ("ask", 0.8001, "PRICE_TOO_HIGH", False),
        ("spread", 0.03, "SPREAD_TOO_WIDE", True),
        ("spread", 0.030001, "SPREAD_TOO_WIDE", False),
        ("edge", 0.05, "INSUFFICIENT_EDGE", True),
        ("edge", 0.049999, "INSUFFICIENT_EDGE", False),
        ("age", 60, "STALE_CANDLE", True),
        ("age", 60.001, "STALE_CANDLE", False),
        ("age", -1, "STALE_CANDLE", False),
        ("remaining", 180, "TOO_LATE", True),
        ("remaining", 179.9, "TOO_LATE", False),
        ("remaining", 600, "TOO_EARLY", True),
        ("remaining", 600.1, "TOO_EARLY", False),
        ("confirm", 0, "CONFIRMATION_FAILED", False),
        ("confirm", -0.001, "CONFIRMATION_FAILED", False),
    ],
)
def test_entry_boundaries(market, now, field, value, code, accepted):
    assert (code not in codes(signal(market, now, **{field: value}))) == accepted


@pytest.mark.parametrize("edge,accepted", [(0.15, True), (0.149999, False)])
def test_proxy_edge(market, now, edge, accepted):
    assert ("PROXY_EDGE_FAILED" not in codes(signal(market, now, source="Coinbase", edge=edge))) == accepted


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "LOW"),
        (19.9, "LOW"),
        (20, "NORMAL"),
        (74.9, "NORMAL"),
        (75, "HIGH"),
        (89.9, "HIGH"),
        (90, "EXTREME"),
        (100, "EXTREME"),
    ],
)
def test_regime_boundaries(value, expected):
    assert regime(value) == expected


@pytest.mark.parametrize(
    "vol,accepted", [("LOW", False), ("NORMAL", True), ("HIGH", False), ("EXTREME", False), (None, False)]
)
def test_regime_gate(market, now, vol, accepted):
    d = signal(market, now, c=volatility_model(), vol=vol)
    assert (d["decision"] == "TRADE_CANDIDATE") == accepted


@pytest.mark.parametrize(
    "settings",
    [
        dict(min_entry_price=0.8, max_entry_price=0.8),
        dict(max_spread=-1),
        dict(no_new_entry=600),
        dict(min_edge=-1),
        dict(volatility_boundaries=(20, 10, 90)),
        dict(volatility_boundaries=(20, 75, 101)),
        dict(allowed_volatility_regimes=("ELEVATED",)),
        dict(max_candle_age=-1),
        dict(bankroll=0),
        dict(bankroll=float("nan")),
        dict(mode="live"),
        dict(volatility_min_samples=60),
        dict(confirmation_minutes=0),
    ],
)
def test_invalid_configuration(settings):
    with pytest.raises(ValueError):
        Momentum(**settings)


def minute_ticks(minutes=40):
    return [Tick(float(t), float(t), 100 * math.exp(0.00001 * (t % 2))) for t in range(minutes * 60)]


def test_causal_history_midrank_gaps_and_warmup():
    c = Momentum()
    ticks = minute_ticks()
    a = observations(ticks, 2400, c)
    assert a["volatility_regime"] == "NORMAL"
    assert a["volatility_percentile"] == 50
    assert a["volatility_samples"] == 39
    assert observations(ticks + [Tick(2500, 2500, 1e9)], 2400, c) == a
    assert observations(ticks + [Tick(2399.5, 2500, 1e9)], 2400, c) == a
    assert observations(ticks[:600], 600, c)["volatility_percentile"] is None
    gappy = [t for t in ticks if not 2370 <= t.source <= 2375]
    assert observations(gappy, 2400, c)["volatility_regime"] is None
    assert observations(gappy, 2400, c)["return_180"] is None
    assert observations(ticks[:-120], 2400, c)["candle_age"] is None


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_prices(price):
    with pytest.raises(ValueError):
        Tick(0, 0, price)


def test_registry_immutable_and_inactive(store):
    c = Momentum()
    assert all(not v["active"] for v in definitions(store).values())
    register(store, c)
    with pytest.raises(ValueError, match="immutable"):
        register(store, replace(c, min_move=0.002))
    key = register(store, replace(c, model_version="v2", min_move=0.002))
    assert not definitions(store)[key]["active"]
    activate(store, key, True)
    assert definitions(store)[key]["active"]
    assert not definitions(store)["conservative-confirmed-momentum:v1"]["active"]


def test_same_snapshot_independent_decisions_and_control_regression(store, config, market, now, monkeypatch):
    from btc15 import engine as module
    from btc15.engine import Engine

    calls = []

    def features(*args):
        return dict(
            sigma=0.0001,
            reference=market.spec.strike * 1.002,
            history_seconds=1000,
            max_gap=0,
            shock=False,
            volatility_disagreement=0,
            regime="NORMAL",
        )

    def probability(*args):
        calls.append(args[2])
        return dict(p_yes=0.85, p_no=0.15, conservative_yes=0.83, conservative_no=0.13)

    monkeypatch.setattr(module, "features", features)
    monkeypatch.setattr(module, "probability", probability)
    monkeypatch.setattr(
        module,
        "observations",
        lambda *a: dict(
            return_180=0.001,
            candle_age=0,
            reference_source="CF Benchmarks BRTI",
            volatility_regime="HIGH",
            volatility_samples=40,
        ),
    )
    for c in (Momentum(paths=config.paths), volatility_model(paths=config.paths)):
        # v1 defaults cannot be silently redefined, including test path count.
        c = replace(c, model_version="test")
        activate(store, register(store, c), True)
    group = ModelGroup(store, config, execute=False)
    baseline = Engine(store, config, execute=False)
    for e in [*group.engines, baseline]:
        e.connection = "test"
        e.markets[market.ticker] = market
        e.books[market.ticker] = Book(
            yes={D(".57"): D(10)}, no={D(".40"): D(10)}, received=now, source_time=now, valid=True
        )
        e.ticks = [
            Tick(now - 1, now - 1, market.spec.strike * 1.002),
            Tick(now, now, market.spec.strike * 1.002),
        ]
        e.healthy = e.clock_ok = e.exchange_open = True
        e.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
        e.series_fee_changes = []
        e.fee_changes = {market.event_ticker: []}
        for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
            store.transition(e.run_id, "PAPER", market.ticker, state, now)
    row = dict(id="shared", received=now, connection_id="test", payload=dict(type="heartbeat", msg={}))
    assert group.ingest(row)
    assert len(calls) == 1
    baseline.ingest(row)
    a, b = [e.latest[market.ticker] for e in (baseline, group.control)]
    for key in ("decision", "reasons", "probability", "features", "net_ev", "side"):
        assert a[key] == b[key]
    decisions = [e.latest[market.ticker] for e in group.engines]
    assert {d["snapshot_id"] for d in decisions} == {"shared"}
    assert decisions[1]["decision"] == "TRADE_CANDIDATE"
    assert "VOLATILITY_HIGH" in codes(decisions[2])
    assert all(e.executor.risk.config.bankroll == 1000 for e in group.engines)
    assert len({e.run_id for e in group.engines}) == 3
    with pytest.raises(ValueError):
        ModelGroup(store, config, mode="LIVE")


def test_independent_trades_bankroll_resume_and_settlement(store, market, now, config):
    from btc15.execution import PaperExecutor

    engines = []
    for c in (Momentum(), volatility_model()):
        key = c.model_id + ":" + c.model_version
        e = PaperExecutor(store, key, "PAPER", c)
        for state in (
            "DISCOVER_MARKET",
            "VALIDATE_MARKET",
            "WARMUP",
            "ENTRY_WINDOW",
            "EVALUATING",
            "TRADE_CANDIDATE",
        ):
            store.transition(key, "PAPER", market.ticker, state, now)
        b = Book(yes={D(".57"): D(10)}, no={D(".40"): D(10)}, received=now, source_time=now, valid=True)
        d = signal(market, now, c=c)
        order = e.submit(market, b, d, key, now, True)
        assert order is not None
        e.aggressive(market, b, now + 1)
        assert e.positions[market.ticker].quantity == 1
        assert e.submit(market, b, d, "duplicate", now + 1, True) is None
        e.settle(market, "yes", market.close_time + 1)
        engines.append(e)
    assert len(store.list(kind="fill")) == 2
    assert len(store.list(kind="trade_result")) == 2
    for e in engines:
        restored = PaperExecutor(store, e.run_id + "-new", "PAPER", e.config)
        restored.restore_daily_history()
        assert restored.risk.realized == e.risk.realized
        assert restored.risk.day(now)["trades"] == 1
        assert e.model_identity["model_version"] == "v1"
    engines[0].risk.realized = -100
    assert engines[1].risk.realized > 0


def test_empty_comparison_does_not_invent_performance(store):
    rows = comparison(store)
    assert len(rows) == 3
    assert all(r["win_rate"] is None and r["net_pnl"] == 0 and r["open_trades"] == 0 for r in rows)


def test_group_restart_deactivation_and_halt(store, config, market, now):
    c = Momentum()
    key = register(store, c)
    activate(store, key, True)
    group = ModelGroup(store, config, run_id="research")
    group.checkpoint()
    assert len(group.engines) == 2
    child = group.engines[1]
    child.executor.risk.realized = 7
    group.checkpoint()
    activate(store, key, False)
    resumed = ModelGroup(store, config, run_id="research", resume=True)
    assert len(resumed.engines) == 2
    assert not resumed.engines[1].entries_active
    assert resumed.engines[1].executor.risk.realized == 7
    assert resumed.control.executor.risk.realized == 0
    resumed.halt(now)
    assert all(e.executor.risk.halted for e in resumed.engines)
    assert all(store.load_checkpoint(e.run_id)["risk"]["halted"] for e in resumed.engines)
    with pytest.raises(ValueError, match="manifest"):
        ModelGroup(store, config, run_id="unrelated", resume=True)


def test_group_collector_fetches_once_and_resumes(store, config, tmp_path, raw, series, monkeypatch):
    import asyncio

    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    activate(store, register(store, Momentum()), True)
    activate(store, register(store, volatility_model()), True)
    client = runner.KalshiClient
    created = []

    class Counted(client):
        def __init__(self, *args):
            created.append(1)
            super().__init__(*args)

    monkeypatch.setattr(runner, "KalshiClient", Counted)
    settings = Settings(data_dir=str(tmp_path))
    for _ in range(2):
        result = asyncio.run(
            runner.collect(
                settings, config, store, paper=True, duration=0.1, managed_run="multi", multi_model=True
            )
        )
        assert result == "multi"
    assert len(created) == 2  # one client per session, not one per model
    assert len(store.list(kind="run")) == 3
    assert len(store.list(kind="resume")) == 3
    status = store.list(kind="status", run_id="multi", newest_first=True, limit=1)[0]["body"]
    assert len(status["models"]) == 3
    assert not status["live_enabled"]
    assert all(store.load_checkpoint(m["run_id"]) for m in status["models"])
    store.acquire("collector", "released")
    store.release("collector", "released")


def test_comparison_realized_only_and_version_separation(store):
    from dataclasses import asdict

    from btc15.models import identity

    for version in ("v1", "v2"):
        c = Momentum(model_version=version)
        store.add(
            "run",
            dict(config=asdict(c), model=identity(c), versions=dict(config=c.version)),
            version,
            "PAPER",
            1,
        )
    op = dict(
        features=dict(volatility_regime="NORMAL", minute_volatility=0.001),
        conservative_probability=0.8,
        net_ev=0.1,
        spread=0.02,
        seconds_remaining=300,
    )
    for key, ts in (("closed", 10), ("open", 30)):
        store.add("opportunity", op, "v1", "PAPER", ts, opportunity_id=key, record_id=key)
        store.add(
            "fill", dict(action="buy", quantity=1, price=0.6, fee=0.02), "v1", "PAPER", ts, opportunity_id=key
        )
    store.add(
        "trade_result", dict(net_pnl=0.38, reason="SETTLEMENT"), "v1", "PAPER", 20, opportunity_id="closed"
    )
    report = comparison(store, now=40)
    v1 = next(
        r for r in report if r["model_id"] == "conservative-confirmed-momentum" and r["model_version"] == "v1"
    )
    v2 = next(r for r in report if r["model_version"] == "v2")
    assert v1["net_pnl"] == 0.38 and v1["open_trades"] == 1 and v1["settled_trades"] == 1
    assert v1["paper_equity_at_cost"] == 1000.38
    assert v1["open_entry_debit"] == 0.62
    assert v1["regimes"][1]["signals_observed"] == 2
    assert v2["net_pnl"] == 0 and v2["open_trades"] == 0
    recent = comparison(store, days=1, now=86425)
    v1 = next(
        r for r in recent if r["model_id"] == "conservative-confirmed-momentum" and r["model_version"] == "v1"
    )
    assert v1["settled_trades"] == 0 and v1["net_pnl"] == 0
    assert v1["open_trades"] == 1


def test_real_features_to_independent_fills_and_settlement(store, config, market, now, raw, series):
    import json

    for c in (Momentum(), volatility_model()):
        activate(store, register(store, c), True)
    group = ModelGroup(store, config, mode="BACKTEST")
    count = 0

    def ingest(payload, when):
        nonlocal count
        count += 1
        assert group.ingest(
            dict(
                id=f"research-{count}",
                received=when,
                connection_id="synthetic",
                monotonic_ns=int((when - now + 3000) * 1e9),
                payload=payload,
            )
        )

    # Warm up once on the shared reference stream before introducing a market.
    for i in range(2401):
        when = now - 2400 + i
        amplitude = (0.5, 1, 1.5)[int(when // 60) % 3]
        price = market.spec.strike + 240 + i * 0.1 + amplitude * math.sin(i * math.pi / 2)
        ingest(
            dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(dict(type="value", id="BRTI", time=when * 1000, value=str(price))),
                ),
            ),
            when,
        )
    ingest(
        dict(
            type="metadata",
            msg=dict(
                series=series,
                markets=[raw],
                clock_skew=0,
                exchange_status=dict(trading_active=True),
                fee_changes={market.event_ticker: []},
                series_fee_changes=[],
                synthetic=True,
            ),
        ),
        now,
    )
    quote = dict(
        type="orderbook_snapshot",
        msg=dict(market_ticker=market.ticker, yes_dollars_fp=[[".57", "10"]], no_dollars_fp=[[".60", "10"]]),
    )
    ingest(quote, now + 0.01)
    ingest(quote, now + 0.51)
    conservative = group.engines[1]
    assert market.ticker in conservative.executor.positions
    entered = [e for e in group.engines if market.ticker in e.executor.positions]
    assert not group.control.executor.positions  # control rejects the low entry price
    for e in entered:
        op = e.latest[market.ticker]
        assert op["features"]["volatility_percentile"] is not None
    ingest(
        dict(type="settlement", msg=dict(market_ticker=market.ticker, result="yes")), market.close_time + 1
    )
    assert len(store.list(kind="trade_result")) == len(entered)
    for r in store.list(kind="trade_result"):
        assert r["body"]["settlement_result"] == "yes"
        assert r["body"]["net_pnl"] > 0
        assert r["body"]["return_on_capital"] > 0
        assert r["body"]["model"]["config_hash"]


def test_mixed_legacy_analytics_requires_model_selection(store):
    from btc15.analytics import metrics

    for c in (Momentum(), volatility_model()):
        store.add(
            "opportunity",
            dict(
                model=dict(model_id=c.model_id, model_version=c.model_version),
                versions=dict(config=c.version),
            ),
            c.model_id,
            "PAPER",
            1,
        )
    report = metrics(store)
    assert report["mixed_models"]
    assert report["net_pnl"] is None
    assert "pooled results are suppressed" in report["limitations"][0]


def test_future_tick_cannot_trigger_momentum_entry(market, now):
    c = Momentum()
    b = Book(yes={D(".57"): D(10)}, no={D(".40"): D(10)}, received=now, source_time=now, valid=True)
    d = evaluate(
        market,
        b,
        Tick(now + 1, now, market.spec.strike * 1.002),
        dict(return_180=0.001, candle_age=0, reference_source="CF Benchmarks BRTI"),
        dict(conservative_yes=0.9, p_yes=0.92),
        dict(reasons=[]),
        now,
        c,
    )
    assert "FUTURE_REFERENCE" in codes(d)
    assert d["decision"] == "NO_TRADE"


def test_momentum_quote_burst_reuses_probability(store, market, now, monkeypatch):
    from btc15 import engine as module

    engine = module.Engine(store, Momentum(), execute=False)
    engine.markets[market.ticker] = market
    engine.books[market.ticker] = Book(
        yes={D(".57"): D(10)}, no={D(".40"): D(10)}, received=now, source_time=now, valid=True
    )
    engine.ticks = [
        Tick(now - 1, now - 1, market.spec.strike + 200),
        Tick(now, now, market.spec.strike + 200),
    ]
    calls = []
    original = module.probability

    def counted(*args):
        calls.append(args[2])
        return original(*args)

    monkeypatch.setattr(module, "probability", counted)
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        store.transition(engine.run_id, "PAPER", market.ticker, state, now)
    engine.process(now, "initial", "heartbeat", {})
    for i in range(1, 101):
        engine.process(now + i / 1000, str(i), "orderbook_delta", {"market_ticker": market.ticker})
    assert calls == [now]
    engine.process(now + 1.01, "next", "orderbook_delta", {"market_ticker": market.ticker})
    assert calls == [now, now + 1.01]


def test_observation_collector_includes_all_active_models_without_orders(
    store, config, tmp_path, raw, series, monkeypatch
):
    import asyncio

    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    for model in [Momentum(), volatility_model()]:
        activate(store, register(store, model), True)
    run = asyncio.run(
        runner.collect(Settings(data_dir=str(tmp_path)), config, store, multi_model=True, duration=0.1)
    )
    status = store.list(kind="status", run_id=run, newest_first=True, limit=1)[0]["body"]
    assert len(status["models"]) == 3
    assert not status["paper_execution"] and not status["live_enabled"]
    assert not store.list(kind="order") and not store.list(kind="fill")
