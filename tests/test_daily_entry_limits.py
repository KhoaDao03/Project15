from dataclasses import replace

import pytest
from test_entry_fill_experiment import candidate
from test_strategy_reverification import initialize, make_book

from btc15.strategies.settlement_edge.rules import Risk


def test_disabled_daily_caps_allow_entry_and_preserve_accounting(store, market, now, config):
    c = replace(config, daily_entry_limits_enabled=False, passive=False, fixed_contracts=10)
    e = initialize(store, market, now, c, "PAPER")
    e.risk.day(now).update(trades=20, exposure=100)
    order = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "over-daily-caps", now, True)
    assert order and order.quantity == 10
    e.aggressive(market, make_book("yes", ".90", ".91", now + 0.3), now + 0.3)
    assert e.positions[market.ticker].quantity == 10
    assert e.risk.day(now)["trades"] == 21
    assert e.risk.day(now)["exposure"] > 100
    assert "DAILY_EXPOSURE_LIMIT" not in e.risk.size_details(0.91, now)["limits"]


@pytest.mark.parametrize("blocker", ["loss", "halt", "open_exposure", "trade_size"])
def test_disabling_daily_caps_preserves_other_limits(config, now, blocker):
    c = replace(
        config, daily_entry_limits_enabled=False, max_trade_dollars=0.1 if blocker == "trade_size" else 10
    )
    r = Risk(c)
    r.day(now).update(trades=100, exposure=1000)
    if blocker == "loss":
        r.day(now)["pnl"] = -c.max_daily_loss
    if blocker == "halt":
        r.halted = True
    if blocker == "open_exposure":
        r.reserved["held"] = c.max_open_exposure
    assert r.size(0.91, now) == 0


def test_daily_caps_remain_enabled_by_default(config, now):
    r = Risk(config)
    r.day(now)["trades"] = config.max_daily_trades
    assert r.size(0.91, now) == 0
    r.day(now).update(trades=0, exposure=config.max_daily_exposure)
    assert r.size(0.91, now) == 0
