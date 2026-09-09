"""Behavior preserved by the measured processing optimizations."""

import random

import pytest

from btc15.domain import Book, D
from btc15.engine import Engine
from btc15.strategies.settlement_edge.model import Tick


def test_incremental_book_matches_full_validation():
    rng = random.Random(15)
    book = Book()
    book.snapshot({"yes_dollars_fp": [], "no_dollars_fp": []}, 0)
    expected = Book(valid=True)
    for i in range(1000):
        side = rng.choice(("yes", "no"))
        price = D(rng.randrange(1, 45)) / 100
        levels = getattr(expected, side)
        quantity = D(rng.randrange(0, 100)) / 10
        delta = quantity - levels.get(price, D(0))
        if quantity:
            levels[price] = quantity
        else:
            levels.pop(price, None)
        expected.validate()
        book.delta(
            dict(side=side, price_dollars=str(price if side == "yes" else 1 - price), delta_fp=str(delta)),
            i,
        )
        assert book.yes == expected.yes and book.no == expected.no
        assert book.valid == expected.valid


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize(
    "price,quantity",
    [("0", "1"), ("1", "1"), ("NaN", "1"), ("0.5", "Infinity"), ("0.5", "NaN"), ("0.5", "-1")],
)
def test_invalid_delta_blocks_book(side, price, quantity):
    book = Book(valid=True)
    with pytest.raises(ValueError):
        book.delta(dict(side=side, price_dollars=price, delta_fp=quantity), 1)
    assert not book.valid


def test_crossed_delta_still_invalidates_book():
    book = Book()
    book.snapshot({"yes_dollars_fp": [["0.4", "2"]], "no_dollars_fp": [["0.7", "2"]]}, 0)
    with pytest.raises(ValueError, match="Crossed"):
        book.delta(dict(side="yes", price_dollars="0.7", delta_fp="1"), 1)
    assert not book.valid


def test_causal_reference_cache_handles_future_ticks_and_clock_reversal(store, config):
    engine = Engine(store, config, execute=False)
    engine.ticks = [Tick(10, 12, 100), Tick(11, 11, 101), Tick(13, 12, 102)]
    for now in (10, 11, 12, 13, 14, 11):
        assert engine.causal_ticks(now) == [t for t in engine.ticks if t.source <= now and t.received <= now]
    engine.ticks = [*engine.ticks, Tick(15, 14, 103)]
    assert engine.causal_ticks(14) == engine.ticks[:-1]
    assert engine.causal_ticks(15) == engine.ticks


def test_state_reads_see_uncommitted_changes_and_rollback(store):
    with pytest.raises(RuntimeError, match="rollback"):
        with store.transaction():
            store.transition("run", "PAPER", "market", "DISCOVER_MARKET", 1)
            assert store.state("run", "market") == "DISCOVER_MARKET"
            raise RuntimeError("rollback")
    assert store.state("run", "market") is None
