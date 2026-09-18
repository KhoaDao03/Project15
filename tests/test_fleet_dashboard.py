import json
import time
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from btc15 import fleet
from btc15.config import Strategy
from btc15.models import identity
from btc15.storage import Store


@pytest.fixture
def portfolios(tmp_path):
    rows, stores = [], {}
    now = time.time()
    for number, asset in enumerate(("BTC", "ETH", "SOL", "XRP")):
        directory = tmp_path / asset
        directory.mkdir()
        config = Strategy(asset=asset)
        path = directory / "config.json"
        path.write_text(json.dumps(asdict(config)))
        run_id = asset.lower() + "-paper"
        store = stores[asset] = Store("sqlite:///" + str(directory / "paper.db"))
        store.add(
            "run", dict(model=identity(config), versions=dict(config=config.version)), run_id, "PAPER", now
        )
        store.add(
            "fill", dict(asset=asset), run_id, "PAPER", now, config.asset_spec.series + "-test", "op-" + asset
        )
        store.add(
            "status",
            dict(
                run_id=run_id,
                mode="PAPER",
                paper_execution=True,
                connected=True,
                clock_ok=True,
                exchange_open=True,
                reference_age=0,
                processing_lag=0,
                positions={},
                recovery=dict(entries_blocked=False, state="READY"),
                exposure=0,
                models=[dict(run_id=run_id, model=identity(config), realized_pnl=number)],
                daily=dict(pnl=number),
            ),
            run_id,
            "PAPER",
            now,
        )
        store.add(
            "trade_result",
            dict(net_pnl=number, cost=8, bought=10, proceeds=8 + number, fees=0),
            run_id,
            "PAPER",
            now,
        )
        store.publish_market_display(
            dict(
                run_id=run_id,
                connected=True,
                published_at=now,
                markets=[dict(ticker=asset + "-contract", fresh=True, book=dict(yes_bid=0.4))],
            )
        )
        store.publish_market_display(
            dict(
                run_id=run_id,
                connected=True,
                published_at=now,
                reference_5hz=dict(value=1.2345 + number, received=now, source_ts_ms=now * 1000),
            ),
            "reference",
        )
        rows.append(dict(asset=asset, data_dir=asset, config=asset + "/config.json", run_id=run_id))
    manifest = tmp_path / "dashboard.json"
    manifest.write_text(json.dumps(rows))
    yield manifest, stores
    for store in stores.values():
        store.engine.dispose()


def test_shared_ui_routes_records_and_settings_to_selected_asset(portfolios):
    manifest, stores = portfolios
    app = fleet.create_fleet_app(manifest)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["asset"] == "BTC"
        response = client.get("/api/fleet").json()
        assert response["realized_pnl"] == 6 and response["totals_complete"]
        assert [r["asset"] for r in response["assets"]] == list(stores)
        for asset in stores:
            prefix = "/assets/" + asset
            assert client.get(prefix + "/").status_code == 200
            assert client.get(prefix + "/api/health").json()["asset"] == asset
            records = client.get(prefix + "/api/records?mode=PAPER&kind=fill").json()["rows"]
            assert [r["body"]["asset"] for r in records] == [asset]
            settings = client.get(prefix + "/api/strategy").json()["config"]
            settings["min_probability"] = 0.91
            result = client.put(
                prefix + "/api/strategy", json=settings, headers={"Origin": "http://127.0.0.1:8000"}
            )
            assert result.status_code == 200, result.text
            assert json.loads((manifest.parent / asset / "strategy.json").read_text())["asset"] == asset
        assert client.get("/assets/INVALID/api/health").status_code == 404
        assert not any(s.writer_owner() for s in stores.values())


@pytest.mark.parametrize(
    "damage", ["duplicate_asset", "same_directory", "same_database", "wrong_config", "wrong_run_hash"]
)
def test_manifest_refuses_mixed_portfolios(portfolios, damage):
    manifest, stores = portfolios
    rows = json.loads(manifest.read_text())
    if damage == "duplicate_asset":
        rows[1] = rows[0]
    elif damage == "same_directory":
        rows[1]["data_dir"] = rows[0]["data_dir"]
    elif damage == "same_database":
        rows[1]["database_url"] = "sqlite:///BTC/paper.db"
    elif damage == "wrong_config":
        rows[1]["config"] = rows[0]["config"]
    else:
        path = manifest.parent / "ETH/config.json"
        config = json.loads(path.read_text())
        config["min_probability"] = 0.92
        path.write_text(json.dumps(config))
    manifest.write_text(json.dumps(rows))
    with pytest.raises(ValueError):
        fleet.create_fleet_app(manifest)


def test_stale_or_wrong_run_reference_never_looks_live(portfolios):
    manifest, stores = portfolios
    for asset in ("SOL", "XRP"):
        body = stores[asset].read_market_display("reference")
        if asset == "SOL":
            body["published_at"] -= 10
        else:
            body["run_id"] = "other-run"
        stores[asset].publish_market_display(body, "reference")
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        rows = {r["asset"]: r for r in client.get("/api/fleet").json()["assets"]}
        assert rows["BTC"]["price"] is not None
        assert rows["SOL"]["price"] is None and rows["XRP"]["price"] is None


def test_shutdown_all_requires_confirmation_and_waits_for_every_ledger(portfolios, monkeypatch):
    async def prepared(self):
        return None

    monkeypatch.setattr(fleet.ExecutionClient, "prepare_shutdown", prepared)
    manifest, stores = portfolios
    app = fleet.create_fleet_app(manifest)
    exits = []
    app.state.shutdown_server = lambda: exits.append(True)
    for asset, store in stores.items():
        store.acquire("collector", asset + "-owner")
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/shutdown", json={"confirm": True}).status_code == 403
        headers = {"Origin": "http://127.0.0.1:8000"}
        assert client.post("/api/shutdown", json={"confirm": 1}, headers=headers).status_code == 422
        for _ in range(2):
            assert client.post("/api/shutdown", json={"confirm": True}, headers=headers).status_code == 202
        deadline = time.monotonic() + 5
        while not all(s.list("shutdown_request", a + "-owner") for a, s in stores.items()):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        for asset, store in stores.items():
            assert len(store.list("shutdown_request", asset + "-owner")) == 1
        for asset in ("BTC", "ETH", "SOL"):
            with stores[asset].transaction():
                stores[asset].add(
                    "shutdown_complete", dict(open_positions=1), asset + "-owner", "PAPER", time.time()
                )
                stores[asset].release("collector", asset + "-owner")
        assert client.get("/api/shutdown").json()["status"] == "stopping" and not exits
        with stores["XRP"].transaction():
            stores["XRP"].add("shutdown_complete", dict(open_positions=2), "XRP-owner", "PAPER", time.time())
            stores["XRP"].release("collector", "XRP-owner")
        while client.get("/api/shutdown").json()["status"] != "stopped":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.get("/api/shutdown").json()["open_positions"] == 5


def test_shutdown_one_asset_leaves_other_collectors_and_dashboard_running(portfolios, monkeypatch):
    prepared_assets = []

    async def prepared(self, asset=None):
        prepared_assets.append(asset)

    monkeypatch.setattr(fleet.ExecutionClient, "prepare_shutdown", prepared)
    manifest, stores = portfolios
    app = fleet.create_fleet_app(manifest)
    for asset, store in stores.items():
        store.acquire("collector", asset + "-owner")
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        endpoint = "/api/bots/ETH/shutdown"
        headers = {"Origin": "http://127.0.0.1:8000"}
        assert client.post(endpoint, json={"confirm": True}).status_code == 403
        assert client.post(endpoint, json={"confirm": 1}, headers=headers).status_code == 422
        assert client.post(endpoint, json={"confirm": True}, headers=headers).status_code == 202
        deadline = time.monotonic() + 5
        while not stores["ETH"].list("shutdown_request", "ETH-owner"):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert prepared_assets == ["ETH"]
        for asset in ("BTC", "SOL", "XRP"):
            assert not stores[asset].list("shutdown_request")
        with stores["ETH"].transaction():
            stores["ETH"].add("shutdown_complete", dict(open_positions=1), "ETH-owner", "PAPER", time.time())
            stores["ETH"].release("collector", "ETH-owner")
        while client.get(endpoint).json()["status"] == "stopping":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert client.get(endpoint).json()["status"] == "stopped"
        assert client.get("/api/shutdown").json()["status"] == "idle"
        assert client.get("/api/fleet").status_code == 200


def test_shutdown_failure_keeps_dashboard_open_and_reports_asset(portfolios, monkeypatch):
    async def prepared(self):
        return None

    monkeypatch.setattr(fleet.ExecutionClient, "prepare_shutdown", prepared)
    manifest, stores = portfolios
    for asset, store in stores.items():
        store.acquire("collector", asset + "-owner")

    async def finish(store, owner, state, callback, **kwargs):
        state.update(status="failed" if owner == "ETH-owner" else "stopped", message="test", open_positions=0)

    monkeypatch.setattr(fleet, "finish_shutdown", finish)
    app = fleet.create_fleet_app(manifest)
    exits = []
    app.state.shutdown_server = lambda: exits.append(True)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.post("/api/shutdown", json={"confirm": True}, headers={"Origin": "http://127.0.0.1:8000"})
        deadline = time.monotonic() + 5
        while (result := client.get("/api/shutdown").json())["status"] == "stopping":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert result["status"] == "failed" and "ETH" in result["message"] and not exits
        assert all(s.list("shutdown_request", a + "-owner") for a, s in stores.items())


def test_audited_checkpoint_migration_preserves_original_run_record(portfolios):
    manifest, stores = portfolios
    path = manifest.parent / "BTC/config.json"
    config = json.loads(path.read_text())
    config["min_probability"] = 0.92
    path.write_text(json.dumps(config))
    updated = Strategy(**config)
    before = stores["BTC"].run_summaries("PAPER")
    stores["BTC"].checkpoint("btc-paper", dict(config_version=updated.version, mode="PAPER"))
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        assert client.get("/api/strategy").json()["version"] == updated.version
    assert stores["BTC"].run_summaries("PAPER") == before


def test_overview_uses_realized_ledger_and_masks_stale_contract_quotes(portfolios):
    manifest, stores = portfolios
    stores["ETH"].add("trade_result", dict(net_pnl=-2.5), "eth-paper", "PAPER", time.time())
    stores["ETH"].add("trade_result", dict(net_pnl=999), "other-run", "PAPER", time.time())
    stale = stores["SOL"].read_market_display()
    stale["published_at"] -= 10
    stores["SOL"].publish_market_display(stale)
    wrong = stores["XRP"].read_market_display()
    wrong["run_id"] = "other-run"
    stores["XRP"].publish_market_display(wrong)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        response = client.get("/api/fleet").json()
        rows = {r["asset"]: r for r in response["assets"]}
        assert response["realized_pnl"] == 3.5
        assert rows["ETH"]["realized_pnl"] == -1.5
        assert rows["ETH"]["completed_trades"] == 2
        assert rows["BTC"]["markets"][0]["book"]["yes_bid"] == 0.4
        assert rows["SOL"]["markets"][0]["book"] == {}
        assert rows["SOL"]["markets"][0]["fresh"] is False
        assert rows["XRP"]["markets"] == []


@pytest.mark.parametrize(
    "pnls,rate,current,best,worst",
    [
        ([], None, 0, 0, 0),
        ([1, 2, -1, -2, -3, 4, 5], 4 / 7, 2, 2, 3),
        ([1, 0], 0.5, 0, 1, 0),
        ([1, -1, -2], 1 / 3, -2, 1, 2),
    ],
)
def test_overview_win_rate_and_streaks_follow_completed_active_run(
    portfolios, pnls, rate, current, best, worst
):
    manifest, stores = portfolios
    members = json.loads(manifest.read_text())
    members[0]["run_id"] = "streak-test"
    manifest.write_text(json.dumps(members))
    # Insert out of order to verify chronological streaks; the fixture's old run is excluded.
    for i in reversed(range(len(pnls))):
        stores["BTC"].add("trade_result", dict(net_pnl=pnls[i]), "streak-test", "PAPER", 100 + i)
    stores["BTC"].add("fill", dict(net_pnl=999), "streak-test", "PAPER", 200)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        row = client.get("/api/fleet").json()["assets"][0]
        assert row["completed_trades"] == len(pnls)
        assert row["wins"] == sum(p > 0 for p in pnls)
        assert row["losses"] == sum(p < 0 for p in pnls)
        assert row["breakeven_trades"] == sum(p == 0 for p in pnls)
        assert row["win_rate"] == rate
        assert row["current_streak"] == current
        assert row["longest_win_streak"] == best
        assert row["longest_loss_streak"] == worst


def test_snapshot_published_during_request_is_not_future_dated(portfolios, monkeypatch):
    manifest, _ = portfolios
    original = Store.read_market_display

    def read(self, key="current"):
        row = original(self, key)
        if row and key in ("reference", "current"):
            now = time.time()
            row["published_at"] = now
            if key == "reference":
                row["reference_5hz"]["received"] = now
                row["reference_5hz"]["source_ts_ms"] = now * 1000
        return row

    monkeypatch.setattr(Store, "read_market_display", read)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        result = client.get("/api/fleet").json()
        for row in result["assets"]:
            assert row["price"] is not None
            assert row["markets"][0]["fresh"]
            assert row["markets"][0]["book"]["yes_bid"] == 0.4


def test_quote_request_does_not_scan_trade_history(portfolios, monkeypatch):
    manifest, _ = portfolios
    original = Store.list
    scans = []

    def listing(self, *args, **kwargs):
        if (args and args[0] == "trade_result") or kwargs.get("kind") == "trade_result":
            scans.append(self.engine.url.database)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "list", listing)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        initial_scans = len(scans)
        assert initial_scans >= 4
        for _ in range(2):
            response = client.get("/api/fleet").json()
            assert response["totals_complete"] and response["realized_pnl"] == 6
            assert all(r["performance_updated_at"] for r in response["assets"])
        assert len(scans) == initial_scans


def test_performance_failure_does_not_hide_quotes(portfolios, monkeypatch):
    manifest, _ = portfolios
    original = Store.list

    def listing(self, *args, **kwargs):
        if args and args[0] == "trade_result":
            raise OSError("history unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "list", listing)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        response = client.get("/api/fleet").json()
        assert response["realized_pnl"] is None and not response["totals_complete"]
        for row in response["assets"]:
            assert row["markets"][0]["fresh"]
            assert row["markets"][0]["book"]["yes_bid"] == 0.4
            assert row["realized_pnl"] is None


@pytest.mark.parametrize("offset", [-10, 10])
def test_fleet_rejects_stale_and_future_snapshots(portfolios, offset):
    manifest, stores = portfolios
    for store in stores.values():
        for key in ("current", "reference"):
            snapshot = store.read_market_display(key)
            snapshot["published_at"] = time.time() + offset
            store.publish_market_display(snapshot, key)
    with TestClient(fleet.create_fleet_app(manifest)) as client:
        for row in client.get("/api/fleet").json()["assets"]:
            assert row["price"] is None
            assert not row["markets"][0]["fresh"]
            assert row["markets"][0]["book"] == {}


def test_seven_asset_manifest_and_commodity_dashboard(portfolios):
    manifest, _ = portfolios
    rows = json.loads(manifest.read_text())
    for asset in ("GOLD", "SILVER", "WTI"):
        directory = manifest.parent / asset
        directory.mkdir()
        config = directory / "config.json"
        config.write_text(json.dumps(asdict(Strategy(asset=asset))))
        rows.append(dict(asset=asset, data_dir=asset, config=str(config), run_id=asset.lower() + "-paper"))
    manifest.write_text(json.dumps(rows))
    assert len(fleet.load_members(manifest)) == 7
    with TestClient(fleet.create_fleet_app(manifest), base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/fleet").json()
        assert len(response["assets"]) == 7
        for asset in ("GOLD", "SILVER", "WTI"):
            assert client.get("/assets/" + asset + "/api/health").json()["asset"] == asset
            assert not next(r for r in response["assets"] if r["asset"] == asset)["paper_only"]
