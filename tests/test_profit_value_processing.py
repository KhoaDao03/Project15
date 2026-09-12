import copy

import pytest
from test_profit_value_exit import setup, step

from btc15.stop_shadow import ConfirmationExecutor


@pytest.fixture(params=[False, True], ids=["primary", "shadow"])
def executor(request, store, market, now, config):
    ex = setup(store, market, now, config)
    if request.param:
        shadow = ConfirmationExecutor(store, ex.run_id, ex.mode, ex.config)
        shadow.restore(ex.snapshot())
        ex = shadow
    return ex


@pytest.mark.parametrize("qualifies", [False, True])
def test_unchanged_confirmation_does_not_snapshot_or_transact(executor, market, now, monkeypatch, qualifies):
    raw = 0.88 if qualifies else 0.95
    step(executor, market, now, 1, "initial", raw=raw)
    before = copy.deepcopy(executor.snapshot())

    def forbidden(*args, **kwargs):
        pytest.fail("Unchanged quote opened a transaction or copied the portfolio")

    with monkeypatch.context() as m:
        m.setattr(executor, "snapshot", forbidden)
        m.setattr(executor.store, "transaction", forbidden)
        for i in range(10):
            step(executor, market, now, 1.1 + i / 100, str(i), raw=raw, source=1)
    assert executor.snapshot() == before


@pytest.mark.parametrize("transition", ["first", "second", "reset", "gap", "same-second"])
def test_confirmation_checkpoint_failure_rolls_back(executor, market, now, monkeypatch, transition):
    if transition != "first":
        step(executor, market, now, 1, "initial")
    before = copy.deepcopy(executor.snapshot())
    executor.store.checkpoint(executor.run_id, before)
    records = executor.store.list(limit=None)
    checkpoint = executor.store.checkpoint

    def fail_after_write(*args, **kwargs):
        checkpoint(*args, **kwargs)
        raise RuntimeError("checkpoint failed")

    elapsed = {"first": 1, "second": 2, "reset": 2, "gap": 4, "same-second": 1.2}[transition]
    with monkeypatch.context() as m:
        m.setattr(executor.store, "checkpoint", fail_after_write)
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            step(executor, market, now, elapsed, "failure", raw=0.95 if transition == "reset" else 0.88)
    assert executor.snapshot() == before
    assert executor.store.load_checkpoint(executor.run_id) == before
    assert executor.store.list(limit=None) == records


def test_changed_depth_and_probability_recheck_same_reference(executor, market, now):
    step(executor, market, now, 1, "first")
    step(executor, market, now, 1.1, "depth-gone", levels=[(".90", 2), (".85", 8)], source=1)
    assert executor.positions[market.ticker].value_count == 0
    step(executor, market, now, 1.2, "depth-back", source=1)
    assert executor.positions[market.ticker].value_count == 1
    step(executor, market, now, 1.3, "probability-changed", raw=0.95, source=1)
    assert executor.positions[market.ticker].value_count == 0
    step(executor, market, now, 1.4, "probability-back", source=1)
    step(executor, market, now, 2, "second")
    assert executor.positions[market.ticker].exit_reason == "PROFIT_VALUE"
