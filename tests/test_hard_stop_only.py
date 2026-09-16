import pytest
from btc15.config import Strategy
from test_exit_execution_v2 import held, quote, sells


@pytest.mark.parametrize("side", ["yes", "no"])
def test_active_config_only_stops_at_configured_price(store, market, now, side):
    c = Strategy.load("config/settlement-edge-active-paper.json")
    assert c.exit_probability == 0 and c.take_profit is None
    assert not any((c.hold_value_exit_enabled, c.profit_value_exit_enabled, c.standard_cashout_enabled))
    ex = held(store, market, now, c, side)
    for offset, price, prob in [(1, ".99", 0.99), (2, ".70", 0), (3, str(c.fixed_stop_price + 0.01), 0)]:
        ex.monitor(
            market,
            quote(now + offset, [(price, 10)], side),
            {"conservative_" + side: prob},
            now + offset,
            "hold",
        )
    assert not store.list(kind="exit_intent") and not sells(store)
    for offset in [4, 4.25]:
        ex.monitor(
            market,
            quote(now + offset, [(str(c.fixed_stop_price), 10)], side),
            {"conservative_" + side: 0.99},
            now + offset,
            f"stop-{offset}",
        )
    assert sum(f["quantity"] for f in sells(store)) == 10
    assert all(f["reason"] == "HARD_STOP" for f in sells(store))
