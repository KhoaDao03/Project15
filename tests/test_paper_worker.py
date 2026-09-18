import asyncio
import json
import os
import time

from test_collection import fake_client, fake_socket
from test_position_management import scenario  # noqa: F401

from btc15.config import Settings
from btc15.engine import Engine
from btc15.paper_worker import PaperWorker, SignalStore
from btc15.runner import collect
from btc15.storage import Store


def test_signal_engine_never_submits_paper_orders(config):
    store = SignalStore()
    try:
        engine = Engine(store, config, execute=False, signal_only=True, record_evaluations=False)
        assert not engine.execute
        assert not store.run_summaries("PAPER")
        assert not engine.executor.positions
    finally:
        store.close()


def test_worker_is_separate_and_preserves_ordered_inputs_and_checkpoint(tmp_path, config):
    database = "sqlite:///" + str(tmp_path / "paper.db")
    store = Store(database)
    worker = PaperWorker(database, config, "test", False, False, tmp_path)
    try:
        ready = worker.start()
        assert ready["process_id"] != os.getpid()
        now = time.time()
        rows = [
            dict(
                id=str(n),
                received=now + n / 10000,
                monotonic_ns=time.monotonic_ns() + n,
                connection_id="test",
                payload=json.dumps(dict(type="heartbeat", msg={})),
            )
            for n in range(10)
        ]
        assert worker.submit(rows)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if worker.poll().get("processed_events") == 10:
                break
            time.sleep(0.02)
        assert worker.status["processed_events"] == 10
        assert store.load_checkpoint("test") is not None
    finally:
        worker.close()
        store.engine.dispose()


def test_separated_collector_publishes_and_stops_worker(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type="ticker", msg={})])
    run = asyncio.run(
        collect(
            Settings(data_dir=str(tmp_path)),
            config,
            store,
            paper=True,
            managed_run="separated",
            duration=0.15,
            separate_paper=True,
        )
    )
    assert run == "separated"
    assert store.load_checkpoint(run) is not None
    assert store.read_market_display("paper_worker")["state"] == "STOPPED"
    status = store.list(kind="status", run_id=run, limit=1, newest_first=True)[0]["body"]
    assert status["paper_worker"]["process_id"] != os.getpid()


def test_overflow_fails_paper_without_blocking_sender(tmp_path, config):
    import signal

    worker = PaperWorker(
        "sqlite:///" + str(tmp_path / "overflow.db"), config, "overflow", False, False, tmp_path, capacity=1
    )
    try:
        worker.start()
        os.kill(worker.process.pid, signal.SIGSTOP)
        time.sleep(0.02)
        started = time.monotonic()
        assert worker.submit([])
        assert not worker.submit([])
        assert time.monotonic() - started < 0.2
        assert worker.poll()["state"] == "FAILED"
    finally:
        os.kill(worker.process.pid, signal.SIGCONT)
        worker.close()


def test_resume_preserves_held_position_and_fills(scenario, tmp_path):  # noqa: F811
    fixture = scenario()
    engine = fixture.e
    positions = engine.executor.snapshot()["positions"]
    fills = engine.store.list(kind="fill", run_id=engine.run_id, limit=None)
    for attempt in range(2):
        worker = PaperWorker(
            str(engine.store.engine.url), engine.config, engine.run_id, True, False, tmp_path
        )
        try:
            ready = worker.start()
            assert ready["positions"] == positions
            if attempt == 0:
                worker.process.terminate()
                worker.process.join(5)
                assert worker.poll()["state"] == "FAILED"
                assert worker.failed.is_set()
        finally:
            worker.close()
        assert engine.store.load_checkpoint(engine.run_id)["positions"] == positions
        assert engine.store.list(kind="fill", run_id=engine.run_id, limit=None) == fills


def test_signal_candidate_does_not_enter_paper_execution(scenario, market, now, monkeypatch):  # noqa: F811
    fixture = scenario(buy=False)
    engine = fixture.e
    engine.signal_only = True
    engine.execute = False

    def forbidden(*args, **kwargs):
        raise AssertionError("Paper work ran in signal engine")

    monkeypatch.setattr(engine.executor, "submit", forbidden)
    monkeypatch.setattr(engine.executor, "reject_submission", forbidden)
    for index in range(10):
        fixture.refresh(now + index * 0.1)
        engine.process(now + index * 0.1, str(index), "orderbook_snapshot", dict(market_ticker=market.ticker))
    assert engine.latest[market.ticker]["decision"] == "TRADE_CANDIDATE"
    assert not engine.executor.orders


def test_worker_replay_matches_inline_results(tmp_path, config, monkeypatch):
    import multiprocessing
    import queue
    import threading
    from dataclasses import replace

    from btc15 import paper_worker
    from btc15.demo import generate
    from btc15.storage import read_events

    config = replace(config)
    rows = list(read_events(generate(tmp_path / "replay.jsonl")))
    inline = Store("sqlite:///" + str(tmp_path / "inline.db"))
    replay = Store("sqlite:///" + str(tmp_path / "replay.db"))
    engine = Engine(inline, config, run_id="inline", record_evaluations=False)
    for row in rows:
        engine.ingest(row)
    inputs, outputs = queue.Queue(), queue.Queue()
    for offset in range(0, len(rows), 256):
        inputs.put(rows[offset : offset + 256])
    inputs.put(None)

    def historical_engine(*args, **kwargs):
        kwargs["clock"] = None  # Deterministic offline replay, not live execution timing.
        return Engine(*args, **kwargs)

    monkeypatch.setattr(paper_worker, "Engine", historical_engine)
    paper_worker._paper_main(
        str(replay.engine.url),
        config,
        "replay",
        False,
        False,
        tmp_path,
        inputs,
        outputs,
        threading.Event(),
        multiprocessing.Array("c", 128),
    )
    for kind, fields in [
        ("fill", ("action", "side", "quantity", "price", "fee")),
        ("trade_result", ("net_pnl", "gross_pnl", "fees")),
    ]:

        def results(store):
            return [{key: r["body"].get(key) for key in fields} for r in store.list(kind=kind, limit=None)]

        assert results(inline)
        assert results(inline) == results(replay)
    assert not replay.load_checkpoint("replay")["positions"]
    inline.engine.dispose()
    replay.engine.dispose()


def test_signal_engine_leaves_historical_settlement_to_paper(config, monkeypatch):
    store = SignalStore()
    try:
        engine = Engine(store, config, execute=False, signal_only=True, record_evaluations=False)

        def forbidden(*args, **kwargs):
            raise AssertionError("Signal engine tried to settle paper history")

        monkeypatch.setattr(engine.executor, "settle", forbidden)
        monkeypatch.setattr(engine, "error", forbidden)
        engine.settle("historical-contract", "yes", time.time())
    finally:
        store.close()
