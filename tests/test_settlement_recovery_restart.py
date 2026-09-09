import copy

import pytest
from test_settlement_recovery import metadata, opened, proof

from btc15.engine import Engine


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_restart_does_not_reclassify_unrelated_halt(store, config, market, book, now, series, mode):
    engine = opened(store, config, market, book, now, series, mode)
    engine.executor.cancel(market.ticker, now + 1, "unrelated_operator_review")
    engine.state(market.ticker, "HALTED", now + 1)
    metadata(engine, {**market.raw, "floor_strike": None}, series, now + 2)
    reserved = copy.deepcopy(engine.executor.risk.reserved)
    for _ in range(2):
        engine = Engine(store, config, mode, run_id=engine.run_id, resume=True)
        assert market.ticker not in engine.executor.quarantines
        assert engine.executor.risk.reserved == reserved
        assert (
            engine.settle(
                market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series)
            )
            == "BLOCKED"
        )
        assert store.state(engine.run_id, market.ticker) == "HALTED"
    assert not store.list(kind="trade_result")
    assert not store.list(kind="settlement_recovery")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_restart_reconciles_observation_before_interrupted_quarantine(
    store, config, market, book, now, series, mode
):
    engine = opened(store, config, market, book, now, series, mode)
    # Metadata observation is durable before the atomic quarantine update.
    store.add(
        "invalid_market",
        {**market.raw, "floor_strike": None},
        engine.run_id,
        mode,
        now + 1,
        market.ticker,
    )
    engine = Engine(store, config, mode, run_id=engine.run_id, resume=True)
    assert engine.executor.quarantines[market.ticker]["reason"] == "METADATA_HISTORY_RECONCILIATION"
    assert store.state(engine.run_id, market.ticker) == "HALTED"
    assert (
        engine.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series))
        == "SETTLED"
    )
    assert not engine.executor.positions
    assert len(store.list(kind="trade_result")) == 1
