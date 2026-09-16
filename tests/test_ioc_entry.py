"""Best-ask IOC matching and exact price-boundary regressions; synthetic data."""

from dataclasses import replace

import pytest
import test_position_management as management_tests
from test_strategy_reverification import initialize, make_book

from btc15.domain import D
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate

scenario = management_tests.scenario


def candidate(side="yes"):
    return dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.999)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("spread,allowed", [(".04", True), (".041", False)])
def test_spread_boundary(side, spread, allowed, market, config, now):
    ask = D(".85") + D(spread)
    book = make_book(side, ".85", str(ask), now)
    decision = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + (100 if side == "yes" else -100)),
        dict(volatility_disagreement=0, regime="NORMAL"),
        {"conservative_" + side: 0.99},
        dict(score=100, reasons=[]),
        now,
        config,
    )
    assert ("SPREAD" not in [r["code"] for r in decision["reasons"]]) == allowed
    assert D(str(decision["spread"])) == D(spread)
    assert D(str(book.summary()["spread"])) == D(spread)


def test_exact_net_edge_boundary(market, config, now):
    c = replace(config, taker_fee_rate=0, slippage=0, min_edge=0.03, min_ev=0.03)
    book = make_book("yes", ".88", ".90", now)
    p = 0.9301  # .90 price + .0001 fee-rounding bound + .03 required edge
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=p),
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert d["decision"] == "TRADE_CANDIDATE" and d["net_ev"] == 0.03


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_ioc_uses_fresh_book_on_heartbeat_after_latency(scenario, market, config, now, side, mode):
    c = replace(config, passive=False, resting_limit_recheck=True)
    s = scenario(side=side, mode=mode, buy=False, chosen=c)
    s.e.process(now, "submit", "orderbook_snapshot", dict(market_ticker=market.ticker))
    order = s.e.executor.orders[market.ticker]
    s.clock[0] = now + 0.1
    s.e.process(now + 0.1, "too-early", "heartbeat", {})
    assert order.active and not s.e.executor.positions
    s.clock[0] = now + 0.25
    s.e.process(now + 0.25, "eligible", "heartbeat", {})
    pos = s.e.executor.positions[market.ticker]
    assert not order.active and pos.quantity == order.quantity
    assert D(str(pos.cost)) / D(pos.bought) == D(".9")
    assert pos.fees > 0


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("coarse_ticks", [False, True])
def test_actual_ask_fills_even_with_off_grid_slippage(store, market, config, now, side, coarse_ticks):
    if coarse_ticks:
        market = replace(market, price_ranges=[dict(start="0.01", end="0.99", step="0.01")])
    c = replace(config, passive=False, slippage=0.0025)
    e = initialize(store, market, now, c, "PAPER")
    book = make_book(side, ".88", ".90", now)
    order = e.submit(market, book, candidate(side), "op", now, True)
    assert D(order.limit) <= D(".9025")
    e.aggressive(market, book, now + 0.25)
    fills = store.list(kind="fill")
    assert len(fills) == 1
    assert fills[0]["body"]["price"] == 0.9
    assert fills[0]["body"]["slippage"] == 0


def test_ioc_walks_depth_only_inside_cap(store, market, config, now):
    c = replace(config, passive=False, slippage=0.002)
    e = initialize(store, market, now, c, "PAPER")
    book = make_book("yes", ".88", ".90", now)
    book.no = {D(".10"): D("1.25"), D(".099"): D("1.50"), D(".097"): D("20")}
    order = e.submit(market, book, candidate(), "op", now, True)
    e.aggressive(market, book, now + 0.25)
    fills = store.list(kind="fill")
    assert sorted((r["body"]["price"], r["body"]["quantity"]) for r in fills) == [(0.9, 1.25), (0.901, 1.5)]
    assert not order.active and order.remaining == 2.25
    assert not e.retry_ready(market.ticker, now + 10)
    e.aggressive(market, book, now + 1)
    assert len(store.list(kind="fill")) == 2


@pytest.mark.parametrize("fault", ["stale", "invalid", "halt", "quarantine", "window"])
def test_ioc_rechecks_execution_safety(store, market, config, now, fault):
    e = initialize(store, market, now, replace(config, passive=False), "PAPER")
    book = make_book("yes", ".88", ".90", now)
    order = e.submit(market, book, candidate(), "op", now, True)
    when = now + 0.25
    if fault == "stale":
        when = now + 6
    if fault == "invalid":
        book.valid = False
    if fault == "halt":
        e.risk.halted = True
    if fault == "quarantine":
        e.quarantines[market.ticker] = {}
    if fault == "window":
        when = market.close_time - 120
    e.aggressive(market, book, when)
    assert not order.active and not store.list(kind="fill")


def test_zero_latency_ioc_matches_at_submission(store, market, config, now):
    e = initialize(store, market, now, replace(config, passive=False, latency_seconds=0), "PAPER")
    order = e.submit(market, make_book("yes", ".88", ".90", now), candidate(), "op", now, True)
    assert not order.active and e.positions[market.ticker].quantity == 5


def test_ioc_cap_never_exceeds_max_entry_price(store, market, config, now):
    c = replace(config, passive=False, min_edge=0.005, min_ev=0.005)
    e = initialize(store, market, now, c, "PAPER")
    book = make_book("yes", ".98", ".989", now)
    order = e.submit(market, book, candidate(), "op", now, True)
    assert order.limit == c.max_entry_price == 0.99
    moved = make_book("yes", ".98", ".991", now + 0.25)
    e.aggressive(market, moved, now + 0.25)
    assert not order.active and not store.list(kind="fill")


def test_ioc_fractional_depth_is_floored_not_invented(store, market, config, now):
    e = initialize(store, market, now, replace(config, passive=False), "PAPER")
    book = make_book("yes", ".88", ".90", now, depth=".019")
    order = e.submit(market, book, candidate(), "op", now, True)
    e.aggressive(market, book, now + 0.25)
    assert e.positions[market.ticker].quantity == 0.01
    assert order.remaining == 4.99 and not order.active


def test_ioc_limit_does_not_charge_slippage_twice(scenario, market, config, now):
    c = replace(config, passive=False, resting_limit_recheck=True)
    s = scenario(buy=False, chosen=c)
    s.p["conservative_yes"] = 0.9384  # Exactly 3 cents after ask fees and slippage.
    s.e.process(now, "submit", "orderbook_snapshot", dict(market_ticker=market.ticker))
    order = s.e.executor.orders[market.ticker]
    assert order.active
    s.clock[0] = now + 0.25
    s.e.process(now + 0.25, "match", "heartbeat", {})
    assert s.e.executor.positions[market.ticker].quantity == 5
