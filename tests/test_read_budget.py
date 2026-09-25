import asyncio
import fcntl
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from btc15 import read_budget
from btc15.api import KalshiClient, read_timings, stop_reads
from btc15.config import Settings
from btc15.read_budget import BOOT_ID, ReadBudget, read_cost, shared_budget_path


def frozen(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(read_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_idle_credit_bursts_without_spacing_then_refills(monkeypatch):
    clock = frozen(monkeypatch)
    budget = ReadBudget()
    assert budget.delay(10) == 0.2  # Cold start does not invent server credit.
    clock[0] += 3
    assert [budget.delay(10) for _ in range(57)] == [0] * 57
    assert budget.delay(10) == 0.05
    # Ordinary traffic cannot consume the three-read stop reserve.
    assert [budget.delay(10, stop=True) for _ in range(3)] == [0] * 3
    assert budget.delay(10, stop=True) == 0.05
    clock[0] += 1
    assert budget.delay(50) == 0
    assert budget.state["tokens"] == 150


def test_budget_caps_at_capacity_and_shares_restarts(tmp_path, monkeypatch):
    clock = frozen(monkeypatch)
    path = tmp_path / "budget.json"
    first = ReadBudget(path)
    assert first.delay(10) == 0.2
    clock[0] += 100
    assert first.delay(50) == 0
    second = ReadBudget(path)
    assert second.delay(10) == 0
    assert json.loads(path.read_text())["tokens"] == 540
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("bad", ["not-json", "{}", "[]", "null", '{"tokens": NaN}'])
def test_corrupt_state_restarts_empty(tmp_path, monkeypatch, bad):
    frozen(monkeypatch)
    path = tmp_path / "budget.json"
    path.write_text(bad)
    path.chmod(0o600)
    assert ReadBudget(path).delay(10) == 0.2
    assert json.loads(path.read_text())["tokens"] == 0


def test_reboot_discards_old_credit(tmp_path, monkeypatch):
    frozen(monkeypatch)
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(dict(boot="previous-boot", tokens=600, at=1, blocked_until=0)))
    path.chmod(0o600)
    assert ReadBudget(path).delay(10) == 0.2


def _spend_in_process(path):
    read_budget.time = SimpleNamespace(monotonic=lambda: 100.0)
    budget = ReadBudget(path)
    success = 0
    for _ in range(40):
        delay = budget.delay(10)
        while delay == 0.005:  # Only retry lock contention, not exhausted credit.
            time.sleep(0.001)
            delay = budget.delay(10)
        success += delay == 0
    return success


def test_processes_share_one_bucket(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(dict(boot=BOOT_ID, tokens=600, at=100, blocked_until=0)))
    path.chmod(0o600)
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        totals = list(pool.map(_spend_in_process, [path] * 3))
    assert sum(totals) == 57
    assert json.loads(path.read_text())["tokens"] == 30


@pytest.mark.anyio
async def test_contended_file_lock_yields_and_cancellation_spends_nothing(tmp_path):
    path = tmp_path / "budget.json"
    budget = ReadBudget(path)
    budget.delay(10)
    before = path.read_bytes()
    with path.open() as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = asyncio.create_task(budget.acquire(10))
        await asyncio.sleep(0.02)
        assert not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert path.read_bytes() == before
    assert budget.delay(10) > 0


@pytest.mark.anyio
async def test_rate_limit_backoff_is_shared_including_stops(tmp_path, monkeypatch):
    clock = frozen(monkeypatch)
    path = tmp_path / "budget.json"
    one, two = ReadBudget(path), ReadBudget(path)
    one.delay(10)
    clock[0] += 3
    assert one.delay(10) == 0
    await one.penalize(1)
    assert two.delay(10, stop=True) == 1
    clock[0] += 0.5
    assert two.delay(10, stop=True) == 0.5
    clock[0] += 0.5
    assert two.delay(10, stop=True) == 0


@pytest.mark.parametrize(
    "path,cost",
    [
        ("series/X", 10),
        ("/cfbenchmarks", 50),
        ("cfbenchmarks/foo?x=1", 50),
        ("portfolio/orders", 10),
        ("portfolio/orders/id?x=1", 2),
    ],
)
def test_endpoint_costs(path, cost):
    assert read_cost(path) == cost


def test_shared_identity_ignores_key_and_api_path_but_separates_hosts():
    assert shared_budget_path("https://external-api.kalshi.com/trade-api/v2") == shared_budget_path(
        "https://external-api.kalshi.com/other"
    )
    assert shared_budget_path("https://external-api.kalshi.com") != shared_budget_path(
        "https://demo-api.kalshi.co"
    )


@pytest.mark.anyio
async def test_warm_client_sends_burst_and_accounts_for_every_retry(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": ".1"})
        return httpx.Response(200, json={})

    client = KalshiClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url=Settings.rest_url + "/", transport=httpx.MockTransport(handler))
    client.read_budget.state.update(tokens=600)
    metrics = []
    token = read_timings.set(metrics)
    try:
        await client.get("markets/example")
        assert metrics[0]["attempts"] == 2
        assert metrics[0]["retry_wait_ms"] >= 90
        assert metrics[0]["http_status"] == 200
        # A full local bucket allows four requests without artificial spacing.
        client.read_budget.state.update(tokens=600, at=time.monotonic(), blocked_until=0)

        async def no_sleep(_):
            raise AssertionError("Warm bucket should not sleep")

        monkeypatch.setattr(read_budget.asyncio, "sleep", no_sleep)
        await asyncio.gather(*(client.get("markets/example") for _ in range(4)))
    finally:
        read_timings.reset(token)
        await client.close()
    assert len(calls) == 6


@pytest.mark.anyio
async def test_stop_context_uses_reserved_credit():
    client = KalshiClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        base_url=Settings.rest_url + "/",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    client.read_budget.state.update(tokens=30)
    token = stop_reads.set(True)
    try:
        await client.get("markets/stop")
        assert client.read_budget.state["tokens"] < 30
    finally:
        stop_reads.reset(token)
        await client.close()


def test_shared_file_permissions_fail_closed(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="private"):
        ReadBudget(path).delay(10)


@pytest.mark.anyio
async def test_credentialed_clients_share_origin_budget(tmp_path, monkeypatch):
    import btc15.api as api

    path = tmp_path / "shared.json"
    monkeypatch.setattr(api, "shared_budget_path", lambda url: path)
    a = KalshiClient(Settings(api_key_id="one", data_dir="collector"))
    b = KalshiClient(Settings(api_key_id="two", data_dir="executor"))
    try:
        assert a.read_budget.path == b.read_budget.path == path
        assert a.read_budget.delay(10) > 0
        state = json.loads(path.read_text())
        state.update(tokens=100, at=time.monotonic())
        path.write_text(json.dumps(state))
        assert b.read_budget.delay(10) == 0
        assert json.loads(path.read_text())["tokens"] < 91
        assert a.read_budget.delay(10) == 0
        assert json.loads(path.read_text())["tokens"] < 82
    finally:
        await a.close()
        await b.close()
