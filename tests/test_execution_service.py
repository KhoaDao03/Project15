import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_fleet_dashboard import portfolios  # noqa: F401
from test_manual_trading import venue  # noqa: F401

from btc15.config import Settings
from btc15.execution_service import ExecutionClient, create_execution_app
from btc15.fleet import create_fleet_app

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def connect_dashboard(monkeypatch, service):
    def initialize(self, manifest):
        def handle(request):
            result = service.request(
                request.method, str(request.url), content=request.content, headers=dict(request.headers)
            )
            return httpx.Response(result.status_code, content=result.content)

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://localhost")

    monkeypatch.setattr(ExecutionClient, "__init__", initialize)


def test_dashboard_restart_does_not_restart_execution(portfolios, monkeypatch):  # noqa: F811
    manifest, _ = portfolios
    app = create_execution_app(manifest, settings=Settings())
    with TestClient(app, base_url="http://localhost") as service:
        connect_dashboard(monkeypatch, service)
        live = app.state.live
        lock = live.lock_file
        for _ in range(2):
            with TestClient(create_fleet_app(manifest), base_url=ORIGIN) as dashboard:
                status = dashboard.get("/api/live/status").json()
                assert status["running"]
                assert dashboard.get("/api/fleet").json()["live"]["available"]
                assert dashboard.get("/api/manual/status").status_code == 200
            assert live.running and live.lock_file is lock and not lock.closed
        before = live.last_cycle
        deadline = time.monotonic() + 2
        while live.last_cycle <= before:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    assert not live.running


def test_execution_has_exclusive_journal_ownership(portfolios):  # noqa: F811
    manifest, _ = portfolios
    first = create_execution_app(manifest, settings=Settings())
    with TestClient(first):
        with pytest.raises(RuntimeError, match="already owned"):
            with TestClient(create_execution_app(manifest, settings=Settings())):
                pytest.fail("Second execution service started")
        assert first.state.live.running


def test_unavailable_execution_keeps_quotes_but_blocks_shutdown_and_orders(portfolios):  # noqa: F811
    manifest, _ = portfolios
    with TestClient(create_fleet_app(manifest), base_url=ORIGIN) as dashboard:
        result = dashboard.get("/api/fleet").json()
        assert result["live"]["available"] is False
        assert result["assets"][0]["markets"][0]["fresh"]
        dashboard.app.state.shutdown_server = lambda: None
        assert (
            dashboard.post("/api/shutdown", json={"confirm": True}, headers={"Origin": ORIGIN}).status_code
            == 503
        )
        assert dashboard.post("/api/manual/orders", json={}, headers={"Origin": ORIGIN}).status_code == 503
        assert (
            dashboard.post("/api/live/control", json={}, headers={"Origin": "http://evil.test"}).status_code
            == 403
        )


@pytest.mark.anyio
async def test_ambiguous_forwarding_is_never_retried(tmp_path):
    client = ExecutionClient(tmp_path / "manifest.json")
    await client.close()
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("Response lost")

    client.http = httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="http://localhost")
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            await client.request("POST", "/api/manual/orders", content=b'{"client_order_id":"same-id"}')
        assert error.value.status_code == 503
        assert len(calls) == 1 and b"same-id" in calls[0].content
    finally:
        await client.close()


def test_proxy_controls_and_safe_shutdown_are_owned_by_service(portfolios, monkeypatch):  # noqa: F811
    manifest, _ = portfolios
    app = create_execution_app(manifest, settings=Settings())
    with TestClient(app, base_url="http://localhost") as service:
        connect_dashboard(monkeypatch, service)
        calls = []
        from fastapi import HTTPException

        def blocked():
            calls.append("prepare")
            raise HTTPException(409, "Position still managed")

        monkeypatch.setattr(app.state.live, "prepare_shutdown", blocked)
        with TestClient(create_fleet_app(manifest), base_url=ORIGIN) as dashboard:
            dashboard.app.state.shutdown_server = lambda: None
            assert dashboard.post("/api/live/control", json={}, headers={"Origin": ORIGIN}).status_code == 422
            result = dashboard.post("/api/shutdown", json={"confirm": True}, headers={"Origin": ORIGIN})
            assert result.status_code == 409 and calls == ["prepare"]
            assert dashboard.get("/api/shutdown").json()["status"] == "idle"


def test_manual_order_id_survives_execution_restart(portfolios, venue, monkeypatch):  # noqa: F811
    from test_manual_trading import request_body

    from btc15.manual_trading import ManualTrading

    manifest, _ = portfolios
    _, exchange, existing, factory = venue

    async def no_stream(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(ManualTrading, "watch_fills", no_stream)
    payload = request_body()
    for _ in range(2):
        app = create_execution_app(manifest, settings=existing.settings, client_factory=factory)
        with TestClient(app, base_url="http://localhost") as service:
            connect_dashboard(monkeypatch, service)
            with TestClient(create_fleet_app(manifest), base_url=ORIGIN) as dashboard:
                response = dashboard.post("/api/manual/orders", json=payload, headers={"Origin": ORIGIN})
                assert response.status_code == 200
                assert response.json()["state"] == "complete"
                assert response.json()["exchange_order"]["fill_count_fp"] == "2.00"
                assert len(exchange["posts"]) == 1


def test_worker_failure_requests_service_restart_and_blocks_actions(portfolios, monkeypatch):  # noqa: F811
    from btc15.live_automation import LiveAutomation

    manifest, _ = portfolios

    async def failed(self):
        self.running = True
        await asyncio.sleep(0.05)
        raise RuntimeError("worker test failure")

    monkeypatch.setattr(LiveAutomation, "run", failed)
    app = create_execution_app(manifest, settings=Settings())
    exits = []
    app.state.shutdown_server = lambda: exits.append(True)
    with TestClient(app, base_url="http://localhost") as client:
        deadline = time.monotonic() + 2
        while not exits:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert app.state.worker_failed
        assert (
            client.post("/api/manual/orders", json={}, headers={"Origin": "http://localhost"}).status_code
            == 503
        )
