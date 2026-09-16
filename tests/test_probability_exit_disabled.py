import pytest
from test_exit_execution_v2 import held, quote, sells

from btc15.config import Strategy


@pytest.mark.parametrize("asset", ["active", "eth", "sol", "xrp"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_active_presets_hold_low_probability_but_keep_stop(store, market, now, asset, side):
    c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
    assert c.exit_probability == 0 and not c.hold_value_exit_enabled
    ex = held(store, market, now, c, side)
    p = {"conservative_" + side: 0.0}
    ex.monitor(market, quote(now + 1, [(".73", 10)], side), p, now + 1, "low-probability")
    assert not store.list(kind="exit_intent") and not sells(store)
    ex.monitor(market, quote(now + 2, [(".55", 10)], side), p, now + 2, "stop")
    assert store.list(kind="exit_intent")[0]["body"]["reason"] == "HARD_STOP"


@pytest.mark.parametrize("asset", ["eth", "sol", "xrp"])
def test_paper_profit_exit_still_enabled(store, market, now, asset):
    c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
    assert c.take_profit == 0.99
    ex = held(store, market, now, c)
    ex.monitor(market, quote(now + 1, [(".99", 10)]), {"conservative_yes": 0.0}, now + 1, "profit")
    assert store.list(kind="exit_intent")[0]["body"]["reason"] == "TAKE_PROFIT"
