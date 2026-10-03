"""Slow maintenance and local backlog must not create a recovery feedback loop."""

import threading
import time
from types import SimpleNamespace

import pytest
from research_helpers import records
from test_collector_recovery import ready_inputs

from btc15.research_log import ResearchLog
from btc15.strategies.settlement_edge.model import Tick


def test_slow_maintenance_still_drains_all_events(research_recorder, tmp_path, monkeypatch):
    clock = [100.0]
    entered, release = threading.Event(), threading.Event()
    calls = []
    fake_time = SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})
    fake_time.monotonic = lambda: clock[0]
    monkeypatch.setattr("btc15.research_log.time", fake_time)

    def slow_retention(self):
        calls.append(clock[0])
        entered.set()
        assert release.wait(5)
        clock[0] += 6  # Each pass takes longer than its five-second interval.
        return True

    monkeypatch.setattr(ResearchLog, "_retention", slow_retention)
    log = research_recorder(tmp_path)
    try:
        assert entered.wait(5)
        for i in range(500):
            assert log.emit("evidence", {"index": i})
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while log.status()["written"] < 500 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1
    log.close()
    assert log.status()["written"] == 500
    assert log.status()["dropped"] == 0
    assert [r["body"]["index"] for r in records(tmp_path)] == list(range(500))


@pytest.mark.parametrize("stream", ["reference", "book"])
def test_backlog_does_not_trigger_stall_reconnect_but_real_stall_still_does(
    store, config, market, now, monkeypatch, stream
):
    engine, recovery = ready_inputs(store, config, market, now)
    if stream == "reference":
        engine.ticks = [Tick(now - 20, now - 20, market.spec.strike)]
    else:
        engine.books[market.ticker].received = now - 20
    clock = [100.0]
    monkeypatch.setattr("btc15.collector_recovery.time", SimpleNamespace(monotonic=lambda: clock[0]))
    # A partial timer before the backlog must not carry through the catch-up.
    recovery.check(engine, now, 0, 0, 100, True)
    clock[0] += 5
    recovery.pressure(2.1, 20, 100, now)
    for _ in range(3):
        clock[0] += 11
        recovery.check(engine, now, 2.1, 20, 100, True)
        assert recovery.paused.is_set()
        assert not recovery.drain.is_set()
    recovery.pressure(0, 0, 100, now)
    recovery.check(engine, now, 0, 0, 100, True)
    assert recovery.paused.is_set() and not recovery.drain.is_set()
    clock[0] += 9
    recovery.check(engine, now, 0, 0, 100, True)
    assert not recovery.drain.is_set()
    clock[0] += 2
    recovery.check(engine, now, 0, 0, 100, True)
    assert recovery.drain.is_set()
    assert recovery.reason.startswith(
        "REFERENCE_STREAM_STALLED" if stream == "reference" else "BOOK_STREAM_STALLED"
    )


def test_backlog_never_masks_integrity_failure(store, config, market, now):
    engine, recovery = ready_inputs(store, config, market, now)
    recovery.pressure(2.1, 20, 100, now)
    engine._collector_integrity_failed = True
    recovery.check(engine, now, 2.1, 20, 100, True)
    assert recovery.drain.is_set()
    assert recovery.reason == "DATA_INTEGRITY_FAILURE"
