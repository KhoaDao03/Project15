from dataclasses import replace

import pytest
from test_exit_execution_v2 import held, quote, sells


def setup(store, market, now, config, side="yes"):
    ex = held(store, market, now, replace(config, profit_value_exit_enabled=True), side)
    ex.positions[market.ticker].cost = 8.2
    ex.positions[market.ticker].fees = 0.1034
    return ex


def step(
    ex, market, now, elapsed, event, *, raw=0.88, adjusted=0.85, levels=None, source=None, received=None
):
    side = ex.positions[market.ticker].side
    ex.monitor(
        market,
        quote(now + (elapsed if received is None else received), levels or [(".90", 10)], side),
        {"p_yes": raw if side == "yes" else 1 - raw, "conservative_" + side: adjusted},
        now + elapsed,
        event,
        reference_source=now + (elapsed if source is None else source),
    )


@pytest.mark.parametrize("side", ["yes", "no"])
def test_confirm_then_commit_despite_probability_recovery(store, market, now, config, side):
    ex = setup(store, market, now, config, side)
    step(ex, market, now, 1, "first")
    step(ex, market, now, 1.1, "same-reference", source=1)
    assert not ex.positions[market.ticker].exit_reason
    step(ex, market, now, 2, "second")
    assert ex.positions[market.ticker].exit_reason == "PROFIT_VALUE"
    snapshot = ex.snapshot()
    ex.restore(snapshot)
    step(ex, market, now, 2.3, "old-book", raw=0.98, received=2.2)
    assert not sells(store)
    step(ex, market, now, 2.4, "fill", raw=0.98)
    assert sells(store)[0]["reason"] == "PROFIT_VALUE"
    result = store.list(kind="trade_result")[0]["body"]
    assert result["net_pnl"] == pytest.approx(0.6336)


@pytest.mark.parametrize(
    "raw_probability,levels", [(0.94, [(".90", 10)]), (0.80, [(".83", 10)]), (0.88, [(".90", 2), (".85", 8)])]
)
def test_requires_raw_value_net_profit_and_depth(store, market, now, config, raw_probability, levels):
    ex = setup(store, market, now, config)
    step(ex, market, now, 1, "first", raw=raw_probability, levels=levels)
    step(ex, market, now, 2, "second", raw=raw_probability, levels=levels)
    assert not ex.positions[market.ticker].exit_reason


def test_reset_and_partial_ioc_floor(store, market, now, config):
    ex = setup(store, market, now, config)
    step(ex, market, now, 1, "first")
    step(ex, market, now, 2, "clears", raw=0.95)
    step(ex, market, now, 3, "new-first")
    assert not ex.positions[market.ticker].exit_reason
    step(ex, market, now, 6, "gap")
    assert not ex.positions[market.ticker].exit_reason
    step(ex, market, now, 7, "second")
    step(ex, market, now, 7.3, "partial", levels=[(".90", 4), (".85", 6)])
    assert [(s["quantity"], s["price"]) for s in sells(store)] == [(4, 0.90)]
    assert ex.positions[market.ticker].quantity == 6
    assert not ex.positions[market.ticker].exit_reason
    step(ex, market, now, 8, "safety", adjusted=0.60, levels=[(".60", 6)])
    assert ex.positions[market.ticker].exit_reason == "HARD_STOP"


def test_no_fill_expires_and_safety_resumes(store, market, now, config):
    ex = setup(store, market, now, config)
    step(ex, market, now, 1, "first")
    step(ex, market, now, 2, "second")
    step(ex, market, now, 2.3, "ioc-empty", adjusted=0.60, levels=[(".68", 10)])
    assert not sells(store)
    assert ex.positions[market.ticker].exit_reason == "INVALIDATION"
    step(ex, market, now, 2.6, "safety-fill", adjusted=0.60, levels=[(".68", 10)])
    assert sells(store)[0]["reason"] == "INVALIDATION"


def test_commit_timeout(store, market, now, config):
    ex = setup(store, market, now, config)
    step(ex, market, now, 1, "first")
    step(ex, market, now, 2, "second")
    step(ex, market, now, 5, "after-gap", raw=0.95)
    assert not sells(store)
    assert not ex.positions[market.ticker].exit_reason
    assert store.list(kind="exit_cancelled")[0]["body"]["reason"] == "IOC_DATA_TIMEOUT"
