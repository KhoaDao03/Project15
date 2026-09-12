"""Explicit, small walk-forward experiments; never changes running settings."""

import json
from dataclasses import asdict
from pathlib import Path

from .analytics import metrics
from .audit import audit_files, file_hash, unique_events
from .config import Strategy
from .engine import Engine


def replay_files(paths, config, store, parent_run=None):
    paths = [Path(p) for p in paths]
    inputs = [dict(path=str(p), sha256=file_hash(p)) for p in paths]
    audit = audit_files(paths)
    if not audit["events"]:
        raise ValueError("Empty replay dataset")
    engine = Engine(store, config, "BACKTEST", execute=True)
    count = 0
    try:
        for row in unique_events(paths):
            engine.ingest(row)
            count += 1
    except Exception as exc:
        store.add(
            "experiment_failed",
            {"reason": type(exc).__name__, "events": count, "inputs": inputs},
            engine.run_id,
            "BACKTEST",
            engine.last_received,
        )
        raise
    if any(file_hash(p) != item["sha256"] for p, item in zip(paths, inputs)):
        store.add(
            "experiment_failed", {"reason": "INPUT_CHANGED"}, engine.run_id, "BACKTEST", engine.last_received
        )
        raise ValueError("Replay input changed during execution")
    store.add(
        "experiment",
        dict(
            parent_run=parent_run,
            inputs=inputs,
            dataset_audit=audit,
            events=count,
            config_version=engine.config.version,
            open_positions=len(engine.executor.positions),
            ended_without_settlement=bool(engine.executor.positions),
        ),
        engine.run_id,
        "BACKTEST",
        engine.last_received,
    )
    return engine.run_id


def walk_forward(manifest_path, store):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    candidates = [Strategy(**c) for c in manifest["candidates"]]
    if not 1 <= len(candidates) <= 12:
        raise ValueError("Use 1–12 predeclared configurations; no unrestricted parameter search")
    min_markets = manifest.get("minimum_training_markets", 30)
    if min_markets < 1:
        raise ValueError("Positive training minimum required")
    last_test_end = -float("inf")
    report = []
    validated = []
    for fold in manifest["folds"]:
        train = [manifest_path.parent / p for p in fold["train"]]
        test = [manifest_path.parent / p for p in fold["test"]]
        train_audit, test_audit = audit_files(train), audit_files(test)
        if not train_audit["events"] or not test_audit["events"]:
            raise ValueError("Empty fold")
        embargo = manifest.get("embargo_seconds", 0)
        if embargo < 0:
            raise ValueError("Nonnegative embargo required")
        if train_audit["last_received"] + embargo >= test_audit["first_received"]:
            raise ValueError("Training and holdout overlap")
        if set(train_audit["active_markets"]) & set(test_audit["active_markets"]):
            raise ValueError("Training and holdout share a market")
        if test_audit["first_received"] <= last_test_end:
            raise ValueError("Holdout folds overlap or go backwards")
        if not train_audit["research_valid"] or not test_audit["research_valid"]:
            raise ValueError(
                "Fold failed dataset audit; inspect gaps, clock adjustments and settlement coverage"
            )
        if train_audit["synthetic"] != test_audit["synthetic"]:
            raise ValueError("Do not mix synthetic and authentic training/holdout data")
        last_test_end = test_audit["last_received"]
        validated.append((train, test, last_test_end))
    for train, test, last_test_end in validated:
        scores = []
        for config in candidates:
            run = replay_files(train, config, store)
            m = metrics(store, "BACKTEST", run)
            scores.append(
                dict(
                    run_id=run,
                    config_version=config.version,
                    brier=m["calibration"]["brier"],
                    markets=m["calibration"]["n"],
                    net_pnl=m["net_pnl"],
                )
            )
        eligible = [i for i, s in enumerate(scores) if s["markets"] >= min_markets and s["brier"] is not None]
        if not eligible:
            result = dict(status="INSUFFICIENT_TRAINING_DATA", training=scores)
            store.add("walk_forward", result, scores[0]["run_id"], "BACKTEST", last_test_end)
            report.append(result)
            continue
        # Primary selection metric is calibration; ties preserve declared candidate order.
        selected = min(eligible, key=lambda i: scores[i]["brier"])
        config = candidates[selected]
        run = replay_files(test, config, store, parent_run=scores[selected]["run_id"])
        outcome = metrics(store, "BACKTEST", run)
        result = dict(
            status="HOLDOUT_EVALUATED", training=scores, selected_config=asdict(config), holdout=outcome
        )
        store.add("walk_forward", result, run, "BACKTEST", last_test_end)
        report.append(result)
    return report
