import gzip
import json
import time
from types import SimpleNamespace

from research_helpers import records

from btc15.research_log import start_research_log
from btc15.strategies.settlement_edge.config import Strategy


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BTC15_RESEARCH_LOG_ENABLED", raising=False)
    monkeypatch.setenv("BTC15_RESEARCH_LOG_DIR", str(tmp_path / "unused"))
    assert start_research_log(Strategy(), "run") is None
    assert not (tmp_path / "unused").exists()


def test_portable_records_and_source(research_recorder, tmp_path):
    r = research_recorder(tmp_path)
    assert r.emit("input", {"received": 123, "payload": {"type": "heartbeat", "msg": {}}})
    r.close()
    assert not r.thread.is_alive()
    assert records(tmp_path)[0]["body"]["received"] == 123
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text())
    assert manifest["config"]["asset"] == "BTC"
    assert manifest["source_sha256"]
    assert json.load(gzip.open(next(tmp_path.rglob("source.json.gz")), "rt"))
    status = json.loads(next(tmp_path.rglob("status.json")).read_text())
    assert status["clean_shutdown"] and status["capture_complete"]


def test_oversize_record_is_dropped_and_gap_persisted(research_recorder, tmp_path):
    r = research_recorder(tmp_path, max_queue_bytes=512)
    assert not r.emit("input", {"big": "x" * 1000})
    assert r.emit("input", {"small": True})
    r.close()
    assert r.status()["dropped"] == 1
    rr = records(tmp_path)
    assert rr[0]["kind"] == "recording_gap"
    assert rr[0]["body"]["dropped_records"] == 1
    assert rr[1]["kind"] == "input"


def test_low_disk_does_not_stop_caller(research_recorder, tmp_path, monkeypatch):
    monkeypatch.setattr("btc15.research_log.shutil.disk_usage", lambda _: SimpleNamespace(free=-1))
    r = research_recorder(tmp_path)
    r.emit("input", {})
    r.close()
    assert not records(tmp_path)
    assert r.status()["dropped"] == 1
    assert r.status()["error"] == "LOW_DISK"


def test_retention_archives_closed_recordings_with_context(research_recorder, tmp_path):
    old = tmp_path / "ETH" / "old"
    old.mkdir(parents=True)
    (old / "events-1.jsonl.gz").write_bytes(b"old")
    (old / "events-2.jsonl.gz.part").write_bytes(b"active")
    (old / "unrelated.txt").write_text("keep")
    (old / "manifest.json").write_text('{"session":"old"}')
    (old / "source.json.gz").write_bytes(b"source")
    r = research_recorder(tmp_path, max_disk_bytes=0)
    r.emit("input", {})
    r.close()
    assert not (old / "events-1.jsonl.gz").exists()
    archived = r.archive_dir / "ETH" / "old"
    assert (archived / "events-1.jsonl.gz").read_bytes() == b"old"
    assert (archived / "source.json.gz").read_bytes() == b"source"
    assert json.loads((archived / "manifest.json").read_text()) == {"session": "old"}
    assert (old / "events-2.jsonl.gz.part").exists()
    assert (old / "unrelated.txt").exists()


def test_writer_error_does_not_escape(research_recorder, tmp_path):
    root = tmp_path / "file"
    root.write_text("cannot create directory here")
    r = research_recorder(root)
    r.thread.join(2)
    assert r.status()["error"]
    assert not r.emit("input", {})


def test_outside_window_diagnostics_and_intrasecond_crossings(research_recorder, tmp_path):
    from decimal import Decimal

    from btc15.domain import Book

    config = Strategy()
    r = research_recorder(tmp_path)
    now = time.time()
    book = Book()
    book.yes = {Decimal(".80"): Decimal(10)}
    book.no = {Decimal(".18"): Decimal(10)}
    book.valid, book.received, book.source_time = True, now, now
    evaluation = dict(
        timestamp=now,
        probability={"p_yes": 0.84, "p_no": 0.16},
        book=book.summary(),
        reasons=[{"code": "ENTRY_WINDOW"}],
        seconds_remaining=600,
    )
    engine = SimpleNamespace(
        config=config,
        markets={"M": SimpleNamespace(open_time=now - 300, close_time=now + 600)},
        books={"M": book},
        latest={"M": evaluation},
    )
    row = dict(received=now, payload="{}", id="reference-1")
    r.capture(engine, row, {"type": "cfbenchmarks_value"})
    # Below 83%, then back above it, inside one second: both changes must survive sampling.
    for offset, probability in [(0.1, 0.80), (0.2, 0.84)]:
        evaluation.update(timestamp=now + offset, probability={"p_yes": probability, "p_no": 1 - probability})
        r.capture(engine, dict(row, received=now + offset), {"type": "heartbeat"})
    book.yes = {Decimal(".54"): Decimal(10)}
    r.capture(engine, dict(row, received=now + 0.3), {"type": "orderbook_delta"})
    r.close()
    rr = records(tmp_path)
    ev = [x["body"] for x in rr if x["kind"] == "evaluation"]
    assert len(ev) == 3
    assert ev[0]["seconds_remaining"] == 600
    assert ev[0]["confidence_83"] == [True, False]
    books = [x["body"] for x in rr if x["kind"] == "book"]
    assert len(books) == 2 and books[-1]["yes_bid"] == 0.54
    assert books[-1]["threshold_change"]
    assert not r.status()["error"]


def test_journal_capture_includes_market_and_filters_asset(research_recorder, tmp_path):
    import sqlite3

    settlement = tmp_path / "paper.db"
    orders = tmp_path / "orders.db"
    with sqlite3.connect(settlement) as db:
        db.execute("create table records (market text, timestamp real, kind text, body text)")
        db.execute(
            "insert into records values (?,?,?,?)", ("KXBTC15M-test", 123, "settlement", '{"result":"yes"}')
        )
    with sqlite3.connect(orders) as db:
        db.execute("create table manual_orders (body text)")
        for asset in ("BTC", "ETH"):
            db.execute(
                "insert into manual_orders values (?)",
                (
                    json.dumps(
                        dict(
                            id=asset,
                            updated_at=1,
                            request=dict(ticker="KX" + asset + "15M-test"),
                            exchange_order=dict(user_id="private", fill_count_fp="10"),
                        )
                    ),
                ),
            )
    r = research_recorder(tmp_path / "logs", settlement_db=settlement, order_db=orders)
    # Exercise polling deterministically even if close happens before writer starts.
    r._audit_records()
    r.close()
    rr = records(tmp_path / "logs")
    assert any(x["kind"] == "settlement_journal" and x["body"]["market"] == "KXBTC15M-test" for x in rr)
    oo = [x["body"] for x in rr if x["kind"] == "order_journal"]
    assert oo and all(x["id"] == "BTC" for x in oo)
    assert all("user_id" not in x["exchange_order"] for x in oo)


def test_rotation_produces_independently_readable_segments(research_recorder, tmp_path):
    r = research_recorder(tmp_path, rotation_seconds=0)
    r.emit("input", {"number": 1})
    r.emit("input", {"number": 2})
    r.close()
    paths = list(tmp_path.rglob("events-*.jsonl.gz"))
    assert len(paths) == 2
    assert sorted(x["body"]["number"] for x in records(tmp_path)) == [1, 2]
    assert not list(tmp_path.rglob("*.part"))
