"""Real Engine/executor/SQL tests with synthetic quotes and deterministic model outputs."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from btc15 import engine as module
from btc15.config import Settings
from btc15.dashboard import create_app
from btc15.domain import Book, D
from btc15.runner import stop_entries
from btc15.storage import Store
from btc15.strategies.settlement_edge.model import Tick


@pytest.fixture
def scenario(store, config, market, raw, series, now, monkeypatch):
    def make(mode="PAPER", side="yes", buy=True, chosen=None):
        c = chosen or config
        clock = [now]
        features = dict(
            sigma=0.0001,
            reference=market.spec.strike + 200,
            history_seconds=3600,
            max_gap=0,
            shock=False,
            volatility_disagreement=0,
            regime="NORMAL",
        )
        probabilities = dict(
            p_yes=0.995 if side == "yes" else 0.005,
            p_no=0.005 if side == "yes" else 0.995,
            conservative_yes=0.99 if side == "yes" else 0.005,
            conservative_no=0.005 if side == "yes" else 0.99,
        )
        failure = [False]
        delay = [0]

        def model(*args):
            clock[0] += delay[0]
            if failure[0]:
                raise ValueError("Insufficient reference history (synthetic test)")
            return dict(probabilities)

        monkeypatch.setattr(module, "features", lambda *args: dict(features))
        monkeypatch.setattr(module, "probability", model)
        engine = module.Engine(store, c, mode, clock=lambda: clock[0], record_evaluations=False)
        engine.markets[market.ticker] = market
        store.add("market", dict(raw=raw, series=series), engine.run_id, mode, now, market.ticker)
        for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
            engine.state(market.ticker, state, now)
        engine.healthy = engine.clock_ok = engine.exchange_open = True
        engine.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
        engine.series_fee_changes = []
        engine.fee_changes = {market.event_ticker: []}
        price = market.spec.strike + (200 if side == "yes" else -200)
        features["reference"] = price

        def refresh(when, bid=".88", depth="100"):
            clock[0] = when
            engine.ticks = [Tick(when, when, price)]
            levels = {D(bid): D(depth)}
            other = {D(1) - D(bid) - D(".02"): D(100)}
            # At the high take-profit price use a valid, positive opposing level.
            if D(bid) > D(".97"):
                other = {D(".001"): D(100)}
            engine.books[market.ticker] = Book(
                yes=levels if side == "yes" else other,
                no=other if side == "yes" else levels,
                received=when,
                source_time=when,
                valid=True,
            )

        refresh(now)
        order = None
        if buy:
            engine.process(now, "candidate", "orderbook_snapshot", dict(market_ticker=market.ticker))
            order = engine.executor.orders[market.ticker]
            refresh(now + 0.5)
            engine.process(
                now + 0.5,
                "entry-fill",
                "trade",
                dict(
                    market_ticker=market.ticker,
                    trade_id="entry-fill",
                    ts_ms=(now + 0.5) * 1000,
                    taker_outcome_side="no" if side == "yes" else "yes",
                    yes_price_dollars=str(order.limit if side == "yes" else float(1 - D(order.limit))),
                    count_fp="0.40",
                ),
            )
            assert engine.executor.positions[market.ticker].quantity == 0.4
        return SimpleNamespace(
            e=engine,
            clock=clock,
            f=features,
            p=probabilities,
            failure=failure,
            delay=delay,
            refresh=refresh,
            order=order,
            mode=mode,
            side=side,
        )

    return make


def process(s, market, when, event="book", kind="orderbook_snapshot"):
    s.e.process(when, event, kind, dict(market_ticker=market.ticker))


def sells(store):
    return [r for r in store.list(kind="fill", limit=None) if r["body"]["action"] == "sell"]


def block_entries(s, name, now):
    if name == "kill_switch":
        s.e.executor.halt(now)
    elif name == "inactive":
        s.e.entries_active = False
    elif name == "stopping":
        stop_entries(s.e, now)
    elif name == "execution_off":
        s.e.execute = False
    elif name == "daily_loss":
        s.e.executor.risk.day(now)["pnl"] = -s.e.config.max_daily_loss
    elif name == "daily_attempts":
        s.e.executor.risk.day(now)["trades"] = s.e.config.max_daily_trades
    else:
        raise AssertionError(name)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize(
    "blocker", ["kill_switch", "inactive", "stopping", "execution_off", "daily_loss", "daily_attempts"]
)
def test_entry_policy_does_not_block_hard_stop(scenario, store, market, now, mode, side, blocker):
    s = scenario(mode, side)
    block_entries(s, blocker, now + 1)
    for i in (2, 3):
        s.refresh(now + i, ".50")
        process(s, market, now + i, str(i))
    assert sum(D(r["body"]["quantity"]) for r in sells(store)) == D(".40")
    assert not s.e.executor.positions and not s.e.executor.risk.reserved
    result = store.list(kind="trade_result")[0]["body"]
    assert result["reason"] == "HARD_STOP" and result["net_pnl"] < 0
    assert len([r for r in store.list(kind="fill") if r["body"]["action"] == "buy"]) == 1
    assert not s.order.active
    assert len(store.list(kind="trade_result")) == 1
    fills = store.list(kind="fill")
    buys = [r["body"] for r in fills if r["body"]["action"] == "buy"]
    expected = (
        sum(r["body"]["quantity"] * r["body"]["price"] for r in sells(store))
        - sum(r["quantity"] * r["price"] for r in buys)
        - sum(r["body"]["fee"] for r in fills)
    )
    assert result["net_pnl"] == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize(
    "blocker",
    [
        "kill_switch",
        "inactive",
        "stopping",
        "execution_off",
        "daily_loss",
        "daily_attempts",
        "disabled",
        "entry_window",
    ],
)
def test_entry_policies_still_block_new_orders(scenario, store, market, now, config, mode, blocker):
    s = scenario(mode, buy=False, chosen=replace(config, enabled=False) if blocker == "disabled" else config)
    when = market.close_time - 120 if blocker == "entry_window" else now
    if blocker not in ("disabled", "entry_window"):
        block_entries(s, blocker, when)
    s.refresh(when)
    process(s, market, when)
    assert not s.e.executor.orders and not store.list(kind="fill")
    assert not [r for r in store.list(kind="order") if r["body"].get("status") == "submitted"]


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("problem", ["warmup", "shock", "reference_gap", "unavailable"])
@pytest.mark.parametrize("exit_kind,bid", [("HARD_STOP", ".50"), ("TAKE_PROFIT", ".995")])
def test_price_exits_work_without_usable_model(
    scenario, store, market, now, mode, side, problem, exit_kind, bid
):
    s = scenario(mode, side)
    if problem == "warmup":
        s.f["history_seconds"] = 1
    elif problem == "shock":
        s.f["shock"] = True
    elif problem == "reference_gap":
        s.f["max_gap"] = 3
    else:
        s.failure[0] = True
    for i in (2, 3):
        s.refresh(now + i, bid)
        process(s, market, now + i, str(i))
    assert store.list(kind="trade_result")[0]["body"]["reason"] == exit_kind
    assert not s.e.executor.positions and not store.list(kind="health")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("problem", ["warmup", "shock", "reference_gap", "unavailable"])
def test_unusable_model_never_invents_invalidation(scenario, store, market, now, mode, problem):
    s = scenario(mode)
    s.p["conservative_yes"] = 0.01
    if problem == "warmup":
        s.f["history_seconds"] = 1
    elif problem == "shock":
        s.f["shock"] = True
    elif problem == "reference_gap":
        s.f["max_gap"] = 3
    else:
        s.failure[0] = True
    for i in (2, 3):
        s.refresh(now + i, ".80")
        process(s, market, now + i, str(i))
    assert not sells(store) and not store.list(kind="exit_intent")
    assert s.e.executor.positions[market.ticker].quantity == 0.4


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_valid_model_invalidation_still_works_while_halted(scenario, store, market, now, mode, side):
    s = scenario(mode, side)
    s.e.executor.halt(now + 1)
    s.p["conservative_" + side] = 0.60
    for i in (2, 3):
        s.refresh(now + i, ".80")
        process(s, market, now + i, str(i))
    assert store.list(kind="trade_result")[0]["body"]["reason"] == "INVALIDATION"
    assert s.e.executor.risk.halted


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize(
    "hazard",
    [
        "feed",
        "clock",
        "exchange",
        "fees",
        "fee_type",
        "book_invalid",
        "book_old_receipt",
        "book_old_source",
        "book_future_receipt",
        "book_future_source",
        "ref_old_receipt",
        "ref_old_source",
        "ref_future_receipt",
        "ref_future_source",
        "no_reference",
        "lag",
        "lag_during_model",
        "market_halted",
        "market_error",
        "market_inactive",
        "close_during_model",
        "unknown_quality",
    ],
)
def test_common_safety_gates_still_block_sells(scenario, store, market, now, monkeypatch, mode, hazard):
    s = scenario(mode)
    s.e.executor.halt(now + 1)
    # Establish a legitimate exit intent first; the hazardous next frame must not fill it.
    s.refresh(now + 2, ".50")
    process(s, market, now + 2, "intent")
    s.refresh(now + 3, ".50")
    e, b = s.e, s.e.books[market.ticker]
    t = e.ticks[-1]
    if hazard == "feed":
        e.healthy = False
    elif hazard == "clock":
        e.clock_ok = False
    elif hazard == "exchange":
        e.exchange_open = False
    elif hazard == "fees":
        e.series_fee_changes = None
    elif hazard == "fee_type":
        e.series_fees["fee_type"] = "unknown"
    elif hazard == "book_invalid":
        b.valid = False
    elif hazard == "book_old_receipt":
        b.received -= 10
    elif hazard == "book_old_source":
        b.source_time -= 10
    elif hazard == "book_future_receipt":
        b.received += 10
    elif hazard == "book_future_source":
        b.source_time += 10
    elif hazard == "ref_old_receipt":
        e.ticks = [Tick(t.source, t.received - 10, t.price)]
    elif hazard == "ref_old_source":
        e.ticks = [Tick(t.source - 10, t.received, t.price)]
    elif hazard == "ref_future_receipt":
        e.ticks = [Tick(t.source, t.received + 10, t.price)]
    elif hazard == "ref_future_source":
        e.ticks = [Tick(t.source + 10, t.received, t.price)]
    elif hazard == "no_reference":
        e.ticks = []
        s.failure[0] = True
    elif hazard == "lag":
        s.clock[0] += 10
    elif hazard == "lag_during_model":
        s.delay[0] = 10
    elif hazard == "market_halted":
        e.state(market.ticker, "HALTED", now + 3)
    elif hazard == "market_error":
        e.state(market.ticker, "ERROR", now + 3)
    elif hazard == "market_inactive":
        e.markets[market.ticker] = replace(market, status="closed")
    elif hazard == "close_during_model":
        s.refresh(market.close_time - 0.1, ".50")
        s.delay[0] = 0.2
    elif hazard == "unknown_quality":
        monkeypatch.setattr(module, "quality", lambda *args: dict(score=0, reasons=["UNKNOWN_SAFETY_REASON"]))
    else:
        raise AssertionError(hazard)
    when = market.close_time - 0.1 if hazard == "close_during_model" else now + 3
    process(s, market, when, "hazard")
    assert not sells(store) and not store.list(kind="trade_result")
    assert e.executor.positions[market.ticker].quantity == 0.4
    assert e.executor.risk.reserved and e.executor.risk.halted


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("kind", ["trade", "heartbeat", "cfbenchmarks_value", "metadata"])
def test_price_exits_still_require_new_matching_book_event(scenario, store, market, now, mode, kind):
    s = scenario(mode)
    s.e.executor.halt(now + 1)
    s.refresh(now + 2, ".50")
    process(s, market, now + 2, kind=kind)
    assert not sells(store) and not store.list(kind="exit_intent")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_halt_is_not_forced_liquidation_and_stop_latency_remains(scenario, store, market, now, mode):
    s = scenario(mode)
    s.e.executor.halt(now + 1)
    s.refresh(now + 2, ".88")
    process(s, market, now + 2, "no-trigger")
    assert not store.list(kind="exit_intent")
    s.refresh(now + 3, ".50")
    process(s, market, now + 3, "intent")
    s.refresh(now + 3.1, ".50")
    process(s, market, now + 3.1, "too-soon")
    assert not sells(store)
    s.refresh(now + 4, ".50")
    process(s, market, now + 4, "eligible")
    process(s, market, now + 4, "eligible")
    assert len(sells(store)) == len(store.list(kind="trade_result")) == 1
    assert s.e.executor.risk.halted


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1, 2, "bad", True])
def test_invalid_probability_is_not_an_exit_signal(scenario, store, market, now, value):
    s = scenario()
    p = {"conservative_yes": value}
    for i in (2, 3):
        s.refresh(now + i, ".80")
        s.e.executor.monitor(market, s.e.books[market.ticker], p, now + i, str(i))
    assert not sells(store) and not store.list(kind="exit_intent")
    for i in (4, 5):
        s.refresh(now + i, ".50")
        s.e.executor.monitor(market, s.e.books[market.ticker], p, now + i, str(i))
    assert store.list(kind="trade_result")[0]["body"]["reason"] == "HARD_STOP"


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_halted_resume_preserves_risk_and_manages_during_warmup(scenario, store, market, now, mode):
    s = scenario(mode)
    s.e.executor.halt(now + 1)
    old_positions = copy.deepcopy(s.e.executor.positions)
    old = s.e
    s.e = module.Engine(
        store,
        old.config,
        mode,
        run_id=old.run_id,
        resume=True,
        clock=lambda: s.clock[0],
        record_evaluations=False,
    )
    assert s.e.executor.risk.halted and s.e.executor.positions == old_positions
    assert not s.e.healthy and not s.e.clock_ok
    # Regain current data/metadata, but not enough model history. No downtime fills.
    s.e.healthy = s.e.clock_ok = s.e.exchange_open = True
    s.e.series_fees = old.series_fees
    s.e.series_fee_changes = []
    s.e.fee_changes = old.fee_changes
    s.failure[0] = True
    for i in (2, 3):
        s.clock[0] = now + i
        s.e.ticks = [Tick(now + i, now + i, market.spec.strike + 200)]
        s.e.books[market.ticker] = Book(
            yes={D(".50"): D(100)}, no={D(".48"): D(100)}, valid=True, received=now + i, source_time=now + i
        )
        process(s, market, now + i, str(i))
    assert not s.e.executor.positions
    assert store.load_checkpoint(old.run_id)["risk"]["halted"]
    assert len(store.list(kind="trade_result")) == 1
    assert len([r for r in store.list(kind="fill") if r["body"]["action"] == "buy"]) == 1


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_managed_exit_rollback_retry_and_persistent_views(
    scenario, store, market, now, tmp_path, monkeypatch, mode
):
    s = scenario(mode)
    s.e.executor.halt(now + 1)
    s.refresh(now + 2, ".50")
    process(s, market, now + 2, "intent")
    before = copy.deepcopy(s.e.executor.snapshot())
    original = store.checkpoint
    s.refresh(now + 3, ".50")
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("checkpoint unavailable")

        patch.setattr(store, "checkpoint", fail)
        with pytest.raises(OSError):
            process(s, market, now + 3, "exit")
    assert s.e.executor.snapshot() == before
    assert not sells(store) and not store.list(kind="trade_result")
    assert store.checkpoint == original
    process(s, market, now + 3, "exit")
    result = store.list(kind="trade_result")[0]
    url = store.engine.url
    store.engine.dispose()
    reopened = Store(url.render_as_string(hide_password=False))
    try:
        assert not reopened.load_checkpoint(s.e.run_id)["positions"]
        with TestClient(
            create_app(reopened, settings=Settings(data_dir=str(tmp_path)), config=s.e.config)
        ) as client:
            params = {"mode": mode, "run_id": s.e.run_id}
            trades = client.get("/api/trades", params=params).json()
            assert trades["total"] == 1
            assert trades["rows"][0]["body"]["net_pnl"] == result["body"]["net_pnl"]
            metrics = client.get("/api/analytics", params=params).json()
            assert metrics["trades"] == 1 and metrics["net_pnl"] == result["body"]["net_pnl"]
            replay = client.get("/api/replay/" + s.order.opportunity_id)
            assert replay.status_code == 200
            assert any(
                r["kind"] == "fill" and r["body"]["action"] == "sell" for r in replay.json()["timeline"]
            )
    finally:
        reopened.engine.dispose()


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_hard_stop_after_entry_window_closes(scenario, store, market, mode):
    s = scenario(mode)
    for i in (60, 59):
        when = market.close_time - i
        s.refresh(when, ".50")
        process(s, market, when, str(i))
    assert store.list(kind="trade_result")[0]["body"]["reason"] == "HARD_STOP"
    assert not s.e.executor.positions and not s.e.executor.risk.reserved
