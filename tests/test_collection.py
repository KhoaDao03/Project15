import asyncio
import json
import time

import pytest

from btc15 import runner
from btc15.config import Settings
from btc15.storage import read_events


def fake_client(monkeypatch, raw, series):
    class Client:
        last_clock_skew = 0

        def __init__(self, settings):
            pass

        def headers(self, *args):
            return {}

        async def discover(self):
            return series, [raw]

        async def pages(self, *args):
            for item in []:
                yield item

        async def get(self, path, *args):
            if path.startswith("markets/"):
                return {"market": raw}
            return {"trading_active": True, "series_fee_change_arr": []}

        async def close(self):
            pass

    monkeypatch.setattr(runner, "KalshiClient", Client)


def fake_socket(monkeypatch, payloads):
    class Socket:
        def __init__(self):
            self.rows = iter(payloads)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def recv(self):
            await asyncio.sleep(0)
            try:
                return json.dumps(next(self.rows))
            except StopIteration:
                await asyncio.sleep(3600)

    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **kw: Socket())


def test_receipt_remains_responsive_during_slow_processing(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type="ticker", msg={"test_index": i}) for i in range(200)])
    original = runner.Engine.ingest

    def slow(self, row):
        time.sleep(0.005)
        return original(self, row)

    monkeypatch.setattr(runner.Engine, "ingest", slow)
    run = asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=0.15))
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    ticks = [r for r in rows if r["payload"]["type"] == "ticker"]
    assert [r["payload"]["msg"]["test_index"] for r in ticks] == list(range(200))
    # Serial processing takes >1 s; receipt is independently timestamped.
    assert (ticks[-1]["monotonic_ns"] - ticks[0]["monotonic_ns"]) / 1e9 < 0.7
    assert store.list(kind="status", run_id=run)[-1]["body"]["connected"] is False
    store.acquire("collector", "test-cleanup")
    store.release("collector", "test-cleanup")


def test_subscription_denial_preserved_and_lease_released(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type="error", msg={"code": 9, "msg": "denied"})])
    with pytest.raises(ExceptionGroup):
        asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=1))
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    assert any(r["payload"]["type"] == "error" for r in rows)
    store.acquire("collector", "test-cleanup")
    store.release("collector", "test-cleanup")


def test_completed_batch_is_not_reported_as_pending(store, config, tmp_path, raw, series, monkeypatch):
    from btc15.collector_recovery import CollectorRecovery

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type="ticker", msg={"test_index": i}) for i in range(100)])
    original_ingest = runner.Engine.ingest
    original_check = CollectorRecovery.check
    drained_depths = []

    def slow(engine, row):
        time.sleep(0.002)
        return original_ingest(engine, row)

    def check(recovery, engine, now, lag, depth, capacity, connected, stopping=False):
        if stopping and depth == 0:
            drained_depths.append(recovery.depth)
        return original_check(recovery, engine, now, lag, depth, capacity, connected, stopping)

    monkeypatch.setattr(runner.Engine, "ingest", slow)
    monkeypatch.setattr(CollectorRecovery, "check", check)
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=0.1))
    assert drained_depths and all(depth == 0 for depth in drained_depths)


def test_disk_failure_stops_processing_and_releases_resources(
    store, config, tmp_path, raw, series, monkeypatch
):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type="ticker", msg={})])
    processed = []
    monkeypatch.setattr(runner.Engine, "ingest", lambda self, row: processed.append(row))

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(runner.RawRecorder, "append_rows", fail)
    with pytest.raises((ExceptionGroup, OSError)):
        asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=1))
    assert not processed
    store.acquire("collector", "test-cleanup")
    store.release("collector", "test-cleanup")


def test_rollover_updates_market_subscriptions_without_reconnecting(
    store, config, tmp_path, raw, series, monkeypatch
):
    fake_client(monkeypatch, raw, series)
    initial_client = runner.KalshiClient
    calls = 0
    future = {**raw, "ticker": "KXBTC15M-NEXT-15", "event_ticker": "KXBTC15M-NEXT"}

    class Client(initial_client):
        async def discover(self):
            nonlocal calls
            calls += 1
            return series, [raw] if calls == 1 else [future]

    monkeypatch.setattr(runner, "KalshiClient", Client)
    original_wait = asyncio.wait_for

    async def quick_refresh(future, timeout):
        return await original_wait(future, 0.05 if timeout == 15 else timeout)

    monkeypatch.setattr(runner.asyncio, "wait_for", quick_refresh)
    sent = []
    connections = []

    class Socket:
        async def __aenter__(self):
            connections.append(self)
            self.acks = iter(
                [
                    dict(type="subscribed", msg=dict(channel=c, sid=i))
                    for i, c in enumerate(("orderbook_delta", "trade", "ticker"), 3)
                ]
            )
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, msg):
            sent.append(json.loads(msg))

        async def recv(self):
            await asyncio.sleep(0.01)
            return json.dumps(next(self.acks, dict(type="heartbeat", msg={})))

    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **kw: Socket())
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=0.15))
    assert calls >= 2 and len(connections) == 1
    updates = [x for x in sent if x["cmd"] == "update_subscription"]
    assert [(x["params"]["action"], x["params"]["market_tickers"]) for x in updates] == [
        ("add_markets", [future["ticker"]])
    ] * 3 + [("delete_markets", [raw["ticker"]])] * 3
    assert [x["params"]["sid"] for x in updates] == [3, 4, 5, 3, 4, 5]


def test_transport_loss_reconnects_and_rebuilds_book(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    connections = []
    engines = []
    original_engine = runner.Engine

    def engine(*args, **kwargs):
        obj = original_engine(*args, **kwargs)
        engines.append(obj)
        return obj

    monkeypatch.setattr(runner, "Engine", engine)

    class Socket:
        async def __aenter__(self):
            connections.append(self)
            self.index = len(connections)
            self.sent_snapshot = False
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def recv(self):
            await asyncio.sleep(0.01)
            if not self.sent_snapshot:
                self.sent_snapshot = True
                return json.dumps(
                    dict(
                        type="orderbook_snapshot",
                        sid=3,
                        seq=1,
                        msg=dict(
                            market_ticker=raw["ticker"],
                            yes_dollars_fp=[[".80", str(self.index)]],
                            no_dollars_fp=[[".85", "2"]],
                        ),
                    )
                )
            if self.index == 1:
                raise OSError("controlled transport loss")
            await asyncio.sleep(3600)

    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **kw: Socket())
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=1.6))
    assert len(connections) == 2
    from btc15.domain import D

    assert engines[0].books[raw["ticker"]].yes[D(".80")] == 2
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    snapshots = [r for r in rows if r["payload"]["type"] == "orderbook_snapshot"]
    assert len(snapshots) == 2
    assert snapshots[0]["connection_id"] != snapshots[1]["connection_id"]
    assert not [r for r in store.list(kind="health") if r["body"]["code"] in ("SEQUENCE_GAP", "INVALID_DATA")]


def test_quote_processing_trace_distinguishes_republication(store, config, tmp_path, raw, series, monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from btc15.domain import timestamp

    offset = timestamp(raw['close_time']) - 300 - time.time()
    monkeypatch.setattr(runner, 'time', SimpleNamespace(
        time=lambda: time.time() + offset, monotonic=time.monotonic, monotonic_ns=time.monotonic_ns))
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [dict(type='orderbook_snapshot', sid=3, seq=1, msg=dict(
        market_ticker=raw['ticker'], yes_dollars_fp=[['.80', '2']], no_dollars_fp=[['.85', '2']]))])
    published = []
    publish = store.publish_market_display
    def capture(body, key='current'):
        if key == 'current':
            published.append(deepcopy(body))
        return publish(body, key)
    monkeypatch.setattr(store, 'publish_market_display', capture)
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=.3))
    quotes = [(s['published_at'], m) for s in published for m in s.get('markets', [])
              if m.get('book_first_published_at') is not None]
    assert len(quotes) >= 2
    first = quotes[0][1]
    assert first['book_received'] <= first['book_processing_started_at'] <= first['book_processed_at'] <= first['book_first_published_at']
    assert all(m['book_first_published_at'] == first['book_first_published_at'] for _, m in quotes)
    assert quotes[-1][0] > first['book_first_published_at']
