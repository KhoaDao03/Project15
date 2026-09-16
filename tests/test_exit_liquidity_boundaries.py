"""Stream-boundary checks for the observed-liquidity correction."""

import pytest
from test_exit_liquidity_diagnostics import first_exit, frame, sold
from test_position_management import scenario as scenario

from btc15.domain import D


def test_sequence_gap_cannot_establish_liquidity_disappearance(scenario, store, market, now):
    s = scenario()
    first_exit(s, store, market, now)
    s.clock[0] = now + 4
    s.e.sequences[77] = 1
    row = dict(
        id="gap",
        received=now + 4,
        monotonic_ns=round((now + 4) * 1e9),
        connection_id="book",
        payload=dict(
            type="orderbook_snapshot",
            sid=77,
            seq=3,
            msg=dict(market_ticker=market.ticker, yes_dollars_fp=[], no_dollars_fp=[]),
        ),
    )
    assert not s.e.ingest(row)
    key = (market.ticker, D(".50"))
    assert s.e.executor.exit_consumed[key] == D(".10")
    assert frame(s, market, now + 5, "0")  # First snapshot restores the book, not depth credit.
    assert s.e.executor.exit_consumed[key] == D(".10")
    s.e.healthy = True
    assert frame(s, market, now + 6, ".10")
    assert sold(store) == D(".10")


@pytest.mark.parametrize("side", ["yes", "no"])
def test_opposite_side_removal_does_not_replenish_held_side(scenario, store, market, now, side):
    s = scenario(side=side)
    first_exit(s, store, market, now)
    s.clock[0] = now + 4
    opposite = "no" if side == "yes" else "yes"
    wire_price = ".52" if opposite == "no" else ".48"
    assert s.e.ingest(
        dict(
            id="opposite-delta",
            received=now + 4,
            monotonic_ns=round((now + 4) * 1e9),
            connection_id="book",
            payload=dict(
                type="orderbook_delta",
                msg=dict(
                    market_ticker=market.ticker,
                    side=opposite,
                    price_dollars=wire_price,
                    delta_fp="-100",
                    ts_ms=(now + 4) * 1000,
                ),
            ),
        )
    )
    assert s.e.executor.exit_consumed[(market.ticker, D(".50"))] == D(".10")
    assert sold(store) == D(".10")


def test_zero_snapshot_source_time_is_not_replaced_with_receive_time(scenario, store, market, now):
    s = scenario()
    first_exit(s, store, market, now)
    assert frame(s, market, now + 4, ".30", source=0)
    assert s.e.books[market.ticker].source_time == 0
    assert sold(store) == D(".10")
    assert s.e.executor.exit_consumed[(market.ticker, D(".50"))] == D(".10")
