from dataclasses import replace

import pytest
from test_execution import decision, ready

from btc15.domain import D
from btc15.strategies.settlement_edge.economics import entry_economics


def report(market, book, config, now, **kwargs):
    inputs = dict(
        side="yes", price=0.85, quantity=10, probability=0.88, stage="evaluation", entry_slippage=0.002
    )
    inputs.update(kwargs)
    return entry_economics(
        market,
        book,
        config=replace(config, fee_balance_precision="0.0001", take_profit=0.99),
        now=now,
        **inputs,
    )


def test_both_leg_arithmetic(market, book, config, now):
    r = report(market, book, config, now)
    # .07 * 10 * .85 * .15 = .08925, rounded at account precision.
    assert r["entry_fee"] == 0.0893
    assert r["total_entry_cost"] == 8.6093
    assert r["settlement"]["ev_total"] == pytest.approx(0.1907)
    assert r["target_sale"]["exit_fee"] == 0.007
    assert r["target_sale"]["net_total"] == pytest.approx(1.2837)
    assert r["target_sale"]["probability_weighted_proxy_total"] == pytest.approx(0.09654)
    assert r["stop_exit"]["assumed_sale_price"] <= r["stop_exit"]["trigger_price"]
    assert r["stop_exit"]["net_total"] < 0
    assert r["reporting_only"]


def test_depth_and_no_duplicate_slippage(market, book, config, now):
    book.yes = {D(".84"): D("4"), D(".83"): D("6")}
    r = report(market, book, config, now)
    unwind = r["immediate_unwind"]
    assert unwind["status"] == "AVAILABLE"
    assert unwind["depth_proceeds"] == 8.34
    assert unwind["net_total"] == pytest.approx(8.34 - unwind["exit_fee"] - 8.6093)
    cap = report(market, book, config, now, stage="order_limit", entry_slippage=0)
    assert cap["total_entry_cost"] == 8.5893
    assert cap["immediate_unwind"]["net_total"] - unwind["net_total"] == pytest.approx(0.02)
    book.yes = {D(".84"): D("4")}
    r = report(market, book, config, now)
    assert r["immediate_unwind"]["status"] == "INSUFFICIENT_DEPTH"
    assert r["immediate_unwind"]["net_total"] is None
    book.received = now - 100
    assert report(market, book, config, now)["immediate_unwind"]["status"] == "NO_FRESH_BOOK"


def test_fractional_no_and_unavailable(market, book, config, now):
    r = report(market, book, config, now, side="no", quantity=1.25)
    assert r["quantity"] == 1.25
    assert r["immediate_unwind"]["depth_proceeds"] == 0.125
    assert report(market, book, config, now, quantity=0)["status"] == "UNAVAILABLE"
    assert report(market, book, config, now, probability=None)["status"] == "UNAVAILABLE"


def test_order_reports_actual_quantity_without_changing_order(store, market, book, config, now):
    executor = ready(store, market, now, config)
    order = executor.submit(market, book, decision(), "op", now, True)
    assert order
    body = store.list(kind="order")[0]["body"]
    r = body["entry_economics"]
    assert r["quantity"] == order.quantity
    assert r["entry_price"] == order.limit
    assert r["entry_slippage_total"] == 0
    assert r["stage"] == "order_limit"
    assert body["risk_reserved"] == executor.risk.reserved[market.ticker]


def test_disabled_target_and_unsupported_stop(market, book, config, now):
    r = entry_economics(
        market, book, "yes", 0.001, 10, 0.8, replace(config, take_profit=None), now, stage="evaluation"
    )
    assert r["target_sale"]["status"] == "DISABLED"
    assert r["stop_exit"]["status"] == "NO_SUPPORTED_TICK"
