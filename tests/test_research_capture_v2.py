"""Replay capture preserves causal inputs/checks and makes losses explicit."""

import gzip
import json
import threading
from dataclasses import replace
from decimal import Decimal

import pytest
import test_position_management as management_tests
from research_helpers import records

from btc15.domain import Book, dumps
from btc15.research_coverage import Coverage
from btc15.research_log import ResearchLog

scenario = management_tests.scenario


def test_compact_coverage_preserves_raw_frames_and_continuity(research_recorder, tmp_path):
    log = research_recorder(tmp_path / "capture")
    ticker = "KXBTC15M-TEST"
    frames = [
        dict(
            type="orderbook_snapshot",
            sid=7,
            seq=1,
            msg=dict(market_ticker=ticker, yes_dollars_fp=[["0.30", "123.45"]] * 100),
        ),
        dict(
            type="orderbook_delta",
            sid=7,
            seq=2,
            msg=dict(market_ticker=ticker, side="yes", price_dollars="0.30", delta_fp="-1.25"),
        ),
        dict(type="orderbook_delta", sid=7, seq=4, msg=dict(market_ticker=ticker)),
        dict(type="trade", sid=8, seq=1, msg=dict(market_ticker=ticker, trade_id="fill", count_fp="2.5")),
        dict(type="disconnect", msg={}),
        dict(type="orderbook_delta", sid=7, seq=1, msg=dict(market_ticker=ticker)),
        dict(type="orderbook_snapshot", sid=7, seq=2, msg=dict(market_ticker=ticker)),
    ]
    originals = []
    for i, payload in enumerate(frames):
        raw = dumps(payload)
        originals.append(raw)
        row = dict(id=str(i), received=100 + i, connection_id="before" if i < 5 else "after", payload=raw)
        assert log.capture_input(row, payload)
        # The background coverage worker must not retain mutable caller data.
        payload["msg"]["market_ticker"] = "KXBTC15M-MUTATED"
        payload["seq"] = -1
    log.close()
    captured = records(tmp_path / "capture")
    assert [r["body"]["payload"] for r in captured] == originals
    assert [r["capture_seq"] for r in captured] == list(range(1, len(frames) + 1))
    actual = json.loads((log.directory / "coverage.json").read_text())
    reference_root = tmp_path / "reference"
    reference_root.mkdir()
    reference = Coverage(reference_root, "BTC", log.session, log.metadata["started_at"])
    try:
        for record in captured:
            reference.observe(record)
        expected = reference.summary(log.status(), actual["observed_at"])
        assert actual == expected
        market = actual["markets"][0]
        assert market["sequence_gaps"] == 1
        assert market["book_continuity_breaks"] == 2
        assert market["book_snapshots"] == 2
    finally:
        reference.close()


def test_full_depth_inputs_can_rebuild_book_between_summary_samples(research_recorder, tmp_path):
    log = research_recorder(tmp_path)
    frames = [
        dict(type="connected", msg={}),
        dict(type="subscribed", msg=dict(channel="orderbook_delta", sid=7)),
        dict(
            type="orderbook_snapshot",
            sid=7,
            seq=1,
            msg=dict(
                market_ticker="M",
                ts_ms=1000,
                yes_dollars_fp=[[str(Decimal(".80") - i * Decimal(".05")), "10"] for i in range(8)],
                no_dollars_fp=[[".90", "20"]],
            ),
        ),
        dict(
            type="orderbook_delta",
            sid=7,
            seq=2,
            msg=dict(
                market_ticker="M",
                ts_ms=1100,
                side="yes",
                price_dollars=".45",
                delta_fp="17",
            ),
        ),
        dict(type="trade", sid=8, seq=1, msg=dict(market_ticker="M", ts_ms=1150, trade_id="T")),
        dict(type="heartbeat", msg={}),
    ]
    for i, payload in enumerate(frames):
        row = dict(
            id=str(i), received=1 + i / 100, monotonic_ns=100 + i, connection_id="c", payload=dumps(payload)
        )
        assert log.capture_input(row, payload)
    log.close()
    captured = records(tmp_path)
    assert [json.loads(r["body"]["payload"]) for r in captured] == frames
    book = Book()
    for event in captured:
        frame = json.loads(event["body"]["payload"])
        if frame["type"] == "orderbook_snapshot":
            book.snapshot(frame["msg"], event["received_at"])
        elif frame["type"] == "orderbook_delta":
            book.delta(frame["msg"], event["received_at"])
    assert len(book.yes) == 8
    assert book.yes[Decimal(".45")] == 27
    assert captured[3]["source_time"] == 1.1
    assert captured[3]["received_monotonic_ns"] == 103
    assert captured[-1]["source_time"] is None


def test_clocks_and_historical_journal_times_are_not_conflated(research_recorder, tmp_path):
    log = research_recorder(tmp_path)
    assert log.emit("input", {"id": "zero"}, 0, source_time=0)
    assert log.emit("input", {"id": "later"}, 10)
    assert log.emit("input", {"id": "wall_clock_reversal"}, 1)
    assert log.emit("settlement_journal", {"journal_timestamp": -100})
    log.close()
    rr = records(tmp_path)
    assert [r["capture_seq"] for r in rr] == [1, 2, 3, 4]
    assert [r["received_at"] for r in rr] == [0, 10, 1, None]
    assert rr[0]["source_time"] == 0
    assert all(r["recorded_at"] >= r["captured_at"] for r in rr)
    assert all(r["schema_version"] == 2 and "timestamp" not in r for r in rr)


def test_gaps_follow_queued_predecessors_and_include_terminal_loss(research_recorder, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = ResearchLog._retention

    def blocked(log):
        entered.set()
        assert release.wait(2)
        return original(log)

    monkeypatch.setattr(ResearchLog, "_retention", blocked)
    log = research_recorder(tmp_path, max_queue_bytes=2048)
    assert entered.wait(2)
    try:
        assert log.emit("input", {"id": "first"}, 10)
        assert not log.emit("input", {"big": "x" * 3000}, 11)
        assert log.emit("input", {"id": "third"}, 12)
        assert not log.emit("input", {"big": "x" * 3000}, 13)
    finally:
        release.set()
        log.close()
    rr = records(tmp_path)
    assert [(r["kind"], r["capture_seq"]) for r in rr] == [
        ("input", 1),
        ("recording_gap", 2),
        ("input", 3),
        ("recording_gap", 4),
    ]
    assert rr[1]["body"]["first_missing_seq"] == rr[1]["body"]["last_missing_seq"] == 2
    assert rr[-1]["body"]["next_capture_at"] is None
    status = json.loads(next(tmp_path.rglob("status.json")).read_text())
    assert status["clean_shutdown"] and not status["capture_complete"]
    assert status["dropped"] == 2


def test_failed_write_keeps_part_and_counts_inflight_loss(research_recorder, tmp_path, monkeypatch):
    original = gzip.open

    class BrokenStream:
        def __init__(self, path, *args, **kwargs):
            self.stream = original(path, *args, **kwargs)

        def write(self, data):
            raise OSError("test disk failure")

        def close(self):
            self.stream.close()

    def open_stream(path, *args, **kwargs):
        if str(path).endswith(".part"):
            return BrokenStream(path, *args, **kwargs)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(gzip, "open", open_stream)
    log = research_recorder(tmp_path)
    log.emit("input", {"id": "lost"})
    log.close()
    status = json.loads(next(tmp_path.rglob("status.json")).read_text())
    assert status["dropped"] == 1
    assert not status["clean_shutdown"] and not status["capture_complete"]
    assert not list(tmp_path.rglob("events-*.jsonl.gz"))
    assert list(tmp_path.rglob("events-*.part"))


def test_every_recheck_links_cached_model_even_when_latest_is_unchanged(
    research_recorder, scenario, config, market, now, tmp_path
):
    fixture = scenario(buy=False, chosen=replace(config, evaluation_interval=1))
    engine = fixture.e
    engine.execute = False
    engine.signal_only = True
    log = engine.research_log = research_recorder(tmp_path)
    for event_id, offset, quantity in [("first", 0, 9), ("second", 0.01, 10), ("same_time", 0.01, 9)]:
        # A changed executable quantity requires a check even when the resulting
        # decision and the cached probability remain unchanged.
        engine.books[market.ticker].yes[Decimal(".88")] = Decimal(quantity)
        fixture.clock[0] = now + offset
        engine.process(now + offset, event_id, "orderbook_delta", {"market_ticker": market.ticker})
    assert engine.latest[market.ticker]["snapshot_id"] == "first"
    log.close()
    rr = records(tmp_path)
    models = [r["body"] for r in rr if r["kind"] == "model_calculation"]
    checks = [r["body"] for r in rr if r["kind"] == "decision_check"]
    assert len(models) == 1 and len(checks) == 3
    assert len({c["decision_id"] for c in checks}) == 3
    assert {c["model_id"] for c in checks} == {models[0]["model_id"]}
    assert [c["model_recomputed"] for c in checks] == [True, False, False]
    assert len({dumps(c["decision"]) for c in checks}) <= 2  # Time remaining may change.
    assert not engine.executor.orders


def test_reconnect_requires_snapshot_and_records_rejected_delta(
    research_recorder, scenario, market, now, tmp_path
):
    engine = scenario(buy=False).e
    engine.execute = False
    engine.signal_only = True
    log = engine.research_log = research_recorder(tmp_path)
    payloads = [
        ("a", dict(type="connected", msg={})),
        (
            "a",
            dict(
                type="orderbook_snapshot",
                sid=1,
                seq=1,
                msg=dict(
                    market_ticker=market.ticker,
                    yes_dollars_fp=[[".88", "10"]],
                    no_dollars_fp=[[".90", "10"]],
                ),
            ),
        ),
        ("b", dict(type="connected", msg={})),
        (
            "b",
            dict(
                type="orderbook_delta",
                sid=2,
                seq=1,
                msg=dict(
                    market_ticker=market.ticker,
                    side="yes",
                    price_dollars=".88",
                    delta_fp="5",
                ),
            ),
        ),
        (
            "b",
            dict(
                type="orderbook_snapshot",
                sid=2,
                seq=2,
                msg=dict(
                    market_ticker=market.ticker,
                    yes_dollars_fp=[[".88", "30"]],
                    no_dollars_fp=[[".90", "10"]],
                ),
            ),
        ),
    ]
    outcomes = []
    for i, (connection, payload) in enumerate(payloads):
        row = dict(
            id=str(i),
            received=now + i / 100,
            monotonic_ns=int((now + i / 100) * 1e9),
            connection_id=connection,
            payload=dumps(payload),
            collector_entries_blocked=False,
        )
        log.capture_input(row, payload)
        outcomes.append(engine.ingest(row))
    log.close()
    assert outcomes[3] is False
    assert engine.books[market.ticker].yes[Decimal(".88")] == 30
    assert engine._research_books[market.ticker] == dict(
        snapshot_id="4", input_id="4", connection_id="b", sid=2, seq=2
    )
    rr = records(tmp_path)
    processed = [r["body"] for r in rr if r["kind"] == "input_processed"]
    assert [r["valid"] for r in processed] == outcomes
    assert len([r for r in rr if r["kind"] == "input"]) == 5


def test_model_failure_is_recorded_as_an_actual_calculation(
    research_recorder, scenario, market, now, tmp_path
):
    fixture = scenario(buy=False)
    fixture.failure[0] = True
    engine = fixture.e
    engine.signal_only = True
    engine.execute = False
    log = engine.research_log = research_recorder(tmp_path)
    engine.process(now, "bad-model", "heartbeat", {})
    log.close()
    rr = records(tmp_path)
    model = next(r["body"] for r in rr if r["kind"] == "model_calculation")
    assert "Insufficient reference history" in model["error"]
    check = next(r["body"] for r in rr if r["kind"] == "decision_check")
    assert check["decision"]["reasons"][0]["code"] == "MODEL_UNAVAILABLE"
    assert check["model_id"] == model["model_id"]


def test_collector_capture_precedes_processing_and_retains_heartbeat(
    store, config, tmp_path, raw, series, monkeypatch
):
    import asyncio

    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings

    fake_client(monkeypatch, raw, series)
    payloads = [dict(type="trade", sid=8, seq=i + 1, msg={"trade_id": str(i)}) for i in range(4)]
    fake_socket(monkeypatch, payloads)
    root = tmp_path / "research"
    monkeypatch.setenv("BTC15_RESEARCH_LOG_ENABLED", "1")
    monkeypatch.setenv("BTC15_RESEARCH_LOG_DIR", str(root))
    monkeypatch.setattr(ResearchLog, "_retention", lambda self: True)
    original = runner.Engine.ingest

    def fail_on_trade(engine, row):
        if json.loads(row["payload"]).get("type") == "trade":
            raise RuntimeError("test processing failure after receipt")
        return original(engine, row)

    monkeypatch.setattr(runner.Engine, "ingest", fail_on_trade)
    with pytest.raises(ExceptionGroup):
        asyncio.run(
            runner.collect(
                Settings(data_dir=str(tmp_path / "collector")),
                replace(config, bleep_exchange_seed_enabled=False),
                store,
                live_signals=True,
                managed_run="capture-test",
                duration=0.1,
            )
        )
    inputs = [json.loads(r["body"]["payload"]) for r in records(root) if r["kind"] == "input"]
    assert any(p["type"] == "reference_history" for p in inputs)
    assert any(p["type"] == "heartbeat" for p in inputs)
    assert any(p["type"] == "trade" for p in inputs)
    assert not list((tmp_path / "collector").glob("raw/*"))


def test_shutdown_deadline_reports_abandoned_queue(research_recorder, tmp_path, monkeypatch):
    release = threading.Event()
    original = ResearchLog._writer

    def delayed(log):
        assert release.wait(2)
        original(log)

    monkeypatch.setattr(ResearchLog, "_writer", delayed)
    log = research_recorder(tmp_path)
    log.emit("input", {"id": "unfinished"})
    log.close_deadline = 0
    log.stopping.set()
    release.set()
    log.thread.join(2)
    assert not log.thread.is_alive()
    status = json.loads(next(tmp_path.rglob("status.json")).read_text())
    assert status["error"] == "SHUTDOWN_TIMEOUT"
    assert status["dropped"] == 1 and status["queued_bytes"] == 0
    assert not status["clean_shutdown"] and not status["capture_complete"]
    assert status["last_capture_seq"] > status["last_written_seq"]


def test_low_disk_recovery_marks_only_missing_sequences(research_recorder, tmp_path, monkeypatch):
    import time

    import btc15.research_log as module

    offset = [0]
    allow = [False]
    dropped = threading.Event()
    original_drop = ResearchLog._drop

    class Clock:
        def __getattr__(self, name):
            return getattr(time, name)

        def monotonic(self):
            return time.monotonic() + offset[0]

    def observe_drop(log, reason, count=1):
        original_drop(log, reason, count)
        if reason == "low_disk":
            dropped.set()

    monkeypatch.setattr(module, "time", Clock())
    monkeypatch.setattr(ResearchLog, "_retention", lambda self: allow[0])
    monkeypatch.setattr(ResearchLog, "_drop", observe_drop)
    log = research_recorder(tmp_path)
    log.emit("input", {"id": "low-disk"})
    assert dropped.wait(2)
    allow[0] = True
    offset[0] += 10
    # Wake the writer: it may already be waiting after the old maintenance check.
    log.emit("input", {"id": "wake-up"})
    # Waiting for queue completion isn't available; a subsequent maintenance check
    # is observed via the callback before enqueueing the known recoverable input.
    maintained = threading.Event()

    def recovered(_):
        maintained.set()
        return True

    monkeypatch.setattr(ResearchLog, "_retention", recovered)
    offset[0] += 10
    assert maintained.wait(2)
    log.emit("input", {"id": "recovered"})
    log.close()
    rr = records(tmp_path)
    assert rr[-1]["body"]["id"] == "recovered"
    gaps = [r for r in rr if r["kind"] == "recording_gap"]
    assert gaps[0]["body"]["first_missing_seq"] == 1
    assert sum(g["body"]["dropped_records"] for g in gaps) == log.status()["dropped"]
    assert not log.status()["capture_complete"]


def test_recording_preserves_paper_fills(research_recorder, scenario, market, now, tmp_path):
    outcomes = []
    for enabled in (False, True):
        fixture = scenario(buy=False)
        engine = fixture.e
        log = research_recorder(tmp_path) if enabled else None
        engine.research_log = log
        engine.process(now, "candidate", "orderbook_snapshot", {"market_ticker": market.ticker})
        order = engine.executor.orders[market.ticker]
        fixture.refresh(now + 0.5)
        engine.process(
            now + 0.5,
            "fill",
            "trade",
            dict(
                market_ticker=market.ticker,
                trade_id="fill",
                ts_ms=(now + 0.5) * 1000,
                taker_outcome_side="no",
                yes_price_dollars=str(order.limit),
                count_fp="0.40",
            ),
        )
        if log:
            log.close()
        fills = engine.store.list(kind="fill", run_id=engine.run_id)
        assert fills
        outcomes.append([[r["body"].get(k) for k in ("action", "quantity", "price", "fee")] for r in fills])
    assert outcomes[0] == outcomes[1]


def test_concurrent_capture_orders_and_freezes_payloads(research_recorder, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    log = research_recorder(tmp_path)

    def capture(producer):
        for number in range(30):
            body = {"producer": producer, "number": number}
            assert log.emit("input", body)
            body["number"] = "mutated after capture"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(capture, range(4)))
    log.close()
    rr = records(tmp_path)
    assert [r["capture_seq"] for r in rr] == list(range(1, 121))
    assert {(r["body"]["producer"], r["body"]["number"]) for r in rr} == {
        (producer, number) for producer in range(4) for number in range(30)
    }
    assert log.status()["dropped"] == 0


def test_bad_source_clock_does_not_discard_original_input(research_recorder, tmp_path):
    log = research_recorder(tmp_path)
    payload = dict(type="orderbook_delta", msg={"ts_ms": "not-a-clock"})
    row = dict(id="invalid", received=0, monotonic_ns=1, connection_id="c", payload=dumps(payload))
    assert log.capture_input(row, payload)
    log.close()
    event = records(tmp_path)[0]
    assert event["source_time"] is None
    assert json.loads(event["body"]["payload"]) == payload
