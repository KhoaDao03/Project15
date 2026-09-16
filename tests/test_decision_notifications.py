import asyncio
import json
import sqlite3
import time

import httpx
import pytest

from btc15.api import KalshiClient, read_timings
from btc15.config import Settings
from btc15.decision_notifications import DecisionListener, notification_path, notify_decision


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_notification_wakes_listener_and_missing_listener_is_safe(tmp_path):
    database = str(tmp_path / "paper.db")
    notify_decision(database)
    wake = asyncio.Event()
    listener = DecisionListener([database], wake)
    listener.start()
    try:
        assert listener.sockets
        await asyncio.to_thread(notify_decision, database)
        await asyncio.wait_for(wake.wait(), 0.2)
        wake.clear()
        notify_decision(str(tmp_path / "other.db"))
        await asyncio.sleep(0.01)
        assert not wake.is_set()
    finally:
        listener.close()
    assert not notification_path(database).exists()
    notify_decision(database)


@pytest.mark.anyio
async def test_read_measurements_include_throttling_network_and_failures():
    async def handler(request):
        await asyncio.sleep(0.01)
        if request.url.path.endswith("/failed"):
            raise httpx.ReadTimeout("test")
        return httpx.Response(200, json={})

    client = KalshiClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url=Settings.rest_url + "/", transport=httpx.MockTransport(handler))
    measurements = []
    token = read_timings.set(measurements)
    try:
        await asyncio.gather(client.get("series/test"), client.get("markets/test"))
        with pytest.raises(httpx.ReadTimeout):
            await client.get("failed")
    finally:
        read_timings.reset(token)
        await client.close()
    assert len(measurements) == 3
    assert max(m["rate_wait_ms"] for m in measurements) >= 100
    assert all(m["network_ms"] >= 5 and m["attempts"] == 1 for m in measurements)
    assert measurements[-1]["error"] == "ReadTimeout"
    assert read_timings.get() is None
    assert not any("headers" in m or "params" in m for m in measurements)


@pytest.mark.parametrize("separate_paper", [False, True])
def test_collector_publishes_and_notifies_before_next_status_tick(
    store, config, tmp_path, raw, series, monkeypatch, separate_paper
):
    from test_collection import fake_client

    from btc15 import runner

    fake_client(monkeypatch, raw, series)

    class Socket:
        count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def recv(self):
            await asyncio.sleep(0.04)
            self.count += 1
            return json.dumps(dict(type="ticker", msg=dict(test_index=self.count)))

    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **k: Socket())
    original = runner.Engine.ingest
    published = []

    def ingest(self, row):
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        if payload.get("type") == "ticker":
            ticker = raw["ticker"]
            self.latest[ticker] = dict(
                timestamp=row["received"], ticker=ticker, decision="TRADE_CANDIDATE", reasons=[]
            )
            self._last_op[ticker] = "test"
            return True
        return original(self, row)

    monkeypatch.setattr(runner.Engine, "ingest", ingest)

    def notified(database):
        # A separate reader must already see the committed decision.
        with sqlite3.connect("file:" + database + "?mode=ro", uri=True) as reader:
            record = json.loads(
                reader.execute("select body from market_display where key like 'evaluation:%'").fetchone()[0]
            )
        published.append((time.monotonic(), record["body"]["timestamp"]))

    monkeypatch.setattr(runner, "notify_decision", notified)
    asyncio.run(
        runner.collect(
            Settings(data_dir=str(tmp_path)),
            config,
            store,
            paper=True,
            duration=0.35,
            separate_paper=separate_paper,
        )
    )
    assert len(published) >= 2
    assert published[1][0] - published[0][0] < 1
    assert published[1][1] > published[0][1]
