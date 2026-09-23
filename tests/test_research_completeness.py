import json
import sqlite3
from dataclasses import asdict

import pytest

from btc15 import execution_journal as journal
from btc15.reference_history import load_history, save_history, validate_history
from btc15.research_archive import archive
from btc15.research_coverage import Coverage, pending_markets, recover_settlements
from btc15.strategies.settlement_edge.model import Tick

TICKER = "KXBTC15M-TEST"


def metadata(coverage):
    market = dict(
        ticker=TICKER,
        open_time="1970-01-01T00:01:40Z",
        close_time="1970-01-01T00:03:20Z",
    )
    payload = dict(type="metadata", msg=dict(markets=[market]))
    coverage.observe(dict(kind="input", body=dict(payload=payload), received_at=90))


def test_execution_journal_immutable_unique_and_private():
    db = sqlite3.connect(":memory:")
    journal.initialize(db)
    row = dict(
        id="local",
        request=dict(ticker=TICKER, confirm="REAL_MONEY"),
        timing=dict(decision_id="decision"),
        exchange_order=dict(order_id="exchange", user_id="secret"),
    )
    event = journal.append(db, "fill", row=row, event_id="unique", received_at=20, source_time=19)
    assert journal.append(db, "fill", row=row, event_id="unique") is None
    assert list(journal.read_events(db))[0] == event
    assert "secret" not in str(event) and "REAL_MONEY" not in str(event)
    assert event["decision_id"] == "decision"
    for sql in ("DELETE FROM execution_events", "UPDATE execution_events SET kind='lost'"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(sql)
    details = journal.fill_details(
        dict(trade_id="fill", count_fp="2.00", yes_price_dollars="0.75", fee_cost="0.02"), "no"
    )
    assert details["price_dollars"] == "0.25" and details["economics_complete"]
    assert not journal.fill_details({}, None)["economics_complete"]
    assert journal.fill_source_time(dict(ts_ms="123000")) == 123
    assert journal.fill_source_time(dict(ts="nan")) is None


def test_accepted_reference_journal_is_causal_and_conflicts_fail_closed(tmp_path, config):
    now = 1000000
    coverage = Coverage(tmp_path, "BTC", "s", now)
    for tick in (Tick(now - 2, now - 2, 60000), Tick(now - 1, now + 1, 60001)):
        coverage.observe(dict(kind="reference_sample", body=dict(asdict(tick), input_id="id")))
    body, _ = load_history(tmp_path, [], now, config, reference_journal=tmp_path / "coverage.sqlite")
    assert validate_history(body, now, config) == [Tick(now - 2, now - 2, 60000)]
    save_history(tmp_path, [Tick(now - 2, now - 2, 61000)], now, config)
    body, report = load_history(tmp_path, [], now, config, reference_journal=tmp_path / "coverage.sqlite")
    assert not body["samples"] and report["status"] == "COLD_START"
    coverage.close()


def test_late_settlement_keeps_first_receipt_and_original_session(tmp_path):
    first = Coverage(tmp_path, "BTC", "first", 90)
    metadata(first)
    assert len(pending_markets(tmp_path, "BTC", 201)) == 1
    assert pending_markets(tmp_path, "BTC", 201, after=TICKER) == []
    first.close()
    second = Coverage(tmp_path, "BTC", "second", 300)
    for receipt in (310, 320):
        second.observe(
            dict(
                kind="settlement_observation",
                body=dict(market=TICKER, result="no"),
                received_at=receipt,
                source_time=200,
                capture_seq=1,
            )
        )
    market = second.summary(dict(capture_complete=True), 330)["markets"][0]
    assert market["settlement"]["received_at"] == 310
    assert market["origin_sessions"] == ["first"]
    assert "NO_BOOK_SNAPSHOT" in market["incomplete_reasons"]
    assert "PARTIAL_MARKET_WINDOW" in market["incomplete_reasons"]
    assert pending_markets(tmp_path, "BTC", 330) == []
    second.close()


@pytest.mark.anyio
async def test_recovery_observes_without_injecting_historical_inputs(tmp_path):
    import asyncio
    from types import SimpleNamespace

    coverage = Coverage(tmp_path, "BTC", "first", 90)
    metadata(coverage)
    coverage.close()
    stop, events = asyncio.Event(), []

    class Client:
        async def get(self, path, params):
            stop.set()
            return dict(
                market=dict(
                    ticker=TICKER, close_time="1970-01-01T00:03:20+00:00", status="finalized", result="no"
                )
            )

    log = SimpleNamespace(
        root=tmp_path,
        asset="BTC",
        emit=lambda *a, **k: events.append((a, k)),
        fail=lambda e: pytest.fail(str(e)),
    )
    await recover_settlements(log, Client(), stop)
    assert events[0][0][0] == "settlement_observation"
    assert events[0][0][2] > 200


def test_archive_is_additive_excludes_active_files_and_checks_sequences(research_recorder, tmp_path):
    source, destination = tmp_path / "source", tmp_path / "archive"
    log = research_recorder(source)
    log.emit("test", dict(value=1))
    log.close()
    directory = next(source.glob("*/*/manifest.json")).parent
    (directory / "events-active.jsonl.gz.part").write_text("active")
    result = archive(source, destination)
    assert result["copied_files"] == 3
    assert not result["sessions"][0]["errors"]
    assert not result["sessions"][0]["provisional"]
    assert not list(destination.rglob("*.part")) and not list(destination.rglob("*.sqlite"))
    assert archive(source, destination)["copied_files"] == 0
    segment = next(directory.glob("events-*.jsonl.gz"))
    segment.unlink()
    assert archive(source, destination)["sessions"][0]["records"] == 1
    target = next(destination.glob("*/*/manifest.json"))
    target.write_text(json.dumps(dict(corrupted=True)))
    with pytest.raises(ValueError, match="conflict"):
        archive(source, destination)
    with pytest.raises(ValueError, match="separate"):
        archive(source, source / "nested")


def test_journal_backfill_retries_queue_loss_and_keeps_original_clocks(tmp_path):
    from types import SimpleNamespace

    from btc15.research_log import ResearchLog

    path = tmp_path / "orders.sqlite"
    with sqlite3.connect(path) as db:
        journal.initialize(db)
        journal.append(db, "acknowledgment", market=TICKER, received_at=10, source_time=9)
    delivered = []
    fake = SimpleNamespace(
        order_db=path, asset="BTC", execution_cursor=0, capture_execution=lambda *a, **k: False
    )
    ResearchLog._execution_records(fake)
    assert fake.execution_cursor == 0
    fake.capture_execution = lambda event, **kwargs: delivered.append(event) or True
    ResearchLog._execution_records(fake)
    assert fake.execution_cursor == 1
    assert delivered[0]["received_at"] == 10 and delivered[0]["source_time"] == 9
    ResearchLog._execution_records(fake)
    assert len(delivered) == 1


def test_book_reconnect_requires_new_snapshot_in_coverage(tmp_path):
    coverage = Coverage(tmp_path, "BTC", "s", 90)
    metadata(coverage)
    for i, (kind, connection) in enumerate(
        (("orderbook_snapshot", "a"), ("disconnect", "a"), ("orderbook_delta", "b"))
    ):
        coverage.observe(
            dict(
                kind="input",
                body=dict(connection_id=connection, payload=dict(type=kind, msg=dict(market_ticker=TICKER))),
                received_at=100 + i,
            )
        )
    market = coverage.summary(dict(capture_complete=True), 210)["markets"][0]
    assert "BOOK_CONTINUITY_BREAK" in market["incomplete_reasons"]
    coverage.close()


def test_reference_diagnostics_distinguish_gap_from_price_floor(config, market):
    from bleep_helpers import inputs
    from test_reference_rolling_atr import reference_ticks

    from btc15.strategies.settlement_edge.bleep import probability
    from btc15.strategies.settlement_edge.model import features

    ticks = [t for t in reference_ticks() if not 2100 <= t.source < 2160]
    readiness = features(ticks, 2400, config)["reference_readiness"]
    assert not readiness["atr_ready"]
    assert readiness["missing_seconds"] == [dict(first_missing=2100, last_missing=2159, count=60)]
    assert "MISSING_REFERENCE_SECONDS" in readiness["reasons"]
    now = market.close_time - 120
    f = inputs(market.spec.strike)
    ticks = [Tick(now, now, market.spec.strike)]
    result = probability(market.spec, ticks, now, f, config)
    assert result["atr_fallback_reason"] == "INSUFFICIENT_CONTIGUOUS_REFERENCE_CANDLES"
    f["reference_rolling_atr"] = 0
    result = probability(market.spec, ticks, now, f, config)
    assert result["atr_fallback_reason"] == "MEASURED_ATR_BELOW_PRICE_FLOOR"
