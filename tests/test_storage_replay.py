import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from btc15.analytics import calibration, metrics
from btc15.dashboard import create_app
from btc15.demo import generate
from btc15.engine import Engine
from btc15.runner import backtest
from btc15.storage import RawRecorder, export_packet, read_events, records


def test_history_database_enforced_immutable(store):
    rid = store.add("opportunity", {"p_yes": 0.9}, "r", "PAPER", 1, "m", "o")
    for operation in [
        update(records).where(records.c.id == rid).values(body="{}"),
        delete(records).where(records.c.id == rid),
    ]:
        with pytest.raises(DBAPIError), store.engine.begin() as c:
            c.execute(operation)
    assert store.list()[0]["body"] == {"p_yes": 0.9}
    assert store.claim("r", "entry:m")
    assert not store.claim("r", "entry:m")


def test_transition_and_writer_exclusion(store):
    store.transition("r", "PAPER", "m", "DISCOVER_MARKET", 0)
    with pytest.raises(ValueError):
        store.transition("r", "PAPER", "m", "POSITION_OPEN", 1)
    store.transition("r", "PAPER", "m", "VALIDATE_MARKET", 1)
    assert len(store.list(kind="transition")) == 2
    store.acquire("collector", "a")
    with pytest.raises(RuntimeError):
        store.acquire("collector", "b")
    store.release("collector", "b")
    with pytest.raises(RuntimeError):
        store.acquire("collector", "b")
    store.release("collector", "a")
    store.acquire("collector", "b")


def test_recorder_parquet_journal_roundtrip(tmp_path):
    r = RawRecorder(tmp_path, chunk_size=2)
    r.append({"type": "tick", "msg": {"value": "123"}}, 1.0, 100, "c")
    r.append({"type": "disconnect"}, 2.0, 200, "c")
    r.close()
    journal = list(tmp_path.glob("*.jsonl"))[0]
    parquet = list(tmp_path.glob("*.parquet"))[0]
    assert list(read_events(journal)) == list(read_events(parquet))
    with journal.open("a") as f:
        f.write("{broken")
    with pytest.raises(ValueError, match="Corrupt"):
        list(read_events(journal))


def test_complete_replay_packet_and_dashboard(store, config, tmp_path):
    path = generate(tmp_path / "synthetic.jsonl")
    run = backtest(path, config, store)
    assert not store.list(kind="health", run_id=run)
    report = metrics(store, "BACKTEST", run)
    assert report["trades"] == 1 and report["partial_fill_rate"] == 1
    assert report["calibration"]["n"] == 1
    assert report["all_prediction_calibration"]["n"] > 1
    assert report["mode"] == "BACKTEST"
    ops = store.list(kind="opportunity", run_id=run)
    op = next(r for r in ops if r["body"]["decision"] == "TRADE_CANDIDATE")
    assert op["body"]["versions"]["config"] == config.version
    target = export_packet(store, op["id"], tmp_path / "packets")
    assert (target / "summary.json").exists() and (target / "probability_path.parquet").exists()
    assert json.loads((target / "config.json").read_text())["seed"] == config.seed
    client = TestClient(create_app(store))
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json()["live_enabled"] is False
    assert client.get("/api/records?mode=PAPER").json()["total"] == 0
    assert client.get("/api/records?mode=BACKTEST").json()["total"] > 0
    assert client.get("/api/replay/" + op["id"]).json()["timeline"]
    trades = client.get("/api/trades?mode=BACKTEST").json()
    assert trades["total"] == 1 and trades["rows"][0]["opportunity_id"] == op["id"]
    assert client.get("/api/replay/missing").status_code == 404
    assert client.post("/api/orders", json={}).status_code in (404, 405)
    # Counterfactual uses a different run and does not alter original predictions.
    before = store.list(kind="opportunity", run_id=run)
    other = backtest(path, replace(config, min_edge=0.2), store, parent_run=run)
    assert metrics(store, "BACKTEST", other)["trades"] == 0
    assert store.list(kind="opportunity", run_id=run) == before


def test_gap_invalidates_book_and_replay_rejects_time_reordering(store, config, tmp_path):
    e = Engine(store, config, "BACKTEST", execute=False)

    def row(seq, received):
        return dict(
            id=str(seq),
            connection_id="c",
            monotonic_ns=int(received * 1e9),
            received=received,
            payload=dict(
                type="cfbenchmarks_value",
                sid=1,
                seq=seq,
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(dict(type="value", id="BRTI", time=received * 1000, value="78000")),
                ),
            ),
        )

    assert e.ingest(row(1, 1))
    assert not e.ingest(row(3, 2))
    assert not e.healthy
    with pytest.raises(ValueError, match="backwards"):
        e.ingest(row(4, 1))


def test_calibration_known_scores():
    r = calibration([(0.8, 1), (0.2, 0)])
    assert r["brier"] == pytest.approx(0.04)
    assert r["ece"] == pytest.approx(0.2)
    assert r["log_loss"] == pytest.approx(-__import__("math").log(0.8))
    assert calibration([])["brier"] is None


def test_unverified_event_fees_block_signal_and_preserve_rejection(store, config, tmp_path):
    path = generate(tmp_path / "synthetic.jsonl")
    e = Engine(store, config, "BACKTEST")
    for row in read_events(path):
        if row["payload"]["type"] == "metadata":
            row["payload"]["msg"].pop("fee_changes")
        e.ingest(row)
        if e.latest:
            break
    op = store.list(kind="opportunity")[-1]["body"]
    assert "UNVERIFIED_FEES" in [r["code"] for r in op["reasons"]]
    assert not e.executor.orders


def test_walk_forward_rejects_overlap_before_any_experiment(store, tmp_path):
    from btc15.research import walk_forward

    generate(tmp_path / "data.jsonl")
    manifest = tmp_path / "folds.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [{"paths": 100}],
                "minimum_training_markets": 1,
                "folds": [{"train": ["data.jsonl"], "test": ["data.jsonl"]}],
            }
        )
    )
    with pytest.raises(ValueError, match="overlap"):
        walk_forward(manifest, store)
    assert not store.list(kind="run")


def test_walk_forward_frozen_holdout_and_insufficient_samples(store, tmp_path):
    from btc15.research import walk_forward

    generate(tmp_path / "train.jsonl")
    generate(tmp_path / "test.jsonl", start=1788987600)
    manifest = tmp_path / "folds.json"
    settings = {
        "candidates": [{"paths": 100, "evaluation_interval": 30}],
        "minimum_training_markets": 1,
        "folds": [{"train": ["train.jsonl"], "test": ["test.jsonl"]}],
    }
    manifest.write_text(json.dumps(settings))
    results = walk_forward(manifest, store)
    assert results[0]["status"] == "HOLDOUT_EVALUATED"
    assert results[0]["holdout"]["calibration"]["n"] == 1
    training = results[0]["training"][0]["run_id"]
    assert training != results[0]["holdout"]["run_id"]
    assert len(store.list(kind="walk_forward")) == 1
    settings["minimum_training_markets"] = 30
    manifest.write_text(json.dumps(settings))
    result = walk_forward(manifest, store)[0]
    assert result["status"] == "INSUFFICIENT_TRAINING_DATA"
    assert "holdout" not in result
