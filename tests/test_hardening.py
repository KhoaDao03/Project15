import copy
import json
from pathlib import Path

import pytest
from test_execution import decision, ready

from btc15.audit import audit_files, unique_events
from btc15.demo import generate
from btc15.engine import Engine
from btc15.execution import PaperExecutor
from btc15.storage import read_events


def test_fill_failure_rolls_back_ledger_risk_and_checkpoint(store, market, book, now, config, monkeypatch):
    executor = ready(store, market, now, config)
    order = executor.submit(market, book, decision(), "op", now, True)
    before = copy.deepcopy(executor.snapshot())
    original = store.checkpoint

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "checkpoint", fail)
    with pytest.raises(OSError, match="disk full"):
        executor.fill(order, 1.25, order.limit, now + 1, True)
    assert executor.snapshot() == before
    assert not store.list(kind="fill")
    assert store.state("run", market.ticker) == "ORDER_PENDING"
    monkeypatch.setattr(store, "checkpoint", original)
    executor.fill(executor.orders[market.ticker], 1.25, order.limit, now + 1, True)
    assert len(store.list(kind="fill")) == 1


def test_settlement_failure_retry_is_atomic(store, market, book, now, config, monkeypatch):
    executor = ready(store, market, now, config)
    order = executor.submit(market, book, decision(), "op", now, True)
    executor.fill(order, order.quantity, order.limit, now + 1, True)
    original = executor.finish

    def fail(*args):
        original(*args)
        raise OSError("commit failed")

    monkeypatch.setattr(executor, "finish", fail)
    with pytest.raises(OSError):
        executor.settle(market, "yes", market.close_time + 1)
    assert not store.list(kind="settlement")
    assert not store.list(kind="trade_result")
    assert executor.positions[market.ticker].quantity == order.quantity
    monkeypatch.setattr(executor, "finish", original)
    executor.settle(market, "yes", market.close_time + 2)
    executor.settle(market, "yes", market.close_time + 3)
    assert len(store.list(kind="settlement")) == len(store.list(kind="trade_result")) == 1


def test_checkpoint_restores_partial_fill_fees_and_duplicate_guard(store, market, book, now, config):
    executor = ready(store, market, now, config)
    order = executor.submit(market, book, decision(), "op", now, True)
    trade = dict(
        trade_id="one",
        ts_ms=(now + 1) * 1000,
        taker_outcome_side="no",
        yes_price_dollars=str(order.limit),
        count_fp="1.25",
    )
    executor.trade(market, trade, now + 1)
    restored = PaperExecutor(store, "run", "PAPER", config)
    restored.restore(store.load_checkpoint("run"))
    assert restored.snapshot() == executor.snapshot()
    restored.trade(market, trade, now + 2)
    assert restored.positions[market.ticker].quantity == 1.25
    assert len(store.list(kind="fill")) == 1
    restored.cancel(market.ticker, now + 3, "resume")
    restored.settle(market, "no", market.close_time + 1)
    assert not restored.positions and not restored.risk.reserved


def test_duplicate_claim_does_not_abort_outer_transaction(store):
    assert store.claim("r", "same")
    with store.transaction():
        assert not store.claim("r", "same")
        store.add("test", {}, "r", "PAPER", 1)
    assert len(store.list(kind="test")) == 1


def test_audit_deduplicates_mirrors_and_rejects_conflicts(tmp_path):
    path = generate(tmp_path / "demo.jsonl")
    report = audit_files([path, path])
    assert report["synthetic"] and report["research_valid"]
    assert report["duplicate_events"] == report["events"]
    rows = list(unique_events([path, path]))
    assert len(rows) == report["events"]
    rows[0]["received"] -= 1
    conflict = tmp_path / "conflict.jsonl"
    conflict.write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="Conflicting"):
        audit_files([path, conflict])


def test_wall_clock_step_retains_order_and_invalidates_engine(store, config, tmp_path):
    rows = [
        dict(
            id=str(i),
            received=t,
            monotonic_ns=i * 1000000000,
            connection_id="c",
            payload=dict(type="heartbeat", msg={}),
        )
        for i, t in [(1, 10), (2, 9.5), (3, 10.5)]
    ]
    path = tmp_path / "clock.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert list(read_events(path)) == rows
    engine = Engine(store, config, "BACKTEST", execute=False)
    assert engine.ingest(rows[0])
    assert engine.ingest(rows[1])
    assert not engine.clock_ok
    assert engine.ingest(rows[2])
    assert len(audit_files([path])["clock_adjustments"]) == 1


def test_missing_scheduled_fees_are_visible_and_blocked(store, config, tmp_path):
    engine = Engine(store, config, "BACKTEST")
    for row in read_events(generate(tmp_path / "demo.jsonl")):
        if row["payload"]["type"] == "metadata":
            row["payload"]["msg"].pop("series_fee_changes")
        engine.ingest(row)
        if engine.latest:
            break
    assert not engine.executor.orders
    assert "UNVERIFIED_FEES" in [r["code"] for r in next(iter(engine.latest.values()))["reasons"]]


def test_day_block_intervals_require_independent_days():
    from btc15.analytics import daily_block_interval

    assert daily_block_interval([(1, 1)] * 1000)["status"] == "INSUFFICIENT_INDEPENDENT_DAYS"
    rows = [(i * 86400, 0.04) for i in range(20)]
    report = daily_block_interval(rows)
    assert report["interval"] == pytest.approx([0.04, 0.04])
    assert report == daily_block_interval(rows)


def test_engine_resume_cancels_resting_remainder_but_recovers_position(store, config, tmp_path):
    engine = Engine(store, config, "PAPER")
    for row in read_events(generate(tmp_path / "resume.jsonl")):
        engine.ingest(row)
        if engine.executor.positions:
            break
    assert engine.executor.positions
    before = copy.deepcopy(engine.executor.positions)
    count = len(store.list(kind="fill"))
    resumed = Engine(store, config, "PAPER", run_id=engine.run_id, resume=True)
    assert resumed.executor.positions == before
    assert all(not o.active for o in resumed.executor.orders.values())
    assert len(store.list(kind="fill")) == count
    assert not resumed.healthy and not resumed.clock_ok


def test_recorder_closes_journal_even_if_parquet_flush_fails(tmp_path, monkeypatch):
    from btc15.storage import RawRecorder

    recorder = RawRecorder(tmp_path)
    recorder.append({"type": "heartbeat"}, 1, 1, "c")

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "flush", fail)
    with pytest.raises(OSError):
        recorder.close()
    assert recorder.journal.closed
    assert len(list(read_events(next(tmp_path.glob("*.jsonl"))))) == 1


def test_omitted_empty_snapshot_levels_clear_previous_quotes(book, now):
    from btc15.domain import D

    payload = json.loads((Path(__file__).parent / "fixtures/empty-book-snapshot-20260909.json").read_text())
    book.snapshot(payload["msg"], now + 1)
    assert book.valid and not book.yes and not book.no
    assert book.ask("yes") is None and book.bid("no") is None
    book.delta(dict(side="yes", price_dollars=".80", delta_fp="1.25"), now + 2)
    assert book.yes == {D(".80"): D("1.25")}


def test_pending_market_book_survives_strike_publication(store, config, raw, series, now):
    engine = Engine(store, config, "BACKTEST", execute=False)

    def row(payload, t):
        return dict(id=str(t), received=t, monotonic_ns=int(t * 1e9), connection_id="c", payload=payload)

    pending = {**raw, "floor_strike": None, "status": "initialized"}
    msg = dict(
        series=series,
        markets=[pending],
        clock_skew=0,
        exchange_status={"trading_active": True},
        fee_changes={},
        series_fee_changes=[],
    )
    assert engine.ingest(row(dict(type="metadata", msg=msg), now))
    assert raw["ticker"] not in engine.markets
    assert engine.ingest(
        row(
            dict(
                type="orderbook_snapshot",
                sid=3,
                seq=1,
                msg=dict(
                    market_ticker=raw["ticker"], yes_dollars_fp=[[".80", "2"]], no_dollars_fp=[[".85", "3"]]
                ),
            ),
            now + 0.1,
        )
    )
    warm_book = engine.books[raw["ticker"]]
    assert engine.ingest(row(dict(type="metadata", msg={**msg, "markets": [raw]}), now + 0.2))
    assert engine.books[raw["ticker"]] is warm_book and warm_book.valid
    assert engine.ingest(
        row(
            dict(
                type="orderbook_delta",
                sid=3,
                seq=2,
                msg=dict(market_ticker=raw["ticker"], side="yes", price_dollars=".80", delta_fp="1"),
            ),
            now + 0.3,
        )
    )
    assert not store.list(kind="health")
