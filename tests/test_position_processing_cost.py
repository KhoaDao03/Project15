"""Unchanged quotes must not copy/checkpoint the portfolio; changes remain atomic."""

import copy

import pytest
import test_position_management as management_tests

from btc15.domain import D

scenario = management_tests.scenario


def test_unchanged_position_and_unrelated_exit_depth_are_read_only(scenario, market, now, monkeypatch):
    s = scenario()
    e = s.e.executor
    s.refresh(now + 1)
    book = s.e.books[market.ticker]
    e.monitor(market, book, s.p, now + 1, "initial-mark")
    e.exit_consumed[("closed-other-market", D(".90"))] = D(5)
    before = copy.deepcopy(e.snapshot())

    def unexpected(*args, **kwargs):
        raise AssertionError("Unchanged quote must not start a transaction or create a snapshot")

    with monkeypatch.context() as m:
        m.setattr(e, "snapshot", unexpected)
        m.setattr(e.store, "transaction", unexpected)
        for i in range(1000):
            e.monitor(market, book, s.p, now + 1, str(i))
            e.observe_exit_liquidity(market, book, now + 1, continuous=True, source_time=now + 1)
    assert e.snapshot() == before


@pytest.mark.parametrize("change", ["extrema", "exit", "liquidity"])
def test_position_changes_still_roll_back_on_checkpoint_failure(scenario, market, now, monkeypatch, change):
    s = scenario()
    e = s.e.executor
    s.refresh(now + 1, ".90")
    if change == "exit":
        s.refresh(now + 1, ".50")
    if change == "liquidity":
        e.exit_consumed[(market.ticker, D(".90"))] = D("200.00")
    before = copy.deepcopy(e.snapshot())

    def fail(*args):
        raise OSError("synthetic checkpoint failure")

    monkeypatch.setattr(e.store, "checkpoint", fail)
    with pytest.raises(OSError):
        if change == "liquidity":
            e.observe_exit_liquidity(
                market, s.e.books[market.ticker], now + 1, continuous=True, source_time=now + 1
            )
        else:
            e.monitor(market, s.e.books[market.ticker], s.p, now + 1, "change")
    assert e.snapshot() == before
