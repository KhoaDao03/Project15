import copy
from dataclasses import replace

import pytest
import test_position_management as management
from test_entry_fill_experiment import candidate
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.execution import PaperExecutor

scenario = management.scenario


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("next_side", ["yes", "no"])
def test_closed_market_cannot_reopen_on_either_side_after_restart(
    scenario, store, market, now, config, mode, next_side
):
    s = scenario(mode=mode, chosen=replace(config, one_trade_per_market=True, post_close_cooldown=60))
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50")
        management.process(s, market, now + elapsed, str(elapsed))
    assert not s.e.executor.positions
    assert store.state(s.e.run_id, market.ticker) == "CLOSED"
    assert len(store.list(kind="trade_result", run_id=s.e.run_id)) == 1
    risk = copy.deepcopy(s.e.executor.risk.daily)
    restored = PaperExecutor(store, s.e.run_id, mode, s.e.config)
    restored.restore(store.load_checkpoint(s.e.run_id))
    assert not restored.reentry_ready(market.ticker, now + 70)
    assert not restored.retry_ready(market.ticker, now + 70)
    result = restored.submit(
        market,
        make_book(next_side, ".90", ".91", now + 70),
        {**candidate(), "side": next_side},
        "blocked",
        now + 70,
        True,
    )
    assert result is None
    assert (
        store.list(kind="execution_rejection", newest_first=True)[0]["body"]["reason"] == "MARKET_TRADE_LIMIT"
    )
    s.e.executor = restored
    s.refresh(now + 70)
    management.process(s, market, now + 70, "after-close")
    assert store.state(s.e.run_id, market.ticker) == "CLOSED"
    assert restored.risk.daily == risk
    # The next distinct market can trade after the existing global cooldown.
    other = replace(market, ticker=market.ticker + "-OTHER")
    for state in (
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ):
        restored.state(other.ticker, state, now + 70)
    assert restored.submit(
        other, make_book("yes", ".90", ".91", now + 70), candidate(), "new-market", now + 70, True
    )


def test_partial_fills_share_one_trade_and_exits_keep_working(scenario, store, market, now, config):
    s = scenario(chosen=replace(config, one_trade_per_market=True))
    assert s.e.executor.market_trade_limit_reached(market.ticker)
    # Continue filling the already-submitted first order.
    s.e.executor.fill(s.order, 0.60, s.order.limit, now + 1, True)
    assert s.e.executor.positions[market.ticker].quantity == pytest.approx(1)
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50", depth=".20")
        management.process(s, market, now + elapsed, str(elapsed))
    assert s.e.executor.positions[market.ticker].quantity == pytest.approx(0.8)
    assert s.e.executor.market_trade_limit_reached(market.ticker)
    s.refresh(now + 4, ".50", depth="1")
    management.process(s, market, now + 4, "close-rest")
    assert not s.e.executor.positions
    assert len(store.list(kind="trade_result", run_id=s.e.run_id)) == 1
    assert not store.list(kind="position_monitoring", run_id=s.e.run_id)


def test_unfilled_attempt_can_retry_after_restart(store, market, now, config):
    c = replace(config, one_trade_per_market=True, max_entry_retries=2)
    e = initialize(store, market, now, c, "PAPER")
    first = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.cancel(market.ticker, now + 1, "unfilled")
    restored = PaperExecutor(store, e.run_id, "PAPER", c)
    restored.restore(store.load_checkpoint(e.run_id))
    assert not restored.retry_ready(market.ticker, now + 5.999)
    assert restored.retry_ready(market.ticker, now + 6)
    second = restored.submit(
        market, make_book("yes", ".90", ".91", now + 6), candidate(), "retry", now + 6, True
    )
    assert second and second.id != first.id and second.cycle == 1 and second.attempt == 2


@pytest.mark.parametrize("active", [False, True])
def test_enabling_limit_blocks_preexisting_later_unfilled_cycle(scenario, store, market, now, config, active):
    s = scenario(chosen=replace(config, one_trade_per_market=False))
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50")
        management.process(s, market, now + elapsed, str(elapsed))
    s.refresh(now + 9)
    management.process(s, market, now + 9, "second-cycle")
    second = s.e.executor.orders[market.ticker]
    assert second.cycle == 2 and second.remaining == second.quantity
    if not active:
        s.e.executor.cancel(market.ticker, now + 10, "unfilled")
    new_config = replace(s.e.config, one_trade_per_market=True)
    checkpoint = s.e.executor.snapshot()
    checkpoint["config_version"] = new_config.version
    restored = PaperExecutor(store, s.e.run_id, "PAPER", new_config)
    restored.restore(checkpoint)
    assert restored.market_trade_limit_reached(market.ticker)
    assert not restored.retry_ready(market.ticker, now + 20)
    if active:
        assert restored.fill(restored.orders[market.ticker], 1, second.limit, now + 20, True) is False
        assert not restored.orders[market.ticker].active
        assert not restored.positions


def test_limit_config_is_opt_in_and_boolean():
    old = Strategy()
    assert old.version == "1766c001ffaa6835"
    assert replace(old, one_trade_per_market=True).version != old.version
    for value in ("true", 1, None):
        with pytest.raises(ValueError, match="one_trade_per_market requires a boolean"):
            replace(old, one_trade_per_market=value)
