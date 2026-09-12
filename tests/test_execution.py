from dataclasses import replace

import pytest

from btc15.config import Settings
from btc15.execution import LiveTrader, PaperExecutor, live_payload
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import FeeAccumulator, Risk, evaluate


def ready(store, market, now, config):
    for state in [
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ]:
        store.transition("run", "PAPER", market.ticker, state, now)
    return PaperExecutor(store, "run", "PAPER", config)


def decision():
    return dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.99)


@pytest.mark.parametrize(
    "remaining,valid", [(480, True), (480.001, False), (120.001, True), (120, False), (0, False), (-1, False)]
)
def test_entry_boundaries(market, book, config, remaining, valid):
    now = market.close_time - remaining
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=0.99),
        dict(score=100, reasons=[]),
        now,
        config,
    )
    assert ("ENTRY_WINDOW" not in [r["code"] for r in d["reasons"]]) == valid


def test_submission_rechecks_time_and_freshness(store, market, book, now, config):
    e = ready(store, market, now, config)
    assert e.submit(market, book, decision(), "op", market.close_time - 120, True) is None
    assert e.submit(market, book, decision(), "op", now, False) is None
    assert not e.orders


def test_passive_touch_no_fill_queue_and_duplicate(store, market, book, now, config):
    e = ready(store, market, now, config)
    o = e.submit(market, book, decision(), "op", now, True)
    assert o and e.submit(market, book, decision(), "op2", now, True) is None
    e.aggressive(market, book, now + 1)
    assert not e.positions  # No traded volume, no passive fill.

    def trade(tid, quantity, when):
        return dict(
            trade_id=tid,
            ts_ms=when * 1000,
            taker_outcome_side="no",
            yes_price_dollars=str(o.limit),
            count_fp=str(quantity),
        )

    # A delayed historical trade before order eligibility cannot consume queue.
    e.trade(market, trade("old", 100, now), now + 1)
    assert not e.positions
    e.trade(market, trade("part", 1.25, now + 1), now + 1)
    assert e.positions[market.ticker].quantity == 1.25
    e.trade(market, trade("part", 1.25, now + 1), now + 1)
    assert e.positions[market.ticker].quantity == 1.25
    e.trade(market, trade("rest", 100, now + 2), now + 2)
    assert e.positions[market.ticker].quantity == o.quantity
    assert store.state("run", market.ticker) == "POSITION_OPEN"
    assert len(store.list(kind="fill")) == 2
    e.settle(market, "no", market.close_time + 1)
    result = store.list(kind="trade_result")[0]["body"]
    assert result["net_pnl"] < 0 and not e.risk.reserved


def test_queue_ahead_is_consumed_first(store, market, book, now, config):
    e = ready(store, market, now, replace(config, passive_discount=0.03))
    # Explicit queue at chosen price, unlike the improved-bid case above.
    book.yes.clear()
    from btc15.domain import D

    book.yes[D(".89")] = D("10")
    o = e.submit(market, book, decision(), "op", now, True)
    assert o.queue == 15
    e.trade(
        market,
        dict(
            trade_id="queue",
            ts_ms=(now + 1) * 1000,
            taker_outcome_side="no",
            yes_price_dollars=".89",
            count_fp="15.50",
        ),
        now + 1,
    )
    assert e.positions[market.ticker].quantity == 0.5


def test_cancel_invalid_signal_retains_partial_risk(store, market, book, now, config):
    e = ready(store, market, now, config)
    o = e.submit(market, book, decision(), "op", now, True)
    e.fill(o, 1, 0.89, now + 1, True)
    e.revalidate(market, dict(decision="NO_TRADE"), now + 2, False)
    assert not o.active and market.ticker in e.risk.reserved
    assert store.state("run", market.ticker) == "POSITION_OPEN"
    e.trade(
        market,
        dict(
            trade_id="late",
            ts_ms=(now + 3) * 1000,
            taker_outcome_side="no",
            yes_price_dollars=".88",
            count_fp="100",
        ),
        now + 3,
    )
    assert e.positions[market.ticker].quantity == 1


def test_risk_limits_and_day_rollover(config):
    r = Risk(replace(config, max_daily_trades=1, max_daily_loss=1))
    assert r.size(0.9, 0) > 0
    r.reserve("a", 0.9, 1, 0)
    assert r.size(0.9, 1) == 0
    r.close("a", -2, 2)
    assert r.size(0.9, 3) == 0
    assert r.size(0.9, 86401) > 0
    r.halted = True
    assert r.size(0.9, 86401) == 0


@pytest.mark.parametrize("mode", ["fixed_contracts", "fixed_dollars", "bankroll_percentage"])
def test_size_never_exceeds_worthless_settlement_budget(config, mode):
    r = Risk(replace(config, sizing_mode=mode, max_trade_dollars=2, max_open_exposure=2))
    q = r.size(0.9, 0)
    assert q * 0.9 <= 2
    r.reserve("a", 0.9, q, 0)
    assert r.size(0.9, 0) == 0


def test_fee_rounding_accumulator():
    f = FeeAccumulator("0.01")
    # Published FCM rounding example: price .055, model fee .00363825.
    assert f.charge(0.055, 1, 0.07) == pytest.approx(0.005)
    total = sum(FeeAccumulator("0.0001").charge(0.89, 2, 0.07) for _ in range(3))
    single = FeeAccumulator("0.0001").charge(0.89, 6, 0.07)
    assert total >= single
    fees = FeeAccumulator("0.01")
    accumulated = sum(fees.charge(0.89, 1, 0.07) for _ in range(5))
    assert accumulated == pytest.approx(FeeAccumulator("0.01").charge(0.89, 5, 0.07))


def test_live_is_impossible_and_payload_is_v2(market):
    with pytest.raises(RuntimeError):
        Settings(mode="LIVE", enable_live=True).guard()
    with pytest.raises(RuntimeError):
        LiveTrader().submit()
    p = live_payload(market, "buy", "no", 0.91, 1.25, "id")
    assert p["side"] == "ask" and p["price"] == "0.0900" and p["count"] == "1.25"
    assert p["exchange_index"] == 2 and p["cancel_order_on_pause"]


def test_hard_stop_latency_depth_and_no_depth_reuse(store, market, book, now, config):
    from btc15.domain import D

    e = ready(store, market, now, config)
    o = e.submit(market, book, decision(), "op", now, True)
    e.fill(o, o.quantity, o.limit, now + 0.5, True)
    book.yes = {D(".50"): D("1.25")}
    book.no = {D(".48"): D("100")}
    book.received = now + 1
    p = {"conservative_yes": 0.8}
    e.monitor(market, book, p, now + 1, "one")
    assert e.positions[market.ticker].quantity == o.quantity
    e.monitor(market, book, p, now + 1.1, "two")
    assert e.positions[market.ticker].quantity == o.quantity
    book.received = now + 2  # New observation after exit eligibility.
    e.monitor(market, book, p, now + 2, "three")
    assert e.positions[market.ticker].quantity == o.quantity - 1.25
    e.monitor(market, book, p, now + 3, "four")
    assert e.positions[market.ticker].quantity == o.quantity - 1.25
    e.settle(market, "no", market.close_time + 1)
    assert store.list(kind="trade_result")[0]["body"]["net_pnl"] < 0


def test_aggressive_entry_respects_depth_and_cancels_remainder(store, market, book, now, config):
    from btc15.domain import D

    c = replace(config, passive=False, slippage=0)
    e = ready(store, market, now, c)
    book.no = {D(".1"): D("1.25")}
    o = e.submit(market, book, decision(), "op", now, True)
    e.aggressive(market, book, now + 0.1)
    assert not e.positions
    e.aggressive(market, book, now + 1)
    assert e.positions[market.ticker].quantity == 1.25
    assert not o.active and o.remaining == o.quantity - 1.25


def test_zero_liquidity_minimum_allows_thin_entry(market, book, config, now):
    from btc15.domain import D

    book.no = {D(".10"): D(".25")}
    args = (
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=0.99),
        dict(score=100, reasons=[]),
        now,
    )
    baseline = evaluate(*args, config)
    relaxed = evaluate(*args, replace(config, min_liquidity=0))
    assert "LIQUIDITY" in {r["code"] for r in baseline["reasons"]}
    assert "LIQUIDITY" not in {r["code"] for r in relaxed["reasons"]}
    assert relaxed["decision"] == "TRADE_CANDIDATE"
    book.no.clear()
    assert evaluate(*args, replace(config, min_liquidity=0))["decision"] == "NO_TRADE"
