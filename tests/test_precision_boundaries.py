"""Exact price/budget boundaries; synthetic inputs, no venue orders."""

import copy
import json
import random
from dataclasses import replace
from decimal import ROUND_CEILING
from fractions import Fraction

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.domain import D
from btc15.execution import PaperExecutor
from btc15.strategies.settlement_edge.rules import Risk, fee_bound, passive_price


def supported_ticks(market):
    ticks = set()
    for band in market.price_ranges:
        start, end, step = (D(band[key]) for key in ("start", "end", "step"))
        for i in range(int((end - start) // step) + 1):
            p = start + i * step
            if 0 < p < 1:
                ticks.add(p)
    return sorted(ticks)


@pytest.mark.parametrize("precision", ["0.01", "0.0001"])
def test_fixed_target_on_every_supported_tick(market, precision):
    c = Strategy(fee_balance_precision=precision)
    r = Risk(c)
    for price in supported_ticks(market):
        assert r.size(float(price), 0) == 5, price
        assert r.size(price, 0) == 5, price
        details = r.size_details(float(price), 0)
        assert type(details["quantity"]) is int
        json.dumps(details, allow_nan=False)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize(
    "bid,ask,conservative,discount",
    [
        (".91", ".938", ".979", ".01"),
        (".91", ".939", ".979", ".01"),
        (".89", ".901", ".979", ".01"),
        (".90", ".91", ".979", ".01"),
        (".89", ".90", ".979", ".0001"),
        (".098", ".100", ".979", ".002"),
        (".088", ".090", ".979", ".0001"),
        (".88", ".90", ".93001", ".01"),
        (".91", ".938", ".979", "0"),
    ],
)
def test_passive_tick_selection_from_exact_operands(market, now, side, bid, ask, conservative, discount):
    c = Strategy(passive_discount=float(discount))
    b = make_book(side, bid, ask, now)
    deduction = min(D(discount), (D(ask) - D(bid)) / 2, max(D(0), D(conservative) - D(ask) - D(c.min_edge)))
    expected = max(p for p in supported_ticks(market) if p <= D(ask) - deduction)
    if expected >= D(ask):
        expected = max(p for p in supported_ticks(market) if p <= D(ask) - D(".0001"))
    expected = max(D(bid), expected)
    actual = passive_price(market, b, side, float(conservative), c)
    assert type(actual) is float
    assert D(actual) == expected
    assert D(bid) <= D(actual) < D(ask)
    assert market.valid_tick(actual)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("ask", [".936", ".937"])
def test_aggressive_price_does_not_ceil_an_extra_tick(store, market, now, mode, side, ask):
    c = Strategy(passive=False)
    e = initialize(store, market, now, c, mode)
    b = make_book(side, ".91", ask, now)
    order = e.submit(
        market,
        b,
        dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.999),
        "op",
        now,
        True,
    )
    assert order is not None and order.quantity == 5
    expected = D(ask) + D(c.slippage)
    assert D(order.limit) == expected
    b.received = b.source_time = now + 1
    e.aggressive(market, b, now + 1)
    fills = store.list(kind="fill")
    assert len(fills) == 1 and fills[0]["body"]["quantity"] == 5
    assert D(fills[0]["body"]["price"]) == D(ask)
    assert fills[0]["body"]["slippage"] == 0
    assert e.positions[market.ticker].quantity == 5
    assert not order.active
    restored = PaperExecutor(store, e.run_id, mode, c)
    restored.restore(store.load_checkpoint(e.run_id))
    assert restored.risk.reserved == e.risk.reserved
    assert restored.orders[market.ticker].limit == order.limit


@pytest.mark.parametrize("side", ["yes", "no"])
def test_aggressive_off_grid_slippage_does_not_weaken_order_limit(store, market, now, side):
    c = Strategy(passive=False, slippage=0.0025)
    e = initialize(store, market, now, c, "PAPER")
    b = make_book(side, ".91", ".936", now)
    order = e.submit(
        market,
        b,
        dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.999),
        "op",
        now,
        True,
    )
    assert D(order.limit) == D(".938")  # Never round the price cap up.
    # A quote moving above the cap must still cancel without filling.
    b = make_book(side, ".92", ".939", now + 1)
    b.received = b.source_time = now + 1
    e.aggressive(market, b, now + 1)
    assert not store.list(kind="fill") and not order.active
    assert not e.risk.reserved


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_passive_limit_comparison_is_exact_without_epsilon(store, market, now, mode, side):
    c = Strategy()
    e = initialize(store, market, now, c, mode)
    b = make_book(side, ".91", ".938", now)
    order = e.submit(
        market,
        b,
        dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.979),
        "op",
        now,
        True,
    )
    assert D(order.limit) == D(".928") and order.queue == 0
    for i, extra in enumerate((D(".0000000001"), D(0))):
        leg = D(order.limit) + extra
        msg = dict(
            trade_id=str(i),
            count_fp=".10",
            taker_outcome_side="no" if side == "yes" else "yes",
            yes_price_dollars=str(leg if side == "yes" else 1 - leg),
            ts_ms=(now + 1) * 1000,
        )
        e.trade(market, msg, now + 1)
        assert len(store.list(kind="fill")) == i
    assert e.positions[market.ticker].quantity == 0.10


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "0", "1", "-.01", "nonsense"])
def test_invalid_trade_price_rolls_back_before_matching(store, market, now, price):
    c = Strategy()
    e = initialize(store, market, now, c, "PAPER")
    b = make_book("yes", ".91", ".938", now)
    e.submit(
        market,
        b,
        dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.979),
        "op",
        now,
        True,
    )
    before = copy.deepcopy(e.snapshot())
    with pytest.raises(ValueError, match="Invalid trade price"):
        e.trade(
            market,
            dict(
                trade_id="invalid-price",
                count_fp=".10",
                taker_outcome_side="no",
                yes_price_dollars=price,
                ts_ms=(now + 1) * 1000,
            ),
            now + 1,
        )
    assert e.snapshot() == before and not store.list(kind="fill")


@pytest.mark.parametrize(
    "cap",
    [
        "max_trade_dollars",
        "max_open_exposure",
        "max_daily_exposure",
        "fixed_dollars",
        "available_bankroll",
        "bankroll_allocation",
    ],
)
@pytest.mark.parametrize("offset,expected", [("-0.000000001", 4), ("0", 5), ("0.000000001", 5)])
def test_true_budget_boundary_never_rounds_up(cap, offset, expected):
    base, price = Strategy(), D(".902")
    cost = price + D(fee_bound(price, base)) + D(base.slippage)
    budget = 5 * cost + D(offset)
    changes = {cap: float(budget)}
    if cap == "fixed_dollars":
        changes["sizing_mode"] = "fixed_dollars"
    elif cap == "available_bankroll":
        changes = dict(bankroll=float(budget), bankroll_fraction=1)
    elif cap == "bankroll_allocation":
        changes = dict(bankroll=float(budget * 10), bankroll_fraction=0.1)
    r = Risk(replace(base, **changes))
    assert r.size(price, 0) == expected
    assert D(r.size(price, 0)) * cost <= budget
    r.reserve("entry", price, expected, 0)
    assert D(r.reserved["entry"]) == expected * cost


def test_reservations_and_daily_sum_remain_exact():
    price = D(".902")
    base = Strategy()
    cost = price + D(fee_bound(price, base)) + D(base.slippage)
    c = replace(base, max_daily_exposure=float(15 * cost), max_open_exposure=float(5 * cost + D(".3")))
    r = Risk(c)
    r.reserved = {"a": 0.1, "b": 0.2}
    assert r.size(price, 0) == 5
    r.reserved.clear()
    for i in range(3):
        assert r.size(price, 0) == 5
        r.reserve(str(i), price, 5, 0)
        r.reserved.pop(str(i))  # Same release operation as an unfilled cancellation.
    assert D(r.day(0)["exposure"]) == 15 * cost
    assert r.day(0)["trades"] == 3 and r.size(price, 0) == 0


def exact_sizing_oracle(c, r, price):
    """Independent rational whole-contract bound, not the production size() method."""

    def F(x):
        return Fraction(str(x))

    d = r.day(0)
    if r.halted or F(d["pnl"]) <= -F(c.max_daily_loss) or d["trades"] >= c.max_daily_trades:
        return 0
    fee = (D(c.taker_fee_rate) * D(price) * (1 - D(price))).quantize(D(".000001"), rounding=ROUND_CEILING)
    cost = F(price) + F(fee + D(c.fee_balance_precision)) + F(c.slippage)
    bankroll = max(F(0), F(c.bankroll) + F(r.realized))
    reserved = sum((F(x) for x in r.reserved.values()), F(0))
    target = {
        "fixed_contracts": c.fixed_contracts * cost,
        "fixed_dollars": F(c.fixed_dollars),
        "bankroll_percentage": bankroll * F(c.bankroll_fraction),
    }[c.sizing_mode]
    budget = min(
        target,
        F(c.max_trade_dollars),
        bankroll * F(c.bankroll_fraction),
        F(c.max_open_exposure) - reserved,
        F(c.max_daily_exposure) - F(d["exposure"]),
        bankroll - reserved,
    )
    return max(0, min(c.max_contracts, budget // cost))


@pytest.mark.parametrize("mode", ["fixed_contracts", "fixed_dollars", "bankroll_percentage"])
@pytest.mark.parametrize("precision", ["0.01", "0.0001"])
def test_sizing_matches_independent_rational_caps(mode, precision):
    rng = random.Random(15)
    for _ in range(150):
        c = Strategy(
            sizing_mode=mode,
            fee_balance_precision=precision,
            fixed_contracts=rng.randint(1, 10),
            fixed_dollars=rng.randint(1, 10000) / 1000,
            max_contracts=rng.randint(1, 10),
            bankroll=rng.randint(10000, 2000000) / 1000,
        )
        r = Risk(c)
        r.realized = rng.randint(-50000, 50000) / 1000
        r.reserved = {"a": rng.randint(0, 20000) / 1000, "b": rng.randint(0, 2000) / 1000}
        d = r.day(0)
        d.update(
            pnl=rng.randint(-35000, 20000) / 1000,
            exposure=rng.randint(0, 102000) / 1000,
            trades=rng.randint(0, 21),
        )
        r.halted = rng.randrange(25) == 0
        price = rng.randint(1, 999) / 1000
        assert r.size(price, 0) == exact_sizing_oracle(c, r, price)
        assert r.size_details(price, 0)["quantity"] == r.size(price, 0)
        json.dumps(r.size_details(price, 0), allow_nan=False)


def test_daily_risk_restoration_does_not_reintroduce_float_sum_error(store, market, now):
    c = Strategy()
    e = initialize(store, market, now, c, "PAPER")
    for i in range(10):
        e.record(
            "order", dict(status="submitted", quantity=1, risk_reserved=0.1), now + i, market.ticker, "op"
        )
        e.record("trade_result", dict(cost=0.1, fees=0, net_pnl=0.01), now + i, market.ticker, "op")
        e.risk.close(str(i), 0.01, now + i)
    restored = PaperExecutor(store, "restored", "PAPER", c)
    restored.restore_daily_history()
    assert restored.risk.day(now)["exposure"] == 1.0
    assert restored.risk.day(now)["pnl"] == restored.risk.realized == 0.1
    assert e.risk.realized == 0.1


def test_exact_risk_reservation_rolls_back_and_retries(store, market, now, monkeypatch):
    c = Strategy()
    e = initialize(store, market, now, c, "PAPER")
    b = make_book("yes", ".89", ".902", now)
    decision = dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.99)
    before = copy.deepcopy(e.snapshot())
    original = store.checkpoint

    def fail(*args, **kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(store, "checkpoint", fail)
    with pytest.raises(OSError, match="injected"):
        e.submit(market, b, decision, "op", now, True)
    assert e.snapshot() == before and not store.list(kind="order")
    monkeypatch.setattr(store, "checkpoint", original)
    order = e.submit(market, b, decision, "op", now, True)
    assert order is not None and order.quantity == 5
    assert e.risk.day(now)["trades"] == 1
