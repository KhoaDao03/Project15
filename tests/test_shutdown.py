import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_collection import fake_client, fake_socket
from test_execution import decision, ready

from btc15 import runner
from btc15.config import Settings
from btc15.dashboard import create_app
from btc15.shutdown import finish_shutdown


def test_cancel_remainder_preserves_position(store, market, book, now, config):
    executor = ready(store, market, now, config)
    order = executor.submit(market, book, decision(), "op", now, True)
    executor.trade(
        market,
        dict(
            trade_id="partial",
            ts_ms=(now + 1) * 1000,
            taker_outcome_side="no",
            yes_price_dollars=str(order.limit),
            count_fp="1.25",
        ),
        now + 1,
    )
    position = executor.snapshot()["positions"]
    member = SimpleNamespace(execute=True, entries_active=True, executor=executor)
    runner.stop_entries(SimpleNamespace(engines=[member]), now + 2)
    assert not member.execute and not member.entries_active
    assert not any(order.active for order in executor.orders.values())
    assert executor.snapshot()["positions"] == position
    assert executor.positions and not executor.risk.halted
    assert store.load_checkpoint("run")["positions"] == position


@pytest.mark.parametrize("multi_model", [False, True])
def test_request_stops_runner_and_acknowledges_after_checkpoint(
    store, config, tmp_path, raw, series, monkeypatch, multi_model
):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])

    if multi_model:
        from btc15.models import activate, register
        from btc15.strategies.momentum import Momentum, volatility_model

        for model in [Momentum(), volatility_model()]:
            activate(store, register(store, model), True)

    async def run():
        task = asyncio.create_task(
            runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="shutdown-test",
                multi_model=multi_model,
            )
        )
        for _ in range(100):
            if store.writer_owner():
                break
            await asyncio.sleep(0.01)
        owner = store.writer_owner()
        assert owner
        store.add("shutdown_request", {}, owner, "PAPER", time.time())
        await asyncio.wait_for(task, 5)
        assert store.writer_owner() is None
        runs = store.list("shutdown_complete", owner)[0]["body"]["runs"]
        assert len(runs) == (3 if multi_model else 1)
        assert all(store.load_checkpoint(run_id) is not None for run_id in runs)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "unacknowledged", "replacement"])
def test_failure_keeps_dashboard_open(store, failure):
    store.acquire("collector", "owner")
    if failure != "timeout":
        store.release("collector", "owner")
    if failure == "replacement":
        store.acquire("collector", "new-owner")
    state, exits = {}, []
    asyncio.run(finish_shutdown(store, "owner", state, lambda: exits.append(True), timeout=0, close_delay=0))
    assert state["status"] == "failed" and not exits


def test_ack_and_lease_release_required_before_exit(store):
    async def run():
        store.acquire("collector", "owner")
        state, exits = {}, []
        task = asyncio.create_task(
            finish_shutdown(store, "owner", state, lambda: exits.append(True), close_delay=0)
        )
        await asyncio.sleep(0.03)
        assert not exits
        with store.transaction():
            store.add("shutdown_complete", dict(open_positions=2), "owner", "PAPER", time.time())
            store.release("collector", "owner")
        await task
        assert exits == [True] and state["open_positions"] == 2

    asyncio.run(run())


def test_shutdown_requires_local_origin_confirmation_and_is_idempotent(store, tmp_path):
    app = create_app(store, settings=Settings(data_dir=str(tmp_path)))
    app.state.shutdown_server = lambda: None
    store.acquire("collector", "owner")
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        assert client.get("/api/shutdown").json()["status"] == "idle"
        assert client.post("/api/shutdown", json={"confirm": True}).status_code == 403
        assert (
            client.post(
                "/api/shutdown", json={"confirm": True}, headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )
        headers = {"Origin": "http://127.0.0.1:8001"}
        for body in [{}, {"confirm": False}, {"confirm": 1}, [], None]:
            assert (
                client.post(
                    "/api/shutdown", json=body, headers={**headers, "Content-Type": "application/json"}
                ).status_code
                == 422
            )
        for _ in range(2):
            assert client.post("/api/shutdown", json={"confirm": True}, headers=headers).status_code == 202
        assert len(store.list("shutdown_request", "owner")) == 1


def test_flush_failure_never_acknowledges_clean_shutdown(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    close = runner.RawRecorder.close

    def fail_close(self):
        close(self)
        raise OSError("flush failed")

    monkeypatch.setattr(runner.RawRecorder, "close", fail_close)
    with pytest.raises(OSError, match="flush failed"):
        asyncio.run(
            runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="failed-flush",
                record_all=True,
                duration=0.1,
            )
        )
    assert not store.list("shutdown_complete")
