import sys
import time

import pytest
from fastapi.testclient import TestClient

from btc15 import cli, dashboard
from btc15.config import Settings


@pytest.mark.parametrize(
    "arguments,collect,paper,run_id",
    [
        ([], True, True, "dashboard-paper"),
        (["--observe-only"], True, False, "dashboard-paper"),
        (["--no-collect"], False, True, "dashboard-paper"),
        (["--run-id", "existing-group"], True, True, "existing-group"),
    ],
)
def test_dashboard_cli_execution_defaults(store, monkeypatch, arguments, collect, paper, run_id):
    import uvicorn

    seen = {}
    original = dashboard.create_app

    def create_app(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "Store", lambda *args: store)
    monkeypatch.setattr(cli.Settings, "env", lambda: Settings())
    monkeypatch.setattr(dashboard, "create_app", create_app)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)
    monkeypatch.setattr(sys, "argv", ["btc15", "dashboard", *arguments])
    cli.main()
    assert seen["collect_live"] is collect
    assert seen["paper_execution"] is paper
    assert seen["run_id"] == run_id


def test_observe_only_has_no_managed_paper_run(store, monkeypatch):
    from btc15 import runner

    async def collect(*args, **kwargs):
        assert kwargs["paper"] is False
        assert kwargs["managed_run"] is None
        assert kwargs["min_free_bytes"] == 0
        await kwargs["stop_event"].wait()

    monkeypatch.setattr(runner, "collect", collect)
    settings = Settings(api_key_id="test", private_key_path="unused")
    with TestClient(
        dashboard.create_app(store, collect_live=True, settings=settings, paper_execution=False)
    ) as client:
        assert client.get("/api/health").status_code == 200
    assert not store.list(kind="run")


def test_dashboard_default_resumes_all_paper_checkpoints(store, config, tmp_path, raw, series, monkeypatch):
    from test_collection import fake_client, fake_socket

    from btc15.models import activate, register
    from btc15.strategies.momentum import Momentum, volatility_model

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    for model in (Momentum(), volatility_model()):
        activate(store, register(store, model), True)
    settings = Settings(data_dir=str(tmp_path / "paper-data"), api_key_id="test", private_key_path="unused")
    for attempt in range(2):
        with TestClient(dashboard.create_app(store, collect_live=True, settings=settings, config=config)):
            deadline = time.monotonic() + 5
            while True:
                rows = store.list(kind="run")
                resumes = store.list(kind="resume")
                if (
                    len(rows) == 3
                    and len(resumes) == attempt * 3
                    and all(store.load_checkpoint(row["run_id"]) for row in rows)
                ):
                    break
                assert time.monotonic() < deadline, "Dashboard did not start/resume the paper group"
                time.sleep(0.02)
            for row in rows:
                assert row["body"]["execute"] is True
                assert store.load_checkpoint(row["run_id"])
        assert store.writer_owner() is None
    assert len(store.list(kind="model_group", run_id="dashboard-paper")) == 1
