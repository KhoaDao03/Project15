"""Single-engine scope, archive isolation, and non-destructive legacy recovery."""

import asyncio
import copy
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient

from btc15 import runner
from btc15.analytics import metrics
from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.engine import Engine
from btc15.models import guard_archived_exposure, identity, require_single_run


def saved_run(store, run, *, archived=False, mode="PAPER", config=None):
    config = config or Strategy()
    model = identity(config)
    if archived:
        model = {**model, "model_id": "retired-momentum", "model_name": "Archived momentum"}
    store.add(
        "run", dict(model=model, config=asdict(config), versions=dict(config=config.version)), run, mode, 1
    )
    return model


def trade_result(store, run, model, pnl, mode="PAPER"):
    store.add(
        "trade_result",
        dict(
            model=model,
            net_pnl=pnl,
            gross_pnl=pnl,
            fees=0,
            reason="SETTLEMENT",
            side="yes",
            bought=1,
            cost=0.5,
            proceeds=0.5 + pnl,
        ),
        run,
        mode,
        3,
        "BTC",
        "op-" + run,
    )


def test_only_one_disabled_strategy_card_and_no_registry_writes(store, tmp_path):
    settings = Settings(data_dir=str(tmp_path))
    with TestClient(
        create_app(store, settings=settings, config=replace(Strategy(), enabled=False))
    ) as client:
        data = client.get("/api/strategies").json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["model"]["model_id"] == "settlement-edge"
        assert data["rows"][0]["entries_enabled"] is False
        assert data["rows"][0]["record"] is None
        assert client.get("/api/strategies?mode=INVALID").status_code == 422
    assert not store.list(limit=None)


def test_scope_filters_history_counts_runs_results_and_headline(store, tmp_path):
    control = saved_run(store, "control")
    retired = saved_run(store, "retired", archived=True)
    old_config = saved_run(store, "old-config", config=replace(Strategy(), min_edge=0.04))
    for run, model, pnl in (
        ("control", control, 0.1),
        ("retired", retired, 100),
        ("old-config", old_config, 7),
    ):
        trade_result(store, run, model, pnl)
        store.add("order", dict(model=model, status="submitted"), run, "PAPER", 2, "BTC", "op-" + run)
    # Registry data remains, but it no longer enables an executable strategy.
    store.add("model_activation", dict(key="retired-momentum:v1", active=True), "model-registry", "PAPER", 1)
    before = copy.deepcopy(store.list(limit=None))
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        data = client.get("/api/strategies").json()
        assert len(data["rows"]) == 1
        assert data["lifetime"]["net_pnl"] == 0.1
        assert [r["run_id"] for r in data["rows"][0]["runs"]] == ["control"]
        assert {r["run_id"] for r in client.get("/api/runs").json()} == {"control", "old-config"}
        assert [r["run_id"] for r in client.get("/api/runs?scope=archive").json()] == ["retired"]
        assert client.get("/api/records?kind=order&limit=1").json()["total"] == 2
        archive = client.get("/api/records?kind=order&scope=archive").json()
        assert archive["total"] == 1 and archive["rows"][0]["run_id"] == "retired"
        assert client.get("/api/trades").json()["total"] == 2
        assert client.get("/api/trades?scope=archive").json()["total"] == 1
        assert client.get("/api/trades?run_id=retired").json()["total"] == 0
        assert client.get("/api/analytics?scope=archive&run_id=retired").json()["net_pnl"] == 100
        assert client.get("/api/analytics?run_id=control").json()["net_pnl"] == 0.1
        assert client.get("/api/records?scope=bogus").status_code == 422
        selected = client.get("/api/strategies?run_id=old-config").json()
        assert selected["rows"][0]["model"]["config_hash"] == old_config["config_hash"]
        assert selected["lifetime"]["net_pnl"] == 7
    assert store.list(limit=None) == before


def test_legacy_records_without_embedded_identity_use_the_run_identity(store):
    saved_run(store, "retired", archived=True)
    store.add(
        "trade_result",
        dict(net_pnl=50, gross_pnl=50, fees=0, side="yes", reason="SETTLEMENT"),
        "retired",
        "PAPER",
        2,
        "BTC",
        "op",
    )
    assert metrics(store)["trades"] == 0
    assert metrics(store, run_id="retired", scope="archive")["net_pnl"] == 50


def test_modes_and_selected_run_do_not_mix(store, tmp_path):
    paper = saved_run(store, "paper")
    backtest = saved_run(store, "backtest", mode="BACKTEST")
    trade_result(store, "paper", paper, 0.2)
    trade_result(store, "backtest", backtest, 10, "BACKTEST")
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        assert client.get("/api/strategies").json()["lifetime"]["net_pnl"] == 0.2
        assert client.get("/api/strategies?mode=BACKTEST").json()["lifetime"]["net_pnl"] == 10
        assert client.get("/api/strategies?mode=LIVE").json()["lifetime"]["completed_trades"] == 0


def test_archived_evaluation_is_not_a_live_strategy(store, tmp_path):
    model = saved_run(store, "retired", archived=True)
    store.publish_record("evaluation", dict(model=model, decision="NO_TRADE"), "retired", "PAPER", 2)
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        assert client.get("/api/evaluation?run_id=retired").json()["record"] is None
        assert client.get("/api/strategies").json()["rows"][0]["record"] is None


def test_group_resume_fails_before_writing_or_dropping_children(store, config):
    store.add("model_group", dict(members=[dict(key="retired:v1", config={})]), "legacy", "PAPER", 1)
    before = copy.deepcopy(store.list(limit=None))
    with pytest.raises(ValueError, match="retired multi-strategy portfolio"):
        Engine(store, config, run_id="legacy", resume=True)
    assert store.list(limit=None) == before
    assert store.load_checkpoint("legacy") is None


def test_empty_legacy_group_and_flat_control_resume_preserve_identity(store, config):
    engine = Engine(store, config, run_id="control")
    store.checkpoint("control", engine.executor.snapshot())
    store.add("model_group", dict(members=[]), "control", "PAPER", 1)
    resumed = Engine(store, config, run_id="control", resume=True)
    assert resumed.run_id == "control"
    assert resumed.executor.snapshot()["config_version"] == config.version
    assert len(store.list(kind="run", run_id="control")) == 1
    assert len(store.list(kind="resume", run_id="control")) == 1
    assert store.list(kind="model_group", run_id="control")[0]["body"] == {"members": []}


def test_archived_run_id_cannot_be_resumed_as_control(store):
    saved_run(store, "retired", archived=True)
    with pytest.raises(ValueError, match="retired multi-strategy"):
        require_single_run(store, "retired")


@pytest.mark.parametrize("evidence", ["unresolved_fill", "checkpoint_position", "pending_order"])
def test_archived_exposure_blocks_new_paper_start_without_mutation(store, evidence):
    model = saved_run(store, "retired", archived=True)
    if evidence == "unresolved_fill":
        store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    elif evidence == "checkpoint_position":
        store.checkpoint("retired", dict(positions={"BTC": {"quantity": 1}}, orders={}))
    else:
        store.checkpoint("retired", dict(positions={}, orders={"BTC": {"active": True}}))
    before = copy.deepcopy(store.list(limit=None))
    checkpoint = store.load_checkpoint("retired")
    with pytest.raises(ValueError, match="Unresolved archived paper exposure: retired"):
        guard_archived_exposure(store)
    assert store.list(limit=None) == before
    assert store.load_checkpoint("retired") == checkpoint


def test_resolved_archive_does_not_block_new_control(store):
    model = saved_run(store, "retired", archived=True)
    store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    # Same opportunity ID in a different run must NOT resolve this inventory.
    store.add("trade_result", {}, "different", "PAPER", 3, "BTC", "op")
    with pytest.raises(ValueError, match="Unresolved archived"):
        guard_archived_exposure(store)
    store.add("trade_result", {}, "retired", "PAPER", 3, "BTC", "op")
    store.checkpoint("retired", dict(positions={}, orders={"BTC": {"active": False}}))
    guard_archived_exposure(store)


def test_runner_checks_legacy_before_creating_a_replacement_run(store, config, tmp_path, monkeypatch):
    from test_collection import fake_client

    fake_client(monkeypatch, {}, {})
    model = saved_run(store, "retired", archived=True)
    store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    with pytest.raises(ValueError, match="Unresolved archived"):
        asyncio.run(
            runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                managed_run="new-control",
                duration=0.1,
            )
        )
    assert not store.list(kind="run", run_id="new-control")
    assert store.writer_owner() is None


def test_retired_cli_commands_are_explicitly_unavailable(monkeypatch):
    import sys

    from btc15 import cli

    for command in ("models", "model-paper", "model-backtest", "model-comparison"):
        monkeypatch.setattr(sys, "argv", ["btc15", command])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2


def test_ui_has_single_strategy_settings_and_explicit_archive(store, tmp_path):
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        html = client.get("/").text
        javascript = client.get("/static/app.js").text
        assert 'id="history-scope"' in html and 'value="archive"' in html
        assert 'id="strategy-library"' not in html
        assert ">Settings</button>" in html
        assert 'value="order"' in html and 'value="fill"' in html
        assert "model-paper" not in javascript
        assert "all recorded strategies" not in javascript
