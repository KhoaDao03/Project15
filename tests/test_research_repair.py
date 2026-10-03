"""Regression tests for sampled research, retryable reads and interval coverage."""

import json
import sqlite3
import time
from decimal import Decimal
from types import SimpleNamespace

from research_helpers import records

from btc15 import execution_journal
from btc15.domain import Book, dumps
from btc15.research_archive import rebuild_session_coverage
from btc15.research_coverage import Coverage


def test_sampled_mode_bounds_delta_volume_and_keeps_observed_full_books(research_recorder, tmp_path):
    log = research_recorder(tmp_path, capture_mode="sampled")
    payload = dict(type="orderbook_delta", sid=1, seq=1, msg=dict(market_ticker="KXBTC15M-test"))
    row = dict(id="delta", received=time.time(), connection_id="c", payload=dumps(payload))
    for i in range(10000):
        assert log.capture_input(row, payload)
        assert log.emit("input_processed", dict(input_id=str(i)))
    assert log.capture_seq == 0
    book = Book()
    book.yes = {Decimal(i) / 1000: Decimal(i) for i in range(1, 100)}
    book.no = {Decimal(".1"): Decimal(10)}
    book.valid, book.received, book.source_time = True, row["received"], row["received"]
    engine = SimpleNamespace(
        config=SimpleNamespace(book_max_age=2, max_clock_skew=2),
        markets={
            "KXBTC15M-test": SimpleNamespace(open_time=row["received"] - 1, close_time=row["received"] + 900)
        },
        books={"KXBTC15M-test": book},
        latest={},
    )
    log.capture(engine, row, payload)
    for kind in ["reference_sample", "trade", "model_calculation", "decision_check"]:
        if kind == "reference_sample":
            body = dict(source=row["received"], received=row["received"], price=50000, input_id="ref")
        else:
            body = dict(evidence=kind)
        assert log.emit(kind, body)
    log.close()
    rr = records(tmp_path)
    snapshot = next(r["body"] for r in rr if r["kind"] == "book")
    assert len(snapshot["yes_levels"]) == 99
    assert snapshot["yes_levels"][-1] == ["0.099", "99"]
    assert [r["capture_seq"] for r in rr] == list(range(1, len(rr) + 1))
    assert all(r["schema_version"] == 3 for r in rr)
    assert log.status()["sampled_out"] == dict(orderbook_delta=10000, input_processed=10000)
    assert log.status()["dropped"] == 0 and not log.status()["exact_replay"]
    assert json.loads((log.directory / "manifest.json").read_text())["capture_mode"] == "sampled"


def test_locked_execution_journal_retries_without_fabricating_feed_loss(research_recorder, tmp_path):
    path = tmp_path / "orders.db"
    with sqlite3.connect(path) as db:
        execution_journal.initialize(db)
        execution_journal.append(db, "fill", market="KXBTC15M-test", received_at=10)
    log = research_recorder(tmp_path / "capture")
    log.order_db = path
    db = sqlite3.connect(path)
    try:
        db.execute("BEGIN EXCLUSIVE")
        log._poll("execution_journal_read", log._execution_records)
        assert log.execution_cursor == 0
        assert log.status()["dropped"] == 0
        assert log.status()["diagnostics"]["execution_journal_read"]["sqlite_errorname"] == "SQLITE_BUSY"
    finally:
        db.rollback()
        db.close()
    log._poll("execution_journal_read", log._execution_records)
    assert log.execution_cursor == 1
    assert "execution_journal_read" not in log.status()["diagnostics"]
    log.close()
    rr = records(tmp_path / "capture")
    assert any(r["kind"] == "execution_event" and r["received_at"] == 10 for r in rr)
    assert not any(r["kind"] == "recording_gap" for r in rr)


def test_coverage_failure_does_not_erase_successful_capture(research_recorder, tmp_path, monkeypatch):
    original = Coverage.observe

    def fail(self, record):
        if record["kind"] == "reference_sample":
            raise sqlite3.OperationalError("database is locked")
        return original(self, record)

    monkeypatch.setattr(Coverage, "observe", fail)
    log = research_recorder(tmp_path)
    log.emit("reference_sample", dict(source=100, received=100, price=50000, input_id="id"), 100)
    log.close()
    assert log.status()["dropped"] == 0 and log.status()["capture_complete"]
    assert "coverage_rebuild_required" in log.status()["diagnostics"]
    assert [r["kind"] for r in records(tmp_path)] == ["reference_sample"]
    monkeypatch.setattr(Coverage, "observe", original)
    rebuilt = rebuild_session_coverage(log.directory, log.metadata)
    assert not rebuilt["errors"] and rebuilt["capture_complete"]


def test_gap_only_blocks_markets_whose_window_or_warmup_overlaps(tmp_path):
    c = Coverage(tmp_path, "BTC", "s", 0)
    try:
        for name, opened, closed in [("earlier", 100, 200), ("affected", 300, 400), ("later", 4101, 4200)]:
            c.market(name).update(
                open_time=opened, close_time=closed, book_snapshots=1, settlement={"result": "yes"}
            )
        c.observe(
            dict(
                kind="recording_gap",
                body=dict(dropped_records=5, previous_capture_at=310, next_capture_at=320),
            )
        )
        rows = {r["market"]: r for r in c.summary(dict(capture_complete=False, dropped=5), 4300)["markets"]}
        assert not rows["earlier"]["incomplete_reasons"]
        assert rows["affected"]["incomplete_reasons"] == ["RECORDING_GAP_IN_DEPENDENCIES"]
        assert not rows["later"]["incomplete_reasons"]
    finally:
        c.close()


def test_sampled_snapshots_do_not_report_intentionally_omitted_sequence_as_gap(tmp_path):
    c = Coverage(tmp_path, "BTC", "s", 0, capture_mode="sampled")
    try:
        for seq in [1, 1000]:
            c.observe(
                dict(
                    kind="input",
                    body=dict(
                        connection_id="c",
                        payload=dict(
                            type="orderbook_snapshot", sid=1, seq=seq, msg=dict(market_ticker="KXBTC15M-test")
                        ),
                    ),
                    received_at=seq,
                )
            )
        assert c.market("KXBTC15M-test")["sequence_gaps"] == 0
        assert not c.summary(dict(capture_complete=True), 2000)["exact_replay"]
    finally:
        c.close()


def test_rebuilt_prefix_loss_and_partial_tail_stay_visible(research_recorder, tmp_path):
    import gzip

    log = research_recorder(tmp_path)
    for i in range(4):
        log.emit("test", dict(i=i))
    log.close()
    segment = next(log.directory.glob("events*.gz"))
    rr = records(tmp_path)
    with gzip.open(segment, "wt") as f:
        f.write(json.dumps(rr[1]) + "\n")
        f.write(json.dumps(rr[2]) + "\n")
    rebuilt = rebuild_session_coverage(log.directory, log.metadata)
    assert len(rebuilt["recording_gap_intervals"]) == 2
    assert rebuilt["recording_gap_intervals"][-1]["end"] is None
    assert not rebuilt["capture_complete"]


def test_legacy_settlement_cursor_advances_only_after_admission(tmp_path):
    from btc15.research_log import ResearchLog

    path = tmp_path / "settlements.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records(market TEXT,timestamp REAL,kind TEXT,body TEXT)")
        db.execute("INSERT INTO records VALUES ('KXBTC15M-test', 10, 'settlement', '{}')")
    log = SimpleNamespace(settlement_db=path, order_db=None, settlement_cursor=0, emit=lambda *a, **kw: False)
    ResearchLog._audit_records(log)
    assert log.settlement_cursor == 0
    log.emit = lambda *a, **kw: True
    ResearchLog._audit_records(log)
    assert log.settlement_cursor == 1


def test_retention_failure_is_visible_without_discarding_new_capture(
    research_recorder, tmp_path, monkeypatch
):
    def unavailable(*args):
        raise OSError("retention unavailable")

    monkeypatch.setattr("btc15.research_log.retain_completed", unavailable)
    log = research_recorder(tmp_path)
    log.emit("test", dict(value=1))
    log.close()
    assert log.status()["diagnostics"]["retention_cleanup"]["message"] == "retention unavailable"
    assert log.status()["dropped"] == 0
    assert len(list(log.directory.glob("events*.gz"))) == 1
