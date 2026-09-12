import asyncio
import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from test_collection import fake_client

from btc15 import runner
from btc15.collector_recovery import CollectorRecovery
from btc15.config import Settings
from btc15.domain import D
from btc15.storage import read_events


@pytest.mark.parametrize("failed_first_handshake", [False, True])
def test_heartbeat_and_metadata_during_handshake_allow_fresh_recovery(
    store, config, tmp_path, raw, series, monkeypatch, failed_first_handshake
):
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
    original_wait = asyncio.wait_for

    async def quick_refresh(future, timeout):
        return await original_wait(future, 0.05 if timeout == 15 else timeout)

    monkeypatch.setattr(runner.asyncio, "wait_for", quick_refresh)
    requests, ready, attempts, opened, engines, connected_integrity = [], [], [], [], [], []
    original_ingest = runner.Engine.ingest

    async def run():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        ready_events = asyncio.Queue()
        handshake = None

        class Recovery(CollectorRecovery):
            def request(self, reason, now):
                super().request(reason, now)
                if self.reason != reason or self.since != now:
                    return  # An existing drain already owns this request.
                requests.append(reason)
                if reason == "DATA_INTEGRITY_FAILURE":
                    # End a broken run promptly; the assertions below reject it.
                    loop.call_soon_threadsafe(stop.set)

            def check(self, *args, **kwargs):
                super().check(*args, **kwargs)
                if self.phase == "READY" and self.connection not in ready:
                    ready.append(self.connection)
                    loop.call_soon_threadsafe(ready_events.put_nowait, self.connection)

        def ingest(engine, row):
            if not engines:
                engines.append(engine)
            valid = original_ingest(engine, row)
            kind = json.loads(row["payload"])["type"]
            waiting = handshake
            if waiting and row["received"] >= waiting["started"] and kind in ("heartbeat", "metadata"):
                loop.call_soon_threadsafe(waiting[kind].set)
            if kind == "connected":
                connected_integrity.append(engine._collector_integrity_failed)
            return valid

        class Socket:
            async def __aenter__(self):
                nonlocal handshake
                attempts.append(self)
                handshake = dict(started=time.time(), heartbeat=asyncio.Event(), metadata=asyncio.Event())
                try:
                    # Complete setup only after both independent producers have
                    # emitted and processed frames during this exact handshake.
                    await asyncio.gather(handshake["heartbeat"].wait(), handshake["metadata"].wait())
                    if failed_first_handshake and len(attempts) == 1:
                        raise OSError("controlled handshake failure")
                finally:
                    handshake = None
                opened.append(self)
                self.generation = len(opened)
                self.count = 0
                return self

            async def __aexit__(self, *args):
                pass

            async def send(self, message):
                pass

            async def recv(self):
                await asyncio.sleep(0)
                self.count += 1
                if self.count == 1:
                    payload = dict(
                        type="orderbook_snapshot",
                        sid=3,
                        seq=1,
                        msg=dict(
                            market_ticker=raw["ticker"],
                            yes_dollars_fp=[[".80", str(self.generation * 10)]],
                            no_dollars_fp=[[".90", "10"]],
                        ),
                    )
                elif self.count == 2:
                    payload = dict(
                        type="orderbook_delta",
                        sid=3,
                        seq=2,
                        msg=dict(market_ticker=raw["ticker"], side="yes", price_dollars=".80", delta_fp="2"),
                    )
                elif self.count == 3:
                    payload = dict(
                        type="cfbenchmarks_value",
                        msg=dict(
                            index_id="BRTI",
                            data=json.dumps(
                                dict(type="value", id="BRTI", time=time.time() * 1000, value="79200")
                            ),
                        ),
                    )
                else:
                    await ready_events.get()
                    if self.generation == 1:
                        raise OSError("controlled disconnect after readiness")
                    stop.set()
                    await asyncio.Future()
                return json.dumps(payload)

        monkeypatch.setattr(runner, "CollectorRecovery", Recovery)
        monkeypatch.setattr(runner.Engine, "ingest", ingest)
        monkeypatch.setattr(runner.websockets, "connect", lambda *a, **k: Socket())
        async with asyncio.timeout(12):
            await runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="handshake-recovery",
                stop_event=stop,
            )

    asyncio.run(run())
    assert "DATA_INTEGRITY_FAILURE" not in requests
    assert len(attempts) == 2 + int(failed_first_handshake)
    assert len(opened) == len(ready) == 2
    assert connected_integrity == [False, False]
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl.gz"))))
    connections = [r["connection_id"] for r in rows if r["payload"]["type"] == "connected"]
    assert connections == ready
    assert len({r["connection_id"] for r in rows}) == 3  # Bootstrap plus two established connections.
    for connection in connections:
        generation = [r for r in rows if r["connection_id"] == connection]
        assert generation[0]["payload"]["type"] == "connected"
        assert generation[0]["collector_entries_blocked"] is True
        book_rows = [
            r for r in generation if r["payload"]["type"] in ("orderbook_snapshot", "orderbook_delta")
        ]
        assert [r["payload"]["seq"] for r in book_rows] == [1, 2]
    assert engines[0].books[raw["ticker"]].yes[D(".80")] == 22
    assert not [r for r in store.list(kind="health") if r["body"]["code"] in ("SEQUENCE_GAP", "INVALID_DATA")]
    assert not store.list(kind="fill")
    assert not (tmp_path / "stop-confirmation-shadow.db").exists()
    assert store.list(kind="shutdown_complete")
    store.acquire("collector", "after-handshake-test")
    store.release("collector", "after-handshake-test")
