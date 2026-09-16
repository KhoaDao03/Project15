import copy

import pytest
from test_settlement_recovery import metadata, opened

from btc15.domain import parse_market


@pytest.fixture
def accepted(raw, series):
    return parse_market({**raw, "updated_time": "2026-09-08T17:50:02Z"}, series)


def test_older_missing_strike_does_not_invalidate(store, config, accepted, book, now, series):
    e = opened(store, config, accepted, book, now, series)
    before = copy.deepcopy(e.executor.snapshot())
    metadata(
        e, {**accepted.raw, "floor_strike": None, "updated_time": "2026-09-08T17:50:01Z"}, series, now + 1
    )
    assert not e.executor.quarantines
    assert e.books[accepted.ticker].valid
    assert e.executor.snapshot()["positions"] == before["positions"]
    assert e.executor.snapshot()["contracts"] == before["contracts"]
    assert not store.list(kind="invalid_market")


@pytest.mark.parametrize("stamp", [None, "bad", "2026-09-08T17:50:02Z", "2026-09-08T17:50:03Z"])
def test_unproven_regression_still_quarantines(store, config, accepted, book, now, series, stamp):
    e = opened(store, config, accepted, book, now, series)
    metadata(e, {**accepted.raw, "floor_strike": None, "updated_time": stamp}, series, now + 1)
    assert e.executor.quarantines


@pytest.mark.parametrize("held", [True, False])
def test_known_stale_quarantine_recovers_atomically(
    store, config, accepted, book, now, series, held, monkeypatch
):
    e = opened(store, config, accepted, book, now, series, fill=held)
    ex = e.executor
    bad = {
        **accepted.raw,
        "floor_strike": None,
        "strike_type": None,
        "custom_strike": None,
        "updated_time": "2026-09-08T17:50:01Z",
    }
    ex.quarantine(accepted, now + 1, "METADATA_INVALID", bad)
    before = copy.deepcopy(ex.snapshot())
    checkpoint = store.load_checkpoint(ex.run_id)
    records = store.list(limit=None)
    save = store.checkpoint

    def fail(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("checkpoint failed")

    with monkeypatch.context() as m:
        m.setattr(store, "checkpoint", fail)
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            ex.recover_stale_metadata(
                accepted, series, now + 3, request_started_at=now + 2, source="kalshi_rest", clock_ok=True
            )
    assert ex.snapshot() == before
    assert store.load_checkpoint(ex.run_id) == checkpoint
    assert store.list(limit=None) == records
    assert store.state(ex.run_id, accepted.ticker) == "HALTED"
    assert ex.recover_stale_metadata(
        accepted, series, now + 3, request_started_at=now + 2, source="kalshi_rest", clock_ok=True
    )
    assert not ex.quarantines
    assert store.state(ex.run_id, accepted.ticker) == ("POSITION_OPEN" if held else "EVALUATING")
    assert ex.snapshot()["positions"] == before["positions"]


@pytest.mark.parametrize("failure", ["changed", "newer", "source", "request", "clock", "halt", "pause"])
def test_recovery_does_not_clear_other_failures(store, config, accepted, book, now, series, failure):
    e = opened(store, config, accepted, book, now, series)
    ex = e.executor
    bad = {
        **accepted.raw,
        "floor_strike": None,
        "strike_type": None,
        "custom_strike": None,
        "updated_time": "2026-09-08T17:50:01Z",
    }
    if failure == "newer":
        bad["updated_time"] = "2026-09-08T17:50:03Z"
    ex.quarantine(accepted, now + 1, "METADATA_INVALID", bad)
    if failure == "halt":
        ex.risk.halted = True
    if failure == "pause":
        ex.venue_pauses[accepted.ticker] = {"event": "inactive"}
    market = (
        parse_market({**accepted.raw, "floor_strike": accepted.spec.strike + 1}, series)
        if failure == "changed"
        else accepted
    )
    assert not ex.recover_stale_metadata(
        market,
        series,
        now + 3,
        request_started_at=now if failure == "request" else now + 2,
        source="websocket" if failure == "source" else "kalshi_rest",
        clock_ok=failure != "clock",
    )
    assert ex.quarantines


def test_halted_cannot_be_reopened_without_proof(store, now):
    store.transition("run", "PAPER", "market", "DISCOVER_MARKET", now)
    store.transition("run", "PAPER", "market", "HALTED", now)
    with pytest.raises(ValueError):
        store.transition("run", "PAPER", "market", "EVALUATING", now)
    with pytest.raises(ValueError):
        store.transition("run", "PAPER", "market", "EVALUATING", now, metadata_recovery_id="missing")


@pytest.mark.parametrize("fresh", [False, True])
def test_engine_requires_fresh_inputs_to_recover(store, config, accepted, book, now, series, fresh):
    from btc15.strategies.settlement_edge.model import Tick

    e = opened(store, config, accepted, book, now, series)
    bad = {**accepted.raw, "floor_strike": None, "updated_time": "2026-09-08T17:50:01Z"}
    e.executor.quarantine(accepted, now + 1, "METADATA_INVALID", bad)
    e.healthy = True
    e.books[accepted.ticker].received = now + 3
    e.books[accepted.ticker].source_time = now + 3
    e.books[accepted.ticker].valid = fresh
    e.ticks = [Tick(now + 3, now + 3, accepted.spec.strike)]
    assert e.ingest(
        dict(
            id="fresh-confirmation",
            received=now + 3,
            monotonic_ns=int((now + 3) * 1e9),
            connection_id="test",
            payload=dict(
                type="metadata",
                msg=dict(
                    series=series,
                    markets=[accepted.raw],
                    fee_changes={},
                    series_fee_changes=[],
                    exchange_status={"trading_active": True},
                    clock_skew=0,
                    source="kalshi_rest",
                    request_started_at=now + 2,
                ),
            ),
        )
    )
    assert (accepted.ticker not in e.executor.quarantines) == fresh


def test_recovered_flat_market_can_expire_without_reconnecting(store, config, accepted, book, now, series):
    from test_settlement_recovery import proof

    e = opened(store, config, accepted, book, now, series, fill=False)
    ex = e.executor
    bad = {**accepted.raw, "floor_strike": None, "updated_time": "2026-09-08T17:50:01Z"}
    ex.quarantine(accepted, now + 1, "METADATA_INVALID", bad)
    assert ex.recover_stale_metadata(
        accepted, series, now + 3, request_started_at=now + 2, source="kalshi_rest", clock_ok=True
    )
    assert store.state(e.run_id, accepted.ticker) == "EVALUATING"
    risk = copy.deepcopy(ex.snapshot()["risk"])
    result = ex.settle(accepted, "yes", accepted.close_time + 1, evidence=proof(accepted.raw, series))
    assert result != "BLOCKED"
    assert store.state(e.run_id, accepted.ticker) == "CLOSED"
    assert not ex.positions
    assert ex.snapshot()["risk"] == risk
    assert not store.list(kind="trade_result")
    assert (
        ex.settle(accepted, "yes", accepted.close_time + 2, evidence=proof(accepted.raw, series))
        == "ALREADY_SETTLED"
    )
    assert len(store.list(kind="settlement")) == 1
