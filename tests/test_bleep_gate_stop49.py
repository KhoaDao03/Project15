from dataclasses import replace

import pytest
from test_exit_execution_v2 import held, quote, sells


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
