"""Behavior preserved by the measured processing optimizations."""

import random

import pytest

from btc15.domain import Book, D


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
        for outcome in ("yes", "no"):
            asks = expected.asks(outcome)
            assert book.ask_level(outcome) == (asks[0] if asks else (None, 0))


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


def test_state_reads_see_uncommitted_changes_and_rollback(store):
    with pytest.raises(RuntimeError, match="rollback"):
        with store.transaction():
            store.transition("run", "PAPER", "market", "DISCOVER_MARKET", 1)
            assert store.state("run", "market") == "DISCOVER_MARKET"
            raise RuntimeError("rollback")
    assert store.state("run", "market") is None
