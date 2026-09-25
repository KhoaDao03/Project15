import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from btc15 import public_site as site
from btc15.public_export import publish


def seed(directory, asset="BTC", count=30, timestamp=None):
    publish(directory, f"history-{asset}.json", dict(
        rows=[dict(market=str(i)) for i in range(count)], total=count,
        updated_at=time.time() if timestamp is None else timestamp,
    ))


def test_rate_limit_refills_and_forwarded_addresses_cannot_bypass(tmp_path, monkeypatch):
    seed(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(site, "monotonic", lambda: clock[0])

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=site.create_app(tmp_path)),
                                     base_url="http://test") as client:
            for i in range(10):
                assert (await client.get("/api/history/BTC", headers={"X-Forwarded-For": f"192.0.2.{i}"})).status_code == 200
            limited = await client.get("/api/history/BTC", headers={"X-Forwarded-For": "198.51.100.1"})
            assert limited.status_code == 429 and limited.headers["retry-after"] == "1"
            clock[0] += 1
            for _ in range(5):
                assert (await client.get("/api/history/BTC")).status_code == 200
            assert (await client.get("/api/history/BTC")).status_code == 429
            clock[0] += 100
            for _ in range(10):
                assert (await client.get("/api/history/BTC")).status_code == 200
            assert (await client.get("/api/history/BTC")).status_code == 429

    asyncio.run(run())


def test_cache_reuses_decode_refreshes_atomic_files_and_rechecks_age(tmp_path, monkeypatch):
    seed(tmp_path)
    decoded = []
    now = [time.time()]
    original = json.loads

    def decode(raw):
        decoded.append(len(raw))
        return original(raw)

    monkeypatch.setattr(site, "json", SimpleNamespace(loads=decode))
    monkeypatch.setattr(site, "time", SimpleNamespace(time=lambda: now[0]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=site.create_app(tmp_path)),
                                     base_url="http://test") as client:
            first = (await client.get("/api/history/BTC?offset=0&limit=25")).json()
            second = (await client.get("/api/history/BTC?offset=25&limit=25")).json()
            assert len(first["rows"]) == 25 and len(second["rows"]) == 5 and len(decoded) == 1
            now[0] += 121
            assert (await client.get("/api/history/BTC")).json()["stale"]
            assert len(decoded) == 1
            seed(tmp_path, count=40, timestamp=now[0])
            changed = (await client.get("/api/history/BTC")).json()
            assert changed["total"] == 40 and not changed["stale"] and len(decoded) == 2
            for asset in ("ETH", "SOL"):
                seed(tmp_path, asset)
                assert (await client.get("/api/history/" + asset)).status_code == 200
            assert (await client.get("/api/history/BTC")).status_code == 200
            assert len(decoded) == 5  # Third asset evicted BTC: cache cannot grow per visitor/query.

    asyncio.run(run())


def test_oversized_corrupt_or_incomplete_exports_fail_without_private_details(tmp_path):
    seed(tmp_path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=site.create_app(tmp_path)),
                                     base_url="http://test") as client:
            assert (await client.get("/api/history/BTC")).status_code == 200
            with (tmp_path / "history-BTC.json").open("wb") as output:
                output.truncate(site.HISTORY_MAX_BYTES + 1)
            for content in (None, b"secret-private-error", b'{"rows":[],"total":10,"updated_at":1}'):
                if content is not None:
                    (tmp_path / "history-BTC.json").write_bytes(content)
                response = await client.get("/api/history/BTC")
                assert response.status_code == 503
                assert str(tmp_path) not in response.text and "secret" not in response.text
            seed(tmp_path)
            assert (await client.get("/api/history/BTC")).status_code == 200

    asyncio.run(run())


def test_concurrency_rejects_without_queue_even_after_client_cancellation(tmp_path, monkeypatch):
    seed(tmp_path)
    publish(tmp_path, "view.json", dict(assets=[], updated_at=time.time()))
    entered, release = threading.Event(), threading.Event()
    original = Path.open

    def blocked(path, *args, **kwargs):
        if path.name == "history-BTC.json":
            entered.set()
            assert release.wait(5), "Test failed to release reader"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=site.create_app(tmp_path)),
                                     base_url="http://test") as client:
            first = asyncio.create_task(client.get("/api/history/BTC"))
            assert await asyncio.to_thread(entered.wait, 2)
            second = asyncio.create_task(client.get("/api/history/BTC"))
            await asyncio.sleep(.05)
            try:
                assert (await client.get("/api/history/BTC")).status_code == 429
                assert (await client.get("/api/view")).status_code == 200
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert (await client.get("/api/history/BTC")).status_code == 429
            finally:
                release.set()
                await asyncio.gather(first, second, return_exceptions=True)
            await asyncio.sleep(.02)
            assert (await client.get("/api/history/BTC")).status_code == 200

    asyncio.run(run())
