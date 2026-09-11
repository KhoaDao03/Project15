"""Synthetic checks for resting-price validation and durable bounded retries."""

import hashlib
import json
from dataclasses import asdict, replace

import pytest
import test_position_management as management_tests
from test_strategy_reverification import initialize, make_book

from btc15.execution import PaperExecutor
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate

scenario = management_tests.scenario


def candidate():
    return dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.99, reasons=[])


def test_original_hash_and_opt_in(config):
    values = asdict(config)
    for key in (
        "enabled",
        "daily_entry_limits_enabled",
        "resting_limit_recheck",
        "revalidate_entry_signal",
        "max_entry_retries",
        "entry_retry_cooldown",
        "hold_value_exit_enabled",
        "entry_probability_deductions",
        "sustained_lead_enabled",
        "late_entry_enabled",
        "late_no_new_entry",
        "lead_confirmation_samples",
        "late_min_probability",
        "late_lead_confirmation_samples",
        "min_lead_sigma",
        "late_min_lead_sigma",
    ):
        del values[key]
    assert config.version == hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
    assert replace(config, resting_limit_recheck=True).version != config.version
    assert replace(config, max_entry_retries=1).version != config.version
    for kwargs in ({"max_entry_retries": 3}, {"entry_retry_cooldown": 0}):
        with pytest.raises(ValueError):
            replace(config, **kwargs)


def test_resting_limit_keeps_edge_but_not_bad_health(store, market, now, config):
    c = replace(config, resting_limit_recheck=True)
    book = make_book("yes", ".90", ".91", now)
    e = initialize(store, market, now, c, "PAPER")
    order = e.submit(market, book, candidate(), "first", now, True)
    moved = make_book("yes", ".96", ".97", now + 1)
    args = (
        market,
        moved,
        Tick(now + 1, now + 1, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=0.96),
        dict(score=100, reasons=[]),
        now + 1,
        c,
    )
    assert evaluate(*args)["decision"] == "NO_TRADE"
    resting = evaluate(*args, entry_price=order.limit)
    assert resting["decision"] == "TRADE_CANDIDATE"
    e.revalidate(market, resting, now + 1, True)
    assert order.active
    e.revalidate(market, resting, now + 2, False, [dict(code="REFERENCE_RECEIVE_AGE", actual=4)])
    cancelled = store.list(kind="order", newest_first=True)[0]["body"]
    assert not order.active
    assert cancelled["details"]["reasons"][0]["code"] == "REFERENCE_RECEIVE_AGE"
    assert cancelled["elapsed_seconds"] == 2
    assert cancelled["queue_remaining"] == order.queue


def test_retry_once_survives_restart_and_preserves_budgets(store, market, now, config):
    c = replace(config, max_entry_retries=1)
    e = initialize(store, market, now, c, "PAPER")
    first = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.cancel(market.ticker, now + 1, "signal_invalid_or_timeout")
    assert not e.retry_ready(market.ticker, now + 5.99)
    restored = PaperExecutor(store, e.run_id, "PAPER", c)
    restored.restore(store.load_checkpoint(e.run_id))
    assert restored.retry_ready(market.ticker, now + 6)
    retry = restored.submit(
        market, make_book("yes", ".90", ".91", now + 6), candidate(), "retry", now + 6, True
    )
    assert retry and retry.attempt == 2 and retry.id != first.id
    assert restored.risk.day(now)["trades"] == 2
    assert restored.risk.day(now)["exposure"] > restored.risk.reserved[market.ticker]
    assert (
        restored.submit(
            market, make_book("yes", ".90", ".91", now + 6), candidate(), "duplicate", now + 6, True
        )
        is None
    )
    restored.cancel(market.ticker, now + 7, "signal_invalid_or_timeout")
    restored.restore(store.load_checkpoint(e.run_id))
    assert not restored.retry_ready(market.ticker, now + 20)
    assert (
        restored.submit(
            market, make_book("yes", ".90", ".91", now + 20), candidate(), "third", now + 20, True
        )
        is None
    )


@pytest.mark.parametrize("blocked", ["partial", "budget", "stale", "halt", "quarantine", "pause", "window"])
def test_retry_cannot_bypass_checks(store, market, now, config, blocked):
    c = replace(config, max_entry_retries=1, max_daily_trades=1 if blocked == "budget" else 20)
    e = initialize(store, market, now, c, "PAPER")
    order = e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    if blocked == "partial":
        e.fill(order, 1, order.limit, now + 0.5, True)
    e.cancel(market.ticker, now + 1, "signal_invalid_or_timeout")
    if blocked == "halt":
        e.risk.halted = True
    if blocked == "quarantine":
        e.quarantines[market.ticker] = {"reason": "test"}
    if blocked == "pause":
        e.venue_pauses[market.ticker] = {"event": "paused"}
    when = market.close_time - 120 if blocked == "window" else now + 6
    book = make_book("yes", ".90", ".91", now if blocked == "stale" else when)
    assert e.submit(market, book, candidate(), "retry", when, True) is None
    assert e.risk.day(now)["trades"] == 1


def test_retry_rollback_does_not_consume_attempt(store, market, now, config, monkeypatch):
    c = replace(config, max_entry_retries=1)
    e = initialize(store, market, now, c, "PAPER")
    e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.cancel(market.ticker, now + 1, "test")
    original = store.checkpoint

    def fail(*args):
        raise OSError("synthetic checkpoint failure")

    monkeypatch.setattr(store, "checkpoint", fail)
    with pytest.raises(OSError):
        e.submit(market, make_book("yes", ".90", ".91", now + 6), candidate(), "retry", now + 6, True)
    assert e.orders[market.ticker].attempt == 1
    assert e.risk.day(now)["trades"] == 1
    monkeypatch.setattr(store, "checkpoint", original)
    assert e.submit(market, make_book("yes", ".90", ".91", now + 6), candidate(), "retry", now + 6, True)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_engine_resting_recheck_and_retry(scenario, market, now, config, mode, side):
    c = replace(config, resting_limit_recheck=True, max_entry_retries=1)
    s = scenario(mode=mode, side=side, buy=False, chosen=c)

    def process(when, bid):
        s.refresh(when, bid)
        s.e.process(when, str(when), "orderbook_snapshot", dict(market_ticker=market.ticker))

    process(now, ".88")
    first = s.e.executor.orders[market.ticker]
    process(now + 1, ".95")  # ask .97 has too little edge, resting .89 still qualifies
    assert first.active
    s.p["conservative_" + side] = 0.89
    process(now + 2, ".88")
    assert not first.active
    s.p["conservative_" + side] = 0.99
    process(now + 6, ".88")
    assert s.e.executor.orders[market.ticker].id == first.id
    process(now + 7, ".88")
    second = s.e.executor.orders[market.ticker]
    assert second.active and second.attempt == 2 and second.id != first.id
    s.e.executor.cancel(market.ticker, now + 8, "test")
    process(now + 14, ".88")
    assert s.e.executor.orders[market.ticker].id == second.id


@pytest.mark.parametrize("side", ["yes", "no"])
def test_ioc_preset_fills_and_accounts_for_fees(scenario, market, now, side):
    from btc15.config import Strategy

    c = Strategy.load("config/settlement-edge-fill-taker-paper.json")
    s = scenario(side=side, buy=False, chosen=c)
    s.e.process(now, "submit", "orderbook_snapshot", dict(market_ticker=market.ticker))
    s.refresh(now + 0.5)
    s.e.process(now + 0.5, "fill", "orderbook_snapshot", dict(market_ticker=market.ticker))
    position = s.e.executor.positions[market.ticker]
    assert position.quantity > 0 and position.fees > 0
    order = s.e.executor.orders[market.ticker]
    assert position.cost / position.bought <= order.limit


def test_legacy_cancelled_order_stays_blocked(store, market, now, config):
    e = initialize(store, market, now, config, "PAPER")
    e.submit(market, make_book("yes", ".90", ".91", now), candidate(), "first", now, True)
    e.cancel(market.ticker, now + 1, "test")
    snapshot = e.snapshot()
    for order in snapshot["orders"].values():
        del order["attempt"]
        del order["cancelled_at"]
    e.restore(snapshot)
    assert not e.retry_ready(market.ticker, now + 10)
    assert e.orders[market.ticker].attempt == 1


def test_three_attempts_preserve_cap_and_stop_at_limit(store, market, now, config):
    c = replace(config, passive=False, revalidate_entry_signal=False, max_entry_retries=2, fixed_contracts=10)
    e = initialize(store, market, now, c, "PAPER")
    for attempt in range(1, 4):
        t = now + (attempt - 1) * 6
        order = e.submit(market, make_book("yes", ".90", ".91", t), candidate(), str(attempt), t, True)
        assert order and order.attempt == attempt and order.quantity == 10
        cap = order.limit
        e.aggressive(market, make_book("yes", ".95", ".96", t + 0.3), t + 0.3)
        assert not order.active and order.limit == cap and not e.positions
        assert not e.retry_ready(market.ticker, t + 5)
    assert not e.retry_ready(market.ticker, now + 30)
    assert e.risk.day(now)["trades"] == 3
