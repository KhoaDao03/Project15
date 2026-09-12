"""Queue arithmetic must not reject valid fractional source volume or invent fills."""

import copy
import json
from dataclasses import replace

import pytest

from btc15.domain import Book, D, dumps
from btc15.execution import PaperExecutor


def pending(store, market, now, config, *, side="yes", depth="1.00"):
    book = Book(received=now, source_time=now, valid=True)
    setattr(book, side, {D(".909"): D(depth)})
    setattr(book, "no" if side == "yes" else "yes", {D(".09"): D(20)})
    book.validate()
    executor = PaperExecutor(store, "fractional", "PAPER", config)
    for state in (
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ):
        store.transition(executor.run_id, "PAPER", market.ticker, state, now)
    decision = dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.97)
    evidence = {**decision, "timestamp": now}
    order = executor.submit(market, book, decision, "fractional-op", now, True, entry_evidence=evidence)
    assert order is not None
    return executor, order


def trade(order, volume, when, trade_id="fractional-trade"):
    return dict(
        trade_id=trade_id,
        ts_ms=when * 1000,
        taker_outcome_side="no" if order.side == "yes" else "yes",
        yes_price_dollars=str(D(order.limit) if order.side == "yes" else 1 - D(order.limit)),
        count_fp=volume,
    )


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("moderate", [False, True])
@pytest.mark.parametrize(
    "depth,volume,queue,filled",
    [
        ("1.00", "1.60", "1.50", "0.10"),
        ("2.10", "3.16", "3.15", "0.01"),
        ("1.01", "1.52", "1.52", "0"),
        ("1.01", "1.53", "1.52", "0.01"),
    ],
)
def test_fractional_queue_depletion(
    store, market, now, config, side, moderate, depth, volume, queue, filled
):
    if moderate:
        config = replace(config, entry_window_start=600, min_entry_price=0.80, min_edge=0.02, min_ev=0.02)
    executor, order = pending(store, market, now, config, side=side, depth=depth)
    assert D(order.queue) == D(queue)
    assert D(order.original_queue) == D(queue)
    event = trade(order, volume, now + 1)
    executor.trade(market, event, now + 1)
    assert order.queue == 0
    assert order.active
    fills = store.list(kind="fill")
    if D(filled):
        assert len(fills) == 1
        assert D(fills[0]["body"]["quantity"]) == D(filled)
        assert D(executor.positions[market.ticker].quantity) == D(filled)
        assert len(store.list(kind="opportunity")) == 1
        assert D(filled) <= D(volume) - D(depth) * D(config.queue_multiplier)
    else:
        assert not fills and not executor.positions and not store.list(kind="opportunity")
    before = copy.deepcopy(executor.snapshot())
    executor.trade(market, event, now + 1.1)
    assert executor.snapshot() == before
    assert not store.list(kind="health")


@pytest.mark.parametrize("side", ["yes", "no"])
def test_repeated_depletion_checkpoint_and_settlement(store, market, now, config, side):
    executor, order = pending(store, market, now, config, side=side, depth=".20")
    assert order.queue == 0.30
    for i, expected in enumerate((".20", ".10", "0"), 1):
        executor.trade(market, trade(order, ".10", now + i, str(i)), now + i)
        assert D(order.queue) == D(expected)
        assert not executor.positions
    executor.trade(market, trade(order, ".01", now + 4, "first-fill"), now + 4)
    assert executor.positions[market.ticker].quantity == 0.01
    checkpoint = store.load_checkpoint(executor.run_id)
    # Numeric queue fields and the existing checkpoint shape remain compatible.
    assert isinstance(checkpoint["orders"][market.ticker]["queue"], (int, float))
    restored = PaperExecutor(store, executor.run_id, "PAPER", config)
    restored.restore(json.loads(dumps(checkpoint)))
    assert restored.snapshot() == executor.snapshot()
    restored.trade(market, trade(order, ".01", now + 4, "first-fill"), now + 4)
    restored.trade(market, trade(order, ".02", now + 5, "second-fill"), now + 5)
    assert restored.positions[market.ticker].quantity == 0.03
    assert len(store.list(kind="fill")) == 2
    assert len(store.list(kind="opportunity")) == 1
    restored.settle(market, side, market.close_time + 1)
    result = store.list(kind="trade_result")[0]["body"]
    fees = sum(row["body"]["fee"] for row in store.list(kind="fill"))
    assert result["net_pnl"] == pytest.approx(0.03 * (1 - order.limit) - fees)
    assert not restored.positions and not restored.risk.reserved
    restored.settle(market, side, market.close_time + 2)
    assert len(store.list(kind="trade_result")) == 1


@pytest.mark.parametrize("volume,filled", [("1.52", "0"), ("1.53", ".01")])
def test_legacy_subcent_queue_rounds_residual_down(store, market, now, config, volume, filled):
    executor, order = pending(store, market, now, config)
    legacy = copy.deepcopy(executor.snapshot())
    legacy["orders"][market.ticker].update(queue=1.515, original_queue=1.515)
    store.checkpoint(executor.run_id, legacy)
    executor.restore(store.load_checkpoint(executor.run_id))
    order = executor.orders[market.ticker]
    executor.trade(market, trade(order, volume, now + 1), now + 1)
    assert order.active and order.queue == 0
    quantity = executor.positions[market.ticker].quantity if executor.positions else 0
    assert D(quantity) == D(filled)
    assert D(quantity) <= D(volume) - D("1.515")
    executor.trade(market, trade(order, ".01", now + 2, "later"), now + 2)
    assert D(executor.positions[market.ticker].quantity) == D(filled) + D(".01")


def test_fractional_fill_failure_rolls_back_queue_evidence_and_duplicate_claim(
    store, market, now, config, monkeypatch
):
    executor, order = pending(store, market, now, config)
    before = copy.deepcopy(executor.snapshot())
    checkpoint = store.load_checkpoint(executor.run_id)
    original = store.add

    def fail(kind, *args, **kwargs):
        if kind == "fill":
            raise OSError("simulated write failure")
        return original(kind, *args, **kwargs)

    event = trade(order, "1.60", now + 1)
    with monkeypatch.context() as patch:
        patch.setattr(store, "add", fail)
        with pytest.raises(OSError, match="simulated write failure"):
            executor.trade(market, event, now + 1)
    assert executor.snapshot() == before
    assert store.load_checkpoint(executor.run_id) == checkpoint
    assert not store.list(kind="opportunity") and not store.list(kind="fill")
    assert store.state(executor.run_id, market.ticker) == "ORDER_PENDING"
    executor.trade(market, event, now + 1)
    assert executor.positions[market.ticker].quantity == 0.10
    assert len(store.list(kind="opportunity")) == len(store.list(kind="fill")) == 1


@pytest.mark.parametrize("volume", ["NaN", "Infinity", "-Infinity", "bad", "0", "-1", ".005", "1.515"])
def test_invalid_source_volume_does_not_mutate_queue(store, market, now, config, volume):
    executor, order = pending(store, market, now, config)
    before = copy.deepcopy(executor.snapshot())
    with pytest.raises(ValueError, match="Invalid trade quantity"):
        executor.trade(market, trade(order, volume, now + 1), now + 1)
    assert executor.snapshot() == before
    assert not store.list(kind="fill") and not store.list(kind="opportunity")


@pytest.mark.parametrize("case", ["before_latency", "stale", "wrong_side", "wrong_price"])
def test_fractional_matching_keeps_eligibility_guards(store, market, now, config, case):
    executor, order = pending(store, market, now, config)
    event = trade(order, "1.60", now + 1)
    received = now + 1
    if case == "before_latency":
        event["ts_ms"] = (now + config.latency_seconds / 2) * 1000
    elif case == "stale":
        received = now + config.book_max_age + 2
    elif case == "wrong_side":
        event["taker_outcome_side"] = order.side
    else:
        event["yes_price_dollars"] = str(D(order.limit) + D(".01"))
    executor.trade(market, event, received)
    assert order.queue == 1.50 and order.active
    assert not executor.positions and not store.list(kind="fill")


def test_direct_fill_still_rejects_subcent_quantity(store, market, now, config):
    executor, order = pending(store, market, now, config)
    with pytest.raises(ValueError, match="Invalid fill quantity"):
        executor.fill(order, D(".005"), order.limit, now + 1, True)
    assert not store.list(kind="fill")


@pytest.mark.parametrize("side", ["yes", "no"])
def test_fractional_matching_caps_fill_to_order_remaining(store, market, now, config, side):
    executor, order = pending(store, market, now, config, side=side)
    executor.trade(market, trade(order, "100.01", now + 1), now + 1)
    assert not order.active and order.remaining == 0
    assert executor.positions[market.ticker].quantity == order.quantity
    assert store.state(executor.run_id, market.ticker) == "POSITION_OPEN"
    assert len(store.list(kind="opportunity")) == len(store.list(kind="fill")) == 1
