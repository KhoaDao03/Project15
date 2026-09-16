import copy

import pytest
from test_execution import decision, ready

from btc15.engine import Engine


def test_inactive_cancel_never_snapshots_or_transacts(store, market, book, now, config, monkeypatch):
    ex = ready(store, market, now, config)
    ex.submit(market, book, decision(), "op", now, True)
    ex.cancel(market.ticker, now + 0.1, "test")
    before = copy.deepcopy(ex.snapshot())

    def forbidden(*args, **kwargs):
        pytest.fail("Inactive cancellation copied state or opened a transaction")

    with monkeypatch.context() as m:
        m.setattr(ex, "snapshot", forbidden)
        m.setattr(store, "transaction", forbidden)
        for _ in range(180):
            ex.cancel(market.ticker, now + 1, "disconnect")
            ex.cancel("missing", now + 1, "disconnect")
    assert ex.snapshot() == before


def test_active_cancel_rolls_back_on_checkpoint_failure(store, market, book, now, config, monkeypatch):
    ex = ready(store, market, now, config)
    ex.submit(market, book, decision(), "op", now, True)
    before = copy.deepcopy(ex.snapshot())
    saved = store.load_checkpoint(ex.run_id)
    records = store.list(limit=None)

    def fail(*args):
        raise OSError("checkpoint failure")

    with monkeypatch.context() as m:
        m.setattr(store, "checkpoint", fail)
        with pytest.raises(OSError):
            ex.cancel(market.ticker, now + 1, "disconnect")
    assert ex.snapshot() == before
    assert store.load_checkpoint(ex.run_id) == saved
    assert store.list(limit=None) == records
    assert store.state(ex.run_id, market.ticker) == "ORDER_PENDING"


def test_recovery_skips_history_but_cancels_old_active_orders(store, market, book, now, config, monkeypatch):
    engine = Engine(store, config, "PAPER", execute=False, record_evaluations=False)
    ex = ready(store, market, now, config)
    order = ex.submit(market, book, decision(), "op", now, True)
    engine.executor = ex
    engine.books[market.ticker] = book
    for n in range(180):
        old = copy.copy(order)
        old.active = False
        ex.orders[f"historical-{n}"] = old
    engine._collector_managed = True
    engine._collector_book_sids = {market.ticker: 7}
    called = []
    original = ex.cancel

    def cancel(*args, **kwargs):
        called.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(ex, "cancel", cancel)
    engine.invalidate(now + 1, "disconnect")
    assert called == [market.ticker]
    assert not ex.orders[market.ticker].active
    assert not book.valid and not engine._collector_book_sids
    assert engine.collector_blocked
    assert market.ticker not in ex.risk.reserved
    assert len(ex.orders) == 181
    engine.invalidate(now + 2, "disconnect")
    assert called == [market.ticker]
