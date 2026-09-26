import asyncio
import json
import stat
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from btc15.public_export import export_history, publish, read_history
from btc15.public_site import create_app


def test_public_site_has_no_control_or_private_routes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        for path in ("/api/unlock", "/api/control", "/api/stop", "/api/shutdown",
                     "/api/manual/orders", "/api/live/control", "/api/strategy"):
            assert client.get(path).status_code == 404
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                result = client.request(method, path, json={"confirm": True},
                                        headers={"Origin": "http://testserver"})
                assert result.status_code == 405
        for path in ("/docs", "/openapi.json", "/assets/BTC/api/trades", "/.env",
                     "/api/history/..%2F..%2Fcredentials.env"):
            assert client.get(path).status_code == 404
        response = client.get("/")
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/viewer.js").status_code == 200
        assert client.get("/viewer.css").status_code == 200
        assert client.get("/logo.svg").status_code == 200
        assert client.get("/section-logo.svg").status_code == 200


def test_snapshot_missing_stale_and_atomic_refresh(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        missing = client.get("/api/view")
        assert missing.status_code == 503
        assert str(tmp_path) not in missing.text
        publish(tmp_path, "view.json", dict(assets=[], updated_at=time.time()))
        assert client.get("/api/view?url=http://127.0.0.1:8000/api/strategy").json()["stale"] is False
        publish(tmp_path, "view.json", dict(assets=[], updated_at=time.time() - 21))
        assert client.get("/api/view").json()["stale"] is True
        publish(tmp_path, "view.json", dict(assets=[], updated_at=time.time() + 30))
        assert client.get("/api/view").json()["stale"] is True
        publish(tmp_path, "view.json", dict(assets=[], stale=True, updated_at=time.time()))
        assert client.get("/api/view").json()["stale"] is True
        (tmp_path / "view.json").write_text("broken")
        assert client.get("/api/view").status_code == 503


def test_history_is_local_bounded_and_reports_staleness(tmp_path):
    rows = [{"market": str(i)} for i in range(40)]
    publish(tmp_path, "history-BTC.json", dict(rows=rows, total=40, updated_at=time.time()))
    with TestClient(create_app(tmp_path)) as client:
        data = client.get("/api/history/BTC?offset=25&limit=25").json()
        assert data == dict(rows=rows[25:], total=40, offset=25, limit=25, stale=False)
        assert client.get("/api/history/BTC?offset=999999999999").json()["rows"] == []
        for query in ("offset=-1", "limit=101", "limit=0"):
            assert client.get("/api/history/BTC?" + query).status_code == 422
        assert client.get("/api/history/ETH").status_code == 503
        assert client.get("/api/history/UNKNOWN").status_code == 404
        publish(tmp_path, "history-BTC.json", dict(rows=rows, total=40, updated_at=time.time() - 121))
        assert client.get("/api/history/BTC").json()["stale"] is True


def test_publisher_does_not_replace_good_data_with_partial_file(tmp_path):
    path = tmp_path / "view.json"
    publish(tmp_path, path.name, {"good": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    with pytest.raises(ValueError):
        publish(tmp_path, path.name, {"bad": float("nan")})
    assert json.loads(path.read_text()) == {"good": True}
    assert list(tmp_path.iterdir()) == [path]


def test_history_export_sanitizes_all_pages_and_preserves_previous_on_failure(tmp_path):
    calls = []
    failed = False

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path == "/api/fleet":
            return httpx.Response(200, json={"assets": [{"asset": "BTC", "run_id": "private-run"}]})
        assert request.url.path == "/assets/BTC/api/trades"
        assert dict(request.url.params) == dict(run_id="private-run", mode="PAPER", include_open="true",
                                               offset=request.url.params["offset"], limit="500")
        offset = int(request.url.params["offset"])
        if failed and offset:
            return httpx.Response(500, text="secret-upstream-error")
        rows = [dict(market=str(i), timestamp=i, run_id="private-run", body=dict(
            status="CLOSED", net_pnl=1, order_id="secret-order", private_key="never-publish",
        )) for i in range(offset, min(offset + 500, 501))]
        return httpx.Response(200, json=dict(rows=rows, total=501))

    async def run():
        nonlocal failed
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://127.0.0.1:8000") as client:
            await export_history(client, tmp_path)
            before = (tmp_path / "history-BTC.json").read_text()
            data = json.loads(before)
            assert data["total"] == 501
            assert data["rows"][-1]["market"] == "500"
            assert "secret" not in before and "private" not in before
            failed = True
            await export_history(client, tmp_path)
            assert (tmp_path / "history-BTC.json").read_text() == before

    asyncio.run(run())
    assert len(calls) == 6


def test_incomplete_history_is_not_published():
    def handle(request):
        return httpx.Response(200, json=dict(rows=[], total=1))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://127.0.0.1:8000") as client:
            with pytest.raises(ValueError, match="Incomplete"):
                await read_history(client, "BTC", "run")

    asyncio.run(run())
