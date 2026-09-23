"""Deep-book bursts must not delay actionable signal checks or lose input evidence."""

from decimal import Decimal

import pytest
import test_position_management as management_tests
from research_helpers import records

from btc15 import engine as module
from btc15.domain import dumps
from btc15.strategies.settlement_edge.model import Tick

scenario = management_tests.scenario


def test_deep_updates_preserve_all_inputs_and_check_changed_top(
    scenario, market, now, monkeypatch, research_recorder, tmp_path
):
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.execute, engine.signal_only = False, True
    engine.connection = "test"
    engine.books[market.ticker].yes[Decimal(".88")] = Decimal(engine.config.max_contracts - 1)
    log = engine.research_log = research_recorder(tmp_path)
    checks = []
    original = module.evaluate

    def evaluate(*args, **kwargs):
        checks.append(args[6])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "evaluate", evaluate)

    def update(i, price=".10"):
        at = now + i / 1000
        fixture.clock[0] = at
        payload = dict(
            type="orderbook_delta",
            sid=1,
            seq=i,
            msg=dict(
                market_ticker=market.ticker,
                side="yes",
                price_dollars=price,
                delta_fp="1",
                ts_ms=at * 1000,
            ),
        )
        row = dict(
            id=str(i), received=at, monotonic_ns=int(at * 1e9), connection_id="test", payload=dumps(payload)
        )
        assert log.capture_input(row, payload)
        assert engine.ingest(row)
        log.capture(engine, row, payload)

    for i in range(101):
        update(i)
    assert checks == [now]
    assert engine.books[market.ticker].yes[Decimal(".10")] == 101
    update(101, ".88")  # Best bid quantity changes.
    update(102, ".89")  # Best bid price changes.
    assert checks == [now, now + 0.101, now + 0.102]
    # The normal one-second evaluation continues even with an unchanged quote.
    fixture.clock[0] = now + 1.2
    engine.process(now + 1.2, "heartbeat", "heartbeat", {})
    assert checks[-1] == now + 1.2
    log.close()
    captured = records(tmp_path)
    assert len([r for r in captured if r["kind"] == "input"]) == 103
    assert len([r for r in captured if r["kind"] == "input_processed"]) == 103
    assert len([r for r in captured if r["kind"] == "decision_check"]) == 4


@pytest.mark.parametrize(
    "change",
    [
        "invalid_book",
        "old_source",
        "old_reference",
        "paused",
        "clock",
        "entry_window",
        "entry_cutoff",
        "top_ask_quantity",
        "fees",
    ],
)
def test_unchanged_price_does_not_hide_safety_or_entry_boundary_changes(
    scenario, market, now, monkeypatch, change
):
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.execute, engine.signal_only = False, True
    if change == "entry_window":
        now = market.close_time - engine.config.entry_window_start - 0.1
    elif change == "entry_cutoff":
        now = market.close_time - engine.config.entry_cutoff - 0.1
    fixture.refresh(now)
    engine.process(now, "initial", "orderbook_delta", dict(market_ticker=market.ticker))
    book = engine.books[market.ticker]
    if change == "invalid_book":
        book.valid = False
    elif change == "old_source":
        book.source_time = now - engine.config.book_max_age - 1
    elif change == "old_reference":
        stale = now - engine.config.reference_max_age - 1
        engine.ticks = [Tick(stale, stale, engine.ticks[-1].price)]
    elif change == "paused":
        engine.collector_blocked = True
    elif change == "clock":
        engine.clock_ok = False
    elif change == "top_ask_quantity":
        book.no[max(book.no)] = Decimal(engine.config.max_contracts - 1)
    elif change == "fees":
        engine.series_fees = dict(fee_type="unsupported", fee_multiplier=1)
    checks = []
    original = module.quality

    def quality(*args):
        checks.append(args[3])
        return original(*args)

    monkeypatch.setattr(module, "quality", quality)
    fixture.clock[0] = now + 0.2
    engine.process(now + 0.2, "changed", "orderbook_delta", dict(market_ticker=market.ticker))
    assert checks == [now + 0.2]


def test_excess_top_depth_does_not_repeat_checks_but_lost_liquidity_does(scenario, market, now, monkeypatch):
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.execute, engine.signal_only = False, True
    checks = []
    original = module.evaluate

    def evaluate(*args, **kwargs):
        checks.append(args[6])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "evaluate", evaluate)
    book = engine.books[market.ticker]
    for i, quantity in enumerate([100, 101, 200, 10, 9, 8, 10, 20]):
        at = fixture.clock[0] = now + i / 100
        book.no[max(book.no)] = Decimal(quantity)
        engine.process(at, str(i), "orderbook_delta", dict(market_ticker=market.ticker))
    assert checks == [now, now + 0.04, now + 0.05, now + 0.06]
