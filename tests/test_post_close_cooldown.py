import copy
from dataclasses import replace

import pytest
from test_entry_fill_experiment import candidate
from test_position_management import process, sells
from test_position_management import scenario as scenario
from test_strategy_reverification import initialize, make_book

from btc15.execution import PaperExecutor


@pytest.mark.parametrize("different_market", [False, True])
def test_full_close_blocks_entries_for_sixty_seconds_and_survives_restart(
    scenario, store, market, now, config, different_market
):
    s = scenario(chosen=replace(config, post_close_cooldown=60))
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50")
        process(s, market, now + elapsed, str(elapsed))
    closed = now + 3
    assert not s.e.executor.positions
    assert s.e.executor.last_position_closed_at == closed
    checkpoint = copy.deepcopy(store.load_checkpoint(s.e.run_id))
    # Old portfolios recover this timestamp from completed orders as well.
    for legacy in (False, True):
        saved = copy.deepcopy(checkpoint)
        if legacy:
            saved.pop("last_position_closed_at")
        restored = PaperExecutor(store, s.e.run_id, "PAPER", s.e.config)
        restored.restore(saved)
        assert not restored.post_close_ready(closed + 59.999)
        assert restored.post_close_ready(closed + 60)
    target = replace(market, ticker=market.ticker + "-OTHER") if different_market else market
    if different_market:
        s.e.markets[target.ticker] = target
        for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
            s.e.state(target.ticker, state, closed)
    old_order = s.e.executor.orders.get(target.ticker)
    risk = copy.deepcopy(s.e.executor.risk.daily)
    for elapsed in (59.999, 60):
        when = closed + elapsed
        s.refresh(when)
        s.e.books[target.ticker] = s.e.books[market.ticker]
        process(s, target, when, str(elapsed))
        if elapsed < 60:
            assert s.e.executor.orders.get(target.ticker) is old_order
            assert "POST_CLOSE_COOLDOWN" in {r["code"] for r in s.e.latest[target.ticker]["reasons"]}
            assert s.e.executor.risk.daily == risk
        else:
            assert s.e.executor.orders[target.ticker] is not old_order
    assert len(store.list(kind="trade_result", run_id=s.e.run_id)) == 1


def test_partial_exit_does_not_start_cooldown(scenario, market, now, config):
    s = scenario(chosen=replace(config, post_close_cooldown=60))
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50", depth=".20")
        process(s, market, now + elapsed, str(elapsed))
    assert s.e.executor.positions[market.ticker].quantity == pytest.approx(0.20)
    assert s.e.executor.last_position_closed_at is None
    s.refresh(now + 4, ".50", depth=".40")
    process(s, market, now + 4, "complete")
    assert not s.e.executor.positions
    assert s.e.executor.last_position_closed_at == now + 4


def test_unfilled_retry_remains_five_seconds(store, market, now, config):
    c = replace(config, post_close_cooldown=60, max_entry_retries=1)
    e = initialize(store, market, now, c, "PAPER")
    first = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.cancel(market.ticker, now + 1, "unfilled")
    assert not e.retry_ready(market.ticker, now + 5.999)
    assert e.retry_ready(market.ticker, now + 6)
    second = e.submit(market, make_book("yes", ".90", ".91", now + 6), candidate(), "retry", now + 6, True)
    assert second and second.id != first.id and second.attempt == 2


def test_cooldown_never_blocks_existing_position_risk_exit(scenario, store, market, now, config):
    s = scenario(chosen=replace(config, post_close_cooldown=60))
    # A different market has just completed while this inventory is still held.
    s.e.executor.last_position_closed_at = now + 1
    for elapsed in (2, 3):
        s.refresh(now + elapsed, ".50")
        process(s, market, now + elapsed, str(elapsed))
    assert not s.e.executor.positions
    assert sells(store)[0]["body"]["reason"] == "HARD_STOP"
    assert not store.list(kind="position_monitoring", run_id=s.e.run_id)


def test_direct_submission_and_pending_entry_cannot_bypass_global_cooldown(store, market, now, config):
    e = initialize(store, market, now, replace(config, post_close_cooldown=60), "PAPER")
    order = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.last_position_closed_at = now + 0.1
    assert (
        e.submit(
            replace(market, ticker=market.ticker + "-OTHER"),
            make_book("yes", ".90", ".91", now + 1),
            candidate(),
            "blocked",
            now + 1,
            True,
        )
        is None
    )
    assert (
        store.list(kind="execution_rejection", newest_first=True)[0]["body"]["reason"]
        == "POST_CLOSE_COOLDOWN"
    )
    assert e.fill(order, 1, order.limit, now + 1, True) is False
    assert not e.positions and not order.active
