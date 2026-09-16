import copy

import pytest
from test_settlement_recovery import opened

from btc15.engine import Engine


@pytest.mark.parametrize("fill", [False, True])
@pytest.mark.parametrize(
    "evidence", ["rules_changed", "invalid", "before_entry", "other_run", "other_market", "diagnostic"]
)
def test_restart_checks_scoped_integrity_without_loading_diagnostics(
    store, config, market, book, now, series, monkeypatch, evidence, fill
):
    engine = opened(store, config, market, book, now, series, fill=fill)
    positions = copy.deepcopy(engine.executor.positions)
    results = store.list(kind="trade_result")
    store.add(
        "invalid_market" if evidence == "invalid" else "health",
        dict(code="INVALID_DATA" if evidence == "diagnostic" else "RULES_CHANGED"),
        "another-run" if evidence == "other_run" else engine.run_id,
        "PAPER",
        now - 0.01 if evidence == "before_entry" else now,
        "another-market" if evidence == "other_market" else market.ticker,
    )
    original = store.list

    def bounded(*args, **kwargs):
        assert kwargs.get("kind") not in ("health", "invalid_market")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "list", bounded)
    restored = Engine(store, config, "PAPER", run_id=engine.run_id, resume=True)
    assert bool(restored.executor.quarantines) == (evidence in ("rules_changed", "invalid"))
    assert restored.executor.positions == positions
    assert store.list(kind="trade_result") == results
    assert restored.executor.risk.realized == engine.executor.risk.realized


def test_restart_without_exposure_skips_integrity_history(
    store, config, market, book, now, series, monkeypatch
):
    engine = opened(store, config, market, book, now, series, fill=False)
    engine.executor.cancel(market.ticker, now + 1, "test")
    original = store.list

    def bounded(*args, **kwargs):
        assert kwargs.get("kind") not in ("health", "invalid_market")
        return original(*args, **kwargs)

    def unexpected(*args):
        pytest.fail("Completed exposure must not query integrity history")

    monkeypatch.setattr(store, "list", bounded)
    monkeypatch.setattr(store, "has_market_integrity_failure", unexpected)
    restored = Engine(store, config, "PAPER", run_id=engine.run_id, resume=True)
    assert restored.executor.snapshot() == engine.executor.snapshot()


def test_integrity_lookup_observes_transaction_and_rollback(store):
    with pytest.raises(RuntimeError, match="interrupted"):
        with store.transaction():
            store.add("health", dict(code="RULES_CHANGED"), "run", "PAPER", 10, "market")
            assert store.has_market_integrity_failure("run", "market", 10)
            raise RuntimeError("interrupted")
    assert not store.has_market_integrity_failure("run", "market", 10)
