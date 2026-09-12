import copy
import time

import pytest
from test_collector_recovery import ready_inputs
from test_exit_execution_v2 import held

from btc15.strategies.settlement_edge.model import Tick


@pytest.mark.parametrize(
    "missing",
    ["reference", "reference_received", "reference_source", "snapshot", "book_received", "book_source"],
)
def test_connected_stalled_stream_requests_ordered_recovery(store, config, market, now, monkeypatch, missing):
    engine, recovery = ready_inputs(store, config, market, now)
    engine.executor = held(store, market, now, config)
    before = copy.deepcopy(engine.executor.snapshot())
    monotonic = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    recovery.check(engine, now, 0, 0, 100, True)
    assert not recovery.paused.is_set()
    for elapsed in (0, 9.9, 10.1):
        monotonic[0] = 100 + elapsed
        stamp = now + elapsed
        recovery.metadata_at = stamp
        engine.ticks = [Tick(stamp, stamp, market.spec.strike)]
        engine.books[market.ticker].received = stamp
        engine.books[market.ticker].source_time = stamp
        if missing == "reference":
            recovery.reference = False
        elif missing == "reference_received":
            engine.ticks = [Tick(stamp, stamp - config.reference_max_age - 1, market.spec.strike)]
        elif missing == "reference_source":
            engine.ticks = [Tick(stamp - config.reference_max_age - 1, stamp, market.spec.strike)]
        elif missing == "snapshot":
            recovery.snapshots.clear()
        elif missing == "book_received":
            engine.books[market.ticker].received = stamp - config.book_max_age - 1
        elif missing == "book_source":
            engine.books[market.ticker].source_time = stamp - config.book_max_age - 1
        recovery.check(engine, stamp, 0, 0, 100, True)
        assert recovery.paused.is_set()
        assert recovery.drain.is_set() == (elapsed >= 10)
        assert engine.executor.snapshot() == before
    expected = "REFERENCE_STREAM_STALLED" if missing.startswith("reference") else "BOOK_STREAM_STALLED:"
    assert recovery.reason.startswith(expected)
    assert recovery.status()["state"] == "DRAINING"


def test_brief_stalls_and_new_connections_reset_retry_timer(store, config, market, now, monkeypatch):
    engine, recovery = ready_inputs(store, config, market, now)
    monotonic = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    recovery.reference = False
    recovery.check(engine, now, 0, 0, 100, True)
    monotonic[0] += 9
    recovery.reference = True
    recovery.check(engine, now, 0, 0, 100, True)
    assert not recovery.paused.is_set()
    monotonic[0] += 5
    recovery.reference = False
    recovery.check(engine, now, 0, 0, 100, True)
    assert recovery.paused.is_set() and not recovery.drain.is_set()
    monotonic[0] += 9
    recovery.check(engine, now, 0, 0, 100, True)
    assert not recovery.drain.is_set()
    recovery.request("CONNECTION_LOST", now)
    recovery.reconnect()
    monotonic[0] += 5
    recovery.observe(engine, dict(connection_id="fresh", received=now), dict(type="connected"), True)
    recovery.check(engine, now, 0, 0, 100, True)
    assert recovery.paused.is_set() and not recovery.drain.is_set()
    monotonic[0] += 10
    recovery.check(engine, now, 0, 0, 100, True)
    assert recovery.reason == "REFERENCE_STREAM_STALLED" and recovery.drain.is_set()


@pytest.mark.parametrize(
    "blocker",
    ["metadata", "quarantine", "venue_pause", "closed", "clock", "stopping", "disconnected", "inactive"],
)
def test_non_stream_blocks_do_not_cause_reconnects(store, config, market, now, monkeypatch, blocker):
    engine, recovery = ready_inputs(store, config, market, now)
    monotonic = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic[0])
    if blocker == "metadata":
        recovery.metadata_at = None
    elif blocker == "quarantine":
        engine.executor.quarantines[market.ticker] = {}
        recovery.snapshots.clear()
    elif blocker == "venue_pause":
        engine.executor.venue_pauses[market.ticker] = {}
        recovery.snapshots.clear()
    elif blocker == "closed":
        engine.exchange_open = False
        recovery.reference = False
    elif blocker == "clock":
        engine.clock_ok = False
        recovery.reference = False
    elif blocker in ("stopping", "disconnected"):
        recovery.reference = False
    elif blocker == "inactive":
        engine.markets.clear()
    for elapsed in (0, 20):
        monotonic[0] = 100 + elapsed
        recovery.check(engine, now, 0, 0, 100, blocker != "disconnected", blocker == "stopping")
        assert recovery.paused.is_set() and not recovery.drain.is_set()
