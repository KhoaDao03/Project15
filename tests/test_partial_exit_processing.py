import copy

import pytest
from test_pending_exit_processing import pending as pending

from btc15.domain import D


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("reason", ["HARD_STOP", "TAKE_PROFIT", "INVALIDATION"])
def test_exhausted_partial_exit_avoids_snapshot_and_resumes(
    pending, market, book, now, monkeypatch, side, reason
):
    ex = pending
    eligible = ex._exit_eligible[market.ticker]
    book.received = eligible
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "partial")
    pos = ex.positions[market.ticker]
    pos.side = side
    pos.exit_reason = reason
    if side == "no":
        book.yes, book.no = book.no, book.yes
    mark = book.bid(side) - pos.cost / pos.bought
    before = copy.deepcopy(ex.snapshot())

    def forbidden(*args, **kwargs):
        pytest.fail("Empty partial exit copied state or opened transaction")

    with monkeypatch.context() as m:
        m.setattr(ex, "snapshot", forbidden)
        m.setattr(ex.store, "transaction", forbidden)
        for i in range(20):
            ex._apply_monitor(market, book, mark, reason, 0.5, False, eligible + 0.01, "empty-" + str(i))
    assert ex.snapshot() == before
    levels = book.yes if side == "yes" else book.no
    levels[D(".50")] += D(".25")
    book.received = eligible + 0.1
    ex._apply_monitor(market, book, mark, reason, 0.5, False, eligible + 0.1, "replenished")
    assert pos.quantity == pytest.approx(before["positions"][market.ticker]["quantity"] - 0.25)
    after = copy.deepcopy(ex.snapshot())
    ex._apply_monitor(market, book, mark, reason, 0.5, False, eligible + 0.1, "replenished")
    assert ex.snapshot() == after


def test_partial_exit_new_extremum_still_commits(pending, market, book):
    ex = pending
    eligible = ex._exit_eligible[market.ticker]
    book.received = eligible
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "partial")
    ex._apply_monitor(market, book, -0.8, "HARD_STOP", None, False, eligible + 0.1, "extremum")
    assert ex.positions[market.ticker].max_adverse == -0.8
    assert ex.store.load_checkpoint(ex.run_id)["positions"][market.ticker]["max_adverse"] == -0.8


@pytest.mark.parametrize("extra", [".009", ".01"])
def test_replenishment_respects_minimum_quantity(pending, market, book, extra):
    ex = pending
    eligible = ex._exit_eligible[market.ticker]
    book.received = eligible
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "partial")
    quantity = ex.positions[market.ticker].quantity
    book.yes[D(".50")] += D(extra)
    book.received = eligible + 0.1
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible + 0.1, "replenished")
    assert ex.positions[market.ticker].quantity == pytest.approx(quantity - (0 if extra == ".009" else 0.01))


def test_partial_replenishment_checkpoint_failure_rolls_back(pending, market, book, monkeypatch):
    ex = pending
    eligible = ex._exit_eligible[market.ticker]
    book.received = eligible
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "partial")
    before = copy.deepcopy(ex.snapshot())
    saved = ex.store.load_checkpoint(ex.run_id)
    records = ex.store.list(limit=None)
    checkpoint = ex.store.checkpoint

    def fail(*args, **kwargs):
        checkpoint(*args, **kwargs)
        raise RuntimeError("checkpoint failed")

    book.yes[D(".50")] += D(".25")
    book.received = eligible + 0.1
    with monkeypatch.context() as m:
        m.setattr(ex.store, "checkpoint", fail)
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            ex.monitor(market, book, {"conservative_yes": 0.8}, eligible + 0.1, "replenished")
    assert ex.snapshot() == before
    assert ex.store.load_checkpoint(ex.run_id) == saved
    assert ex.store.list(limit=None) == records
