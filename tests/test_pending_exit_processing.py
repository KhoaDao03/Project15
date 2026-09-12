import copy

import pytest
from test_execution import decision, ready

from btc15.domain import D


@pytest.fixture
def pending(store, market, book, now, config):
    ex = ready(store, market, now, config)
    order = ex.submit(market, book, decision(), "op", now, True)
    ex.fill(order, order.quantity, order.limit, now + 0.5, True)
    book.yes = {D(".50"): D("1.25")}
    book.no = {D(".48"): D("100")}
    book.received = now + 1
    ex.monitor(market, book, {"conservative_yes": 0.8}, now + 1, "trigger")
    return ex


@pytest.mark.parametrize("reason", ["HARD_STOP", "PROFIT_VALUE"])
@pytest.mark.parametrize("waiting", ["clock", "book", "duplicate"])
def test_pending_wait_does_not_copy_or_transact(pending, market, book, now, monkeypatch, waiting, reason):
    ex = pending
    ex.positions[market.ticker].exit_reason = reason
    ex.positions[market.ticker].value_limit = 0.50
    eligible = ex._exit_eligible[market.ticker]
    checked = now + 1.1 if waiting == "clock" else eligible
    book.received = checked if waiting != "book" else eligible - 0.001
    event = "trigger" if waiting == "duplicate" else "waiting"
    before = copy.deepcopy(ex.snapshot())

    def forbidden(*args, **kwargs):
        pytest.fail("Unchanged pending exit copied state or opened a transaction")

    with monkeypatch.context() as m:
        m.setattr(ex, "snapshot", forbidden)
        m.setattr(ex.store, "transaction", forbidden)
        ex.monitor(market, book, {"conservative_yes": 0.8}, checked, event)
    assert ex.snapshot() == before


def test_exit_executes_at_exact_eligibility_and_preserves_depth(pending, market, book):
    ex = pending
    eligible = ex._exit_eligible[market.ticker]
    quantity = ex.positions[market.ticker].quantity
    book.received = eligible
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "eligible")
    assert ex.positions[market.ticker].quantity == quantity - 1.25
    before = copy.deepcopy(ex.snapshot())
    ex.monitor(market, book, {"conservative_yes": 0.8}, eligible, "eligible")
    assert ex.snapshot() == before


@pytest.mark.parametrize("change", ["extremum", "clear", "replace", "fill"])
def test_pending_mutations_remain_atomic(pending, market, book, now, monkeypatch, change):
    ex = pending
    before = copy.deepcopy(ex.snapshot())
    ex.store.checkpoint(ex.run_id, before)
    saved = ex.store.load_checkpoint(ex.run_id)
    records = ex.store.list(limit=None)
    checked = now + 1.1
    probability = {"conservative_yes": 0.8}
    if change == "extremum":
        book.yes = {D(".49"): D("1.25")}
    elif change in ("clear", "replace"):
        book.yes = {D(".80"): D("1.25")}
        probability = {"conservative_yes": 0.1 if change == "replace" else 0.99}
    else:
        checked = ex._exit_eligible[market.ticker]
    book.received = checked
    checkpoint = ex.store.checkpoint

    def fail(*args, **kwargs):
        checkpoint(*args, **kwargs)
        raise RuntimeError("checkpoint failed")

    with monkeypatch.context() as m:
        m.setattr(ex.store, "checkpoint", fail)
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            ex.monitor(market, book, probability, checked, "changed")
    assert ex.snapshot() == before
    assert ex.store.load_checkpoint(ex.run_id) == saved
    assert ex.store.list(limit=None) == records
