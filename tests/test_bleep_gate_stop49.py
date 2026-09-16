from dataclasses import replace

import pytest
from test_exit_execution_v2 import held, quote, sells
from test_strategy_reverification import initialize, make_book

from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("a,b,allowed", [(0.79, 0.9, True), (0.9, 0.849, False), (0.65, 0.9, False)])
def test_only_project15_veto_removed(store, config, market, side, a, b, allowed):
    now = market.close_time - 300
    c = replace(
        config,
        both_models_80_enabled=True,
        project15_probability_veto_enabled=False,
        standard_component_min_probability=0.85,
        bleep_probability_blend_enabled=True,
        entry_value_filters_enabled=False,
        entry_probability_deductions=False,
        min_probability=0.8,
        passive=False,
        min_entry_price=0.75,
        max_entry_price=0.95,
        fixed_contracts=10,
        max_contracts=10,
        max_trade_dollars=10,
    )
    p = dict(
        p_yes=(a + b) / 2 if side == "yes" else 1 - (a + b) / 2,
        p_no=(a + b) / 2 if side == "no" else 1 - (a + b) / 2,
        blend={"project15_p_" + side: a, "bleep": {"p_" + side: b}},
    )
    book = make_book(side, ".89", ".90", now)
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + (100 if side == "yes" else -100)),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    assert "PROJECT15_MIN_PROBABILITY" not in {r["code"] for r in d["reasons"]}
    if (a + b) / 2 < c.min_probability:
        assert "MIN_PROBABILITY" in {r["code"] for r in d["reasons"]}
        return
    e = initialize(store, market, now, c, "PAPER")
    order = e.submit(market, book, {**d, "decision": "TRADE_CANDIDATE"}, "test", now, True)
    assert (order is not None) == allowed
    if allowed:
        e.aggressive(market, book, now + c.latency_seconds)
        fills = [r["body"] for r in store.list(kind="fill")]
        assert sum(f["quantity"] for f in fills) == 10
        assert all(f["price"] == 0.9 for f in fills)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("entry", [0.75, 0.90, 0.95])
@pytest.mark.parametrize("stop", [0.49, 0.65])
def test_fixed_stop_boundary_and_sell_all_at_available_depth(store, market, now, config, side, entry, stop):
    ex = held(store, market, now, replace(config, fixed_stop_price=stop), side)
    ex.positions[market.ticker].cost = 10 * entry
    p = {"conservative_" + side: 0.99}
    ex.monitor(market, quote(now + 1, [(str(stop + 0.01), 10)], side), p, now + 1, "above")
    assert not store.list(kind="exit_intent")
    ex.monitor(market, quote(now + 2, [(str(stop), 10)], side), p, now + 2, "trigger")
    assert not sells(store)
    ex.monitor(
        market,
        quote(now + 2.25, [(str(round(stop - 0.01, 2)), 6), (str(round(stop - 0.02, 2)), 4)], side),
        p,
        now + 2.25,
        "fill",
    )
    assert sorted((f["price"], f["quantity"]) for f in sells(store)) == [
        (round(stop - 0.02, 2), 4),
        (round(stop - 0.01, 2), 6),
    ]
    assert market.ticker not in ex.positions
    assert all(f["reason"] == "HARD_STOP" for f in sells(store))
