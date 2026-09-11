import asyncio
from types import SimpleNamespace

import pytest
from test_collection import fake_client, fake_socket

from btc15 import runner
from btc15.config import Settings
from btc15.operation import health
from btc15.storage import read_events


def test_managed_clean_restart_preserves_run(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    settings = Settings(data_dir=str(tmp_path))
    for _ in range(2):
        assert (
            asyncio.run(
                runner.collect(settings, config, store, paper=True, duration=0.1, managed_run="managed")
            )
            == "managed"
        )
    assert len(store.list(kind="run", run_id="managed")) == 1
    assert len(store.list(kind="resume", run_id="managed")) == 1
    assert store.load_checkpoint("managed") is not None
    assert len(list((tmp_path / "raw").glob("*.jsonl.gz"))) == 2
    assert not list((tmp_path / "raw").glob("*.parquet"))
    assert len(store.list(kind="raw_source")) == 2
    assert (
        store.list(kind="status", run_id="managed")[-1]["body"]["recording"] == "trades_with_compact_inputs"
    )
    assert not store.list(kind="fill")


def test_stop_drains_and_releases_writer(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)

    async def run():
        stop = asyncio.Event()

        def payloads():
            yield from (dict(type="ticker", msg={}) for _ in range(50))
            # The next recv begins only after the previous frame was enqueued.
            # Stop after delivery, not after a machine-speed-dependent 150 ms.
            stop.set()

        fake_socket(monkeypatch, payloads())
        async with asyncio.timeout(10):
            return await runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="stopped",
                record_all=True,
                stop_event=stop,
            )

    asyncio.run(run())
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    assert sum(r["payload"]["type"] == "ticker" for r in rows) == 50
    assert rows[-1]["payload"] == dict(type="disconnect", msg={"reason": "shutdown"})
    assert not store.list(kind="status", newest_first=True, limit=1)[0]["body"]["connected"]
    store.acquire("collector", "after-stop")
    store.release("collector", "after-stop")


def test_low_disk_fails_closed(store, config, tmp_path, raw, series, monkeypatch):
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        asyncio.run(
            runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="disk",
                min_free_bytes=100,
            )
        )
    assert not store.list(kind="fill")
    assert store.load_checkpoint("disk")
    store.acquire("collector", "after-disk")
    store.release("collector", "after-disk")


def test_managed_run_does_not_override_crash_lease(store, config, tmp_path):
    store.acquire("collector", "crashed-writer")
    # Lease acquisition precedes engine creation; use a credential-free client stub.
    from test_collection import fake_client

    with pytest.MonkeyPatch.context() as mp:
        fake_client(mp, {}, {})
        with pytest.raises(RuntimeError, match="writer owns"):
            asyncio.run(
                runner.collect(
                    Settings(data_dir=str(tmp_path)), config, store, paper=True, managed_run="blocked"
                )
            )
    assert not store.list(kind="run")
    with pytest.raises(RuntimeError, match="writer owns"):
        store.acquire("collector", "other")


def test_health_uses_latest_status_and_rejects_staleness(store):
    assert health(store, "service", now=100)["reasons"] == ["NO_STATUS"]
    body = dict(
        connected=True,
        clock_ok=True,
        paper_execution=True,
        exchange_open=True,
        halted=False,
        reference_age=0.5,
        processing_lag=0.01,
    )
    store.add("status", {**body, "connected": False}, "service", "PAPER", 90)
    store.add("status", body, "service", "PAPER", 100)
    assert health(store, "service", now=101)["healthy"]
    assert health(store, "service", now=106)["reasons"] == ["STALE_STATUS"]
    store.add("status", {**body, "halted": True}, "service", "PAPER", 107)
    assert health(store, "service", now=108)["reasons"] == ["HALTED"]


@pytest.mark.parametrize("initialization_failure", [False, True])
def test_managed_shadow_is_opt_in_and_failure_does_not_stop_primary(
    store, config, tmp_path, raw, series, monkeypatch, initialization_failure
):
    from btc15 import stop_shadow

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    if initialization_failure:

        def fail(*args):
            raise OSError("isolated shadow ledger unavailable")

        monkeypatch.setattr(stop_shadow, "StopShadow", fail)
    run = asyncio.run(
        runner.collect(
            Settings(data_dir=str(tmp_path)),
            config,
            store,
            paper=True,
            duration=0.1,
            managed_run="shadow-enabled",
            stop_confirmation_shadow=True,
        )
    )
    assert run == "shadow-enabled"
    statuses = store.list(kind="stop_shadow_status")
    assert statuses[0]["body"]["status"] == ("FAILED" if initialization_failure else "STARTED")
    assert not store.list(kind="fill")
    assert store.load_checkpoint(run) is not None
