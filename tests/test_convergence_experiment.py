from dataclasses import replace
from pathlib import Path

import pytest
import test_position_management as management_tests

from btc15.config import Strategy
from btc15.domain import D
from btc15.strategies.settlement_edge.rules import effective_entry_ceiling, fee_bound

scenario = management_tests.scenario


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize(
    "bid,probability,reason",
    [
        (".96", 0.95, ""),
        (".99", 0.95, "TAKE_PROFIT"),
        (".60", 0.95, "HARD_STOP"),
        (".90", 0.69, "INVALIDATION"),
    ],
)
def test_disabled_hold_exit_preserves_other_exits(
    scenario, config, market, now, side, bid, probability, reason
):
    s = scenario(side=side, chosen=replace(config, hold_value_exit_enabled=False))
    s.refresh(now + 1, bid)
    e = s.e.executor
    e.monitor(market, s.e.books[market.ticker], {"conservative_" + side: probability}, now + 1, "exit")
    assert e.positions[market.ticker].exit_reason == reason


@pytest.mark.parametrize("edge", [0.02, 0.03])
@pytest.mark.parametrize("probability", [0.89, 0.90, 0.95, 0.9790405])
def test_ceiling_matches_exhaustive_supported_prices(market, config, edge, probability):
    c = replace(config, min_edge=edge, min_ev=edge)
    prices = set()
    for band in market.price_ranges:
        price, end, step = (D(band[k]) for k in ("start", "end", "step"))
        while price <= end:
            if (
                D(c.min_entry_price) <= price <= D(c.max_entry_price)
                and probability >= c.min_probability
                and D(probability) - price - D(fee_bound(price, c)) - D(c.slippage) >= D(edge)
            ):
                prices.add(price)
            price += step
    assert effective_entry_ceiling(market, probability, c) == (float(max(prices)) if prices else None)


def test_frozen_control_hash_and_experiment_settings():
    control = Strategy.load("config/settlement-edge-fill-taker-paper.json")
    assert control.version == "1b7ffae5ec144248"
    combined = Strategy.load("config/settlement-edge-combined-paper.json")
    assert combined.min_edge == combined.min_ev == 0.02
    assert not combined.hold_value_exit_enabled
    assert combined.version != control.version
    assert replace(combined, min_edge=0.03, min_ev=0.03, hold_value_exit_enabled=True) == control
    for name in ("entry", "exit"):
        assert Path(f"config/settlement-edge-{name}-paper.json").exists()
    with pytest.raises(ValueError):
        replace(control, hold_value_exit_enabled="false")
