import asyncio
import copy
import json
import time

import pytest
from test_collection import fake_client

from btc15 import runner
from btc15.collector_recovery import CollectorRecovery
from btc15.config import Settings
from btc15.domain import Book, D
from btc15.engine import Engine
from btc15.storage import read_events
from btc15.strategies.settlement_edge.model import Tick


def ready_inputs(store, config, market, now):
    e = Engine(store, config, "PAPER", execute=False, record_evaluations=False)
    e.markets[market.ticker] = market
    e.books[market.ticker] = Book(
        yes={D(".80"): D(10)}, no={D(".10"): D(10)}, received=now, source_time=now, valid=True
    )
    e.ticks = [Tick(now, now, market.spec.strike)]
    e.connection = "fresh"
    e.healthy = e.clock_ok = e.exchange_open = True
    c = CollectorRecovery(config, now - 1)
    c.connection = "fresh"
    c.metadata_at = now
    c.reference = True
    c.snapshots.add(market.ticker)
    return e, c


def test_pressure_warns_then_blocks_and_requires_reconnection(store, config, market, now):
    e, c = ready_inputs(store, config, market, now)
    c.check(e, now, 0, 0, 100, True)
    assert not c.paused.is_set()
    c.pressure(0.6, 50, 100, now)
    assert c.warning and not c.drain.is_set()
    c.pressure(0, 80, 100, now)
    assert c.paused.is_set() and c.drain.is_set()
    c.check(e, now, 0, 0, 100, True)
    assert c.status()["state"] == "DRAINING"
    c.reconnect()
    c.check(e, now, 0, 0, 100, True)
    assert c.paused.is_set() and not c.reference and not c.snapshots


@pytest.mark.parametrize(
    "problem",
    [
        "reference",
        "snapshot",
        "metadata",
        "clock",
        "halt",
        "book_source",
        "reference_source",
        "queue",
        "lag",
        "disconnect",
        "quarantine",
        "stopping",
    ],
)
def test_recovery_fails_closed_until_every_check_passes(store, config, market, now, problem):
    e, c = ready_inputs(store, config, market, now)
    lag, depth, connected, stopping = 0, 0, True, False
    if problem == "reference":
        c.reference = False
    if problem == "snapshot":
        c.snapshots.clear()
    if problem == "metadata":
        c.metadata_at = now - 31
    if problem == "clock":
        e.clock_ok = False
    if problem == "halt":
        e.executor.risk.halted = True
    if problem == "book_source":
        e.books[market.ticker].source_time = now - 100
    if problem == "reference_source":
        e.ticks = [Tick(now - 100, now, market.spec.strike)]
    if problem == "queue":
        depth = 21
    if problem == "lag":
        lag = 0.6
    if problem == "disconnect":
        connected = False
    if problem == "quarantine":
        e.executor.quarantines[market.ticker] = {}
    if problem == "stopping":
        stopping = True
    before = copy.deepcopy(e.executor.snapshot())
    c.check(e, now, lag, depth, 100, connected, stopping)
    assert c.paused.is_set() and c.reasons
    assert e.executor.snapshot() == before


def test_recovery_accepts_only_new_connection_evidence(store, config, market, now):
    e, c = ready_inputs(store, config, market, now)
    c.request("OVERLOAD", now - 0.5)
    c.reconnect()
    payload = dict(type="orderbook_snapshot", sid=3, seq=1, msg=dict(market_ticker=market.ticker))
    c.observe(e, dict(connection_id="old", received=now), payload, True)
    assert not c.snapshots
    row = dict(connection_id="fresh", received=now)
    c.observe(e, row, dict(type="connected"), True)
    c.observe(e, row, payload, True)
    c.observe(e, row, dict(type="cfbenchmarks_value"), True)
    c.observe(e, row, dict(type="metadata", msg=dict(request_started_at=now - 1)), True)
    c.check(e, now, 0, 0, 100, True)
    assert c.paused.is_set()
    c.observe(e, row, dict(type="metadata", msg=dict(request_started_at=now)), True)
    c.check(e, now, 0, 0, 100, True)
    assert not c.paused.is_set()


@pytest.mark.parametrize("already_draining", [True, False])
def test_suspended_analysis_preserves_sequence_and_never_evaluates(
    store, config, market, now, monkeypatch, already_draining
):
    e = Engine(store, config, "PAPER", record_evaluations=False)
    e.markets[market.ticker] = market
    e.books[market.ticker] = Book()
    e.connection = "old"
    evaluations = []
    monkeypatch.setattr(e, "process", lambda *a: evaluations.append(a))

    counter = 0

    def row(seq, kind, msg, conn="old"):
        nonlocal counter
        counter += 1
        return dict(
            id=str(seq),
            received=now + counter / 100,
            monotonic_ns=int((now + counter / 100) * 1e9),
            connection_id=conn,
            analysis_suspended=already_draining if counter == 1 else True,
            collector_entries_blocked=True,
            payload=dict(type=kind, sid=3, seq=seq, msg=dict(market_ticker=market.ticker, **msg)),
        )

    assert e.ingest(
        row(1, "orderbook_snapshot", dict(yes_dollars_fp=[[".80", "10"]], no_dollars_fp=[[".90", "10"]]))
    )
    assert len(evaluations) == (0 if already_draining else 1)
    evaluations.clear()
    delta = dict(side="yes", price_dollars=".80", delta_fp="2")
    assert e.ingest(row(2, "orderbook_delta", delta))
    assert e.books[market.ticker].yes[D(".80")] == 12
    assert e.collector_blocked and not e._collector_integrity_failed
    assert not e.ingest(row(4, "orderbook_delta", delta))
    assert not e.books[market.ticker].valid
    assert not e.ingest(row(1, "orderbook_delta", delta, conn="fresh"))
    assert e.ingest(
        row(
            2,
            "orderbook_snapshot",
            dict(yes_dollars_fp=[[".80", "3"]], no_dollars_fp=[[".90", "10"]]),
            conn="fresh",
        )
    )
    assert e.books[market.ticker].yes[D(".80")] == 3
    assert not evaluations


@pytest.mark.parametrize(
    "failure", ["overload", "disconnect", "sequence_gap", "reference_stall", "snapshot_stall"]
)
def test_failure_drains_recovers_and_replays(store, config, tmp_path, raw, series, monkeypatch, failure):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    start = int(time.time() // 60) * 60 - 300
    end = start + 900

    def prose(stamp):
        return datetime.fromtimestamp(stamp, ZoneInfo("America/New_York")).strftime(
            "%I:%M %p %Z on %b %d, %Y"
        )

    raw = {
        **raw,
        "open_time": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "close_time": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "strike_type": "greater_or_equal",
        "rules_primary": f"If the simple average of the sixty seconds of CF Benchmarks' BRTI before {prose(end)} is at least the simple average of the sixty seconds of CF Benchmarks' BRTI before {prose(start)}, then the market resolves to Yes.",
    }
    fake_client(monkeypatch, raw, series)
    monkeypatch.setattr(runner, "QUEUE_CAPACITY", 32)
    stalled = failure in ("reference_stall", "snapshot_stall")
    if stalled:
        monkeypatch.setattr(CollectorRecovery, "STALLED_STREAM_SECONDS", 0.15)
    original = runner.Engine.ingest
    captured = []
    engines = []
    accepted = []
    import threading

    book_ready = threading.Event()

    def slow(e, row):
        if not engines:
            engines.append(e)
        if not row.get("analysis_suspended"):
            time.sleep(0.005)
        captured.append(row)
        valid = original(e, row)
        payload = json.loads(row["payload"])
        if payload.get("type") == "orderbook_snapshot":
            book_ready.set()
        accepted.append((payload.get("type"), valid))
        return valid

    monkeypatch.setattr(runner.Engine, "ingest", slow)
    sent, sockets = [], []

    class Socket:
        async def __aenter__(self):
            sockets.append(self)
            self.index = len(sockets)
            self.count = 0
            self.seq = 1
            return self

        async def __aexit__(self, *a):
            pass

        async def send(self, *a):
            pass

        async def recv(self):
            await asyncio.sleep(0.02 if stalled else 0)
            self.count += 1
            if self.index == 1 and stalled and (self.count > 1 or failure == "snapshot_stall"):
                if self.count % 2 == 0:
                    # The display feed still moves while a required stream stalls.
                    payload = dict(
                        type="cfbenchmarks_value_5hz",
                        msg=dict(index_id="BRTI", value_usd="79200", source_ts_ms=time.time() * 1000),
                    )
                elif failure == "reference_stall":
                    self.seq += 1
                    payload = dict(
                        type="orderbook_delta",
                        sid=3,
                        seq=self.seq,
                        msg=dict(market_ticker=raw["ticker"], side="yes", price_dollars=".80", delta_fp="1"),
                    )
                else:
                    payload = dict(
                        type="cfbenchmarks_value",
                        msg=dict(
                            index_id="BRTI",
                            data=json.dumps(
                                dict(
                                    type="value",
                                    id="BRTI",
                                    time=time.time() * 1000,
                                    value=str(raw["floor_strike"]),
                                )
                            ),
                        ),
                    )
            elif self.index == 1 and failure == "overload" and self.count > 1:
                while not book_ready.is_set():
                    await asyncio.sleep(0.001)
                payload = dict(
                    type="orderbook_delta",
                    sid=3,
                    seq=self.count,
                    msg=dict(market_ticker=raw["ticker"], side="yes", price_dollars=".80", delta_fp="1"),
                )
            elif self.index == 1 and self.count > 1:
                if failure == "disconnect":
                    raise OSError("injected transport loss")
                if self.count > 2:
                    await asyncio.sleep(3600)
                payload = dict(
                    type="orderbook_delta",
                    sid=3,
                    seq=3,
                    msg=dict(market_ticker=raw["ticker"], side="yes", price_dollars=".80", delta_fp="99"),
                )
            elif self.count == 1:
                payload = dict(
                    type="orderbook_snapshot",
                    sid=3,
                    seq=1,
                    msg=dict(
                        market_ticker=raw["ticker"],
                        yes_dollars_fp=[[".80", "3"]],
                        no_dollars_fp=[[".90", "10"]],
                    ),
                )
            elif self.count == 2:
                payload = dict(
                    type="cfbenchmarks_value",
                    msg=dict(
                        index_id="BRTI",
                        data=json.dumps(
                            dict(
                                type="value",
                                id="BRTI",
                                time=time.time() * 1000,
                                value=str(raw["floor_strike"]),
                            )
                        ),
                    ),
                )
            else:
                await asyncio.sleep(3600)
            sent.append(payload)
            return json.dumps(payload)

    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **k: Socket())
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=2.2))
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    assert len(sockets) == 2
    assert [
        r["payload"]
        for r in rows
        if r["payload"]["type"]
        in ("ticker", "orderbook_snapshot", "orderbook_delta", "cfbenchmarks_value", "cfbenchmarks_value_5hz")
    ] == sent
    if failure == "overload":
        assert any(r.get("analysis_suspended") and r["payload"]["type"] == "orderbook_delta" for r in rows)
        assert all(valid for kind, valid in accepted if kind == "orderbook_delta")
        assert not [r for r in store.list(kind="health") if r["body"]["code"] == "INVALID_DATA"]
    assert any(not r.get("collector_entries_blocked") for r in captured)
    assert any(r["body"]["state"] == "DRAINING" for r in store.list(kind="collector_recovery"))
    assert any(r["body"]["state"] == "READY" for r in store.list(kind="collector_recovery"))
    assert not store.list(kind="fill")
    gaps = [r for r in store.list(kind="health") if r["body"]["code"] == "SEQUENCE_GAP"]
    assert bool(gaps) == (failure == "sequence_gap")
    if stalled:
        reason = "REFERENCE_STREAM_STALLED" if failure == "reference_stall" else "BOOK_STREAM_STALLED:"
        assert any(r["body"]["reason"].startswith(reason) for r in store.list(kind="collector_recovery"))
        assert not [r for r in store.list(kind="health") if r["body"]["code"] == "INVALID_DATA"]
    assert engines[0].books[raw["ticker"]].yes[D(".80")] == 3
    assert store.list(kind="status")[-1]["body"]["queue_depth"] == 0

    # Replay the captured recovery controls into an independent ledger.
    from btc15.storage import Store

    replay_store = Store("sqlite:///" + str(tmp_path / "replay.db"))
    clock = [rows[0]["received"]]
    try:
        replay = Engine(
            replay_store, config, "PAPER", run_id=engines[0].run_id, execute=False, clock=lambda: clock[0]
        )
        for row in rows:
            clock[0] = row["received"]
            # Live status reporting materializes the current empty daily budget.
            replay.executor.risk.day(clock[0])
            original(replay, row)
        assert replay.books == engines[0].books
        assert replay.sequences == engines[0].sequences
        assert replay.executor.snapshot() == engines[0].executor.snapshot()
        assert replay_store.list(kind="fill") == store.list(kind="fill") == []
        assert replay_store.list(kind="trade_result") == store.list(kind="trade_result") == []
    finally:
        replay_store.engine.dispose()


def test_duplicate_reference_from_old_connection_cannot_release_gate(store, config, market, now):
    e, c = ready_inputs(store, config, market, now)
    c.request("OVERLOAD", now)
    c.reconnect()
    row = dict(connection_id="fresh", received=now + 1)
    c.observe(e, row, dict(type="connected"), True)
    c.observe(e, row, dict(type="cfbenchmarks_value"), True)
    assert not c.reference


@pytest.mark.parametrize("sid,seq", [(None, 2), (3, None), (True, 2), (4, 1), (3, 1), (3, 3)])
def test_book_integrity_fails_closed(store, config, market, now, sid, seq):
    e, _ = ready_inputs(store, config, market, now)
    e._collector_book_sids[market.ticker] = 3
    e.sequences[3] = 1
    row = dict(
        id="bad",
        connection_id="fresh",
        received=now,
        monotonic_ns=int(now * 1e9),
        collector_entries_blocked=False,
        payload=dict(
            type="orderbook_delta",
            sid=sid,
            seq=seq,
            msg=dict(market_ticker=market.ticker, side="yes", price_dollars=".80", delta_fp="2"),
        ),
    )
    assert not e.ingest(row)
    assert not e.books[market.ticker].valid and e.collector_blocked
    assert e.books[market.ticker].yes[D(".80")] == 10


def test_pause_blocks_submission_and_pending_buy_fills(store, config, market, book, now):
    import threading

    from test_execution import decision, ready

    ex = ready(store, market, now, config)
    order = ex.submit(market, book, decision(), "pending", now, True)
    assert order
    before = copy.deepcopy(ex.snapshot())
    ex.collector_pause = threading.Event()
    ex.collector_pause.set()
    assert ex.fill(order, 1, order.limit, now + 1, True) is False
    assert ex.submit(market, book, decision(), "blocked", now + 1, True) is None
    assert ex.snapshot() == before
    assert not store.list(kind="fill")
    assert store.list(kind="execution_rejection")[-1]["body"]["reason"] == "COLLECTOR_RECOVERING"


def test_drain_preserves_held_portfolio_risk_and_results(store, config, market, now):
    from test_exit_execution_v2 import held

    e = Engine(store, config, "PAPER", run_id="run", record_evaluations=False)
    e.executor = held(store, market, now, config)
    e.markets[market.ticker] = market
    e.books[market.ticker] = Book()
    e.executor.risk.realized = -3.5
    e.executor.risk.day(now)["trades"] = 2
    before = copy.deepcopy(e.executor.snapshot())
    records = store.list(kind="trade_result")
    assert e.ingest(
        dict(
            id="drain",
            connection_id="old",
            received=now,
            monotonic_ns=int(now * 1e9),
            analysis_suspended=True,
            collector_entries_blocked=True,
            payload=dict(type="ticker", msg={}),
        )
    )
    assert e.executor.snapshot() == before
    assert store.list(kind="trade_result") == records
    assert not store.list(kind="fill")


def test_repeated_recovery_preserves_held_exposure(store, config, market, now):
    from test_exit_execution_v2 import held

    e, c = ready_inputs(store, config, market, now)
    e.executor = held(store, market, now, config)
    e.executor.risk.realized = -3.5
    e.executor.risk.day(now)["trades"] = 2
    before = copy.deepcopy(e.executor.snapshot())
    for generation in range(3):
        stamp = now + generation + 1
        c.request("PROCESSING_OVERLOAD", stamp)
        c.check(e, stamp, 2, 80, 100, True)
        assert c.paused.is_set() and c.phase == "DRAINING"
        c.reconnect()

        def ingest(payload):
            row = dict(
                id=f"{generation}-{payload['type']}",
                connection_id=f"recovery-{generation}",
                received=stamp,
                monotonic_ns=int(stamp * 1e9),
                analysis_suspended=True,
                collector_entries_blocked=True,
                payload=payload,
            )
            valid = e.ingest(row)
            assert valid
            c.observe(e, row, payload, valid)

        ingest(dict(type="connected"))
        ingest(
            dict(
                type="orderbook_snapshot",
                sid=3,
                seq=1,
                msg=dict(
                    market_ticker=market.ticker, yes_dollars_fp=[[".80", "10"]], no_dollars_fp=[[".90", "10"]]
                ),
            )
        )
        ingest(
            dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(
                        dict(
                            type="value",
                            id="BRTI",
                            time=stamp * 1000,
                            value=str(market.spec.strike),
                        )
                    ),
                ),
            )
        )
        c.check(e, stamp, 0, 0, 100, True)
        assert c.paused.is_set() and "WAITING_FOR_FRESH_METADATA" in c.reasons
        # Metadata validation itself is exercised by the runner integration test.
        c.observe(
            e,
            dict(connection_id=e.connection, received=stamp),
            dict(type="metadata", msg=dict(request_started_at=stamp)),
            True,
        )
        c.check(e, stamp, 0, 0, 100, True)
        assert c.phase == "READY" and not c.paused.is_set()
        assert e.executor.snapshot() == before
        assert not store.list(kind="fill") and not store.list(kind="trade_result")


def test_health_reports_recovery_even_when_transport_is_fresh(store):
    from btc15.operation import health

    store.add(
        "status",
        dict(
            connected=True,
            clock_ok=True,
            paper_execution=True,
            exchange_open=True,
            reference_age=0,
            processing_lag=0,
            recovery=dict(entries_blocked=True, warning=True),
        ),
        "run",
        "PAPER",
        100,
    )
    assert health(store, "run", 100)["reasons"] == ["COLLECTOR_RECOVERING", "BACKLOG_WARNING"]


def test_internal_clock_invalidation_requests_recovery(store, config, market, now):
    e, c = ready_inputs(store, config, market, now)
    e._collector_integrity_failed = True
    c.check(e, now, 0, 0, 100, True)
    assert c.drain.is_set() and c.paused.is_set()


def test_recovery_flags_survive_all_recording_formats(tmp_path):
    from btc15.storage import CompactRecorder, RawRecorder

    row = dict(
        id="drain",
        received=100,
        monotonic_ns=100000000000,
        connection_id="old",
        analysis_suspended=True,
        collector_entries_blocked=True,
        payload=json.dumps(dict(type="heartbeat", msg={})),
    )
    full = RawRecorder(tmp_path / "full", chunk_size=1)
    compact = CompactRecorder(tmp_path / "compact")
    for recorder in (full, compact):
        recorder.append_rows([row])
        recorder.close()
    paths = [
        next((tmp_path / "full").glob("*.jsonl")),
        next((tmp_path / "full").glob("*.parquet")),
        compact.path,
    ]
    for path in paths:
        recorded = list(read_events(path))
        assert len(recorded) == 1
        assert recorded[0]["analysis_suspended"] is True
        assert recorded[0]["collector_entries_blocked"] is True


def test_gap_blocks_later_events_in_same_recorded_batch(store, config, market, now, monkeypatch):
    e, _ = ready_inputs(store, config, market, now)
    e.sequences[3] = 1
    e._collector_book_sids[market.ticker] = 3
    gates = []
    monkeypatch.setattr(e, "process", lambda *args: gates.append(e.collector_blocked))

    def frame(payload, offset):
        return dict(
            id=str(offset),
            received=now + offset,
            monotonic_ns=int((now + offset) * 1e9),
            connection_id="fresh",
            collector_entries_blocked=False,
            payload=payload,
        )

    assert not e.ingest(
        frame(
            dict(
                type="orderbook_delta",
                sid=3,
                seq=3,
                msg=dict(market_ticker=market.ticker, side="yes", price_dollars=".80", delta_fp="1"),
            ),
            0.1,
        )
    )
    assert e.ingest(
        frame(
            dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(
                        dict(type="value", id="BRTI", time=(now + 0.2) * 1000, value=str(market.spec.strike))
                    ),
                ),
            ),
            0.2,
        )
    )
    assert e.ingest(
        frame(
            dict(
                type="orderbook_snapshot",
                sid=3,
                seq=4,
                msg=dict(
                    market_ticker=market.ticker, yes_dollars_fp=[[".80", "10"]], no_dollars_fp=[[".90", "10"]]
                ),
            ),
            0.3,
        )
    )
    assert gates == [True, True]
    assert e.books[market.ticker].valid and e.healthy
    assert e._collector_integrity_failed


def test_new_overload_during_readiness_check_cannot_reopen_entries(store, config, market, now, monkeypatch):
    import btc15.collector_recovery as module

    e, c = ready_inputs(store, config, market, now)
    original = module.freshness_rechecks

    def interrupted(*args):
        c.request("NEW_OVERLOAD", now)
        return original(*args)

    monkeypatch.setattr(module, "freshness_rechecks", interrupted)
    c.check(e, now, 0, 0, 100, True)
    assert c.paused.is_set() and c.drain.is_set()
    assert c.status()["state"] == "DRAINING"
