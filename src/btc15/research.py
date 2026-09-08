"""Explicit, small walk-forward experiments; never changes running settings."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .analytics import metrics
from .config import Strategy
from .engine import Engine
from .storage import read_events


def replay_files(paths, config, store, parent_run=None):
    engine = Engine(store, config, "BACKTEST", execute=True)
    count = 0
    inputs = []
    for path in paths:
        path = Path(path)
        inputs.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for row in read_events(path):
            engine.ingest(row)
            count += 1
    if not count:
        raise ValueError("Empty replay dataset")
    store.add(
        "experiment",
        dict(
            parent_run=parent_run,
            inputs=inputs,
            events=count,
            config_version=config.version,
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
    for fold in manifest["folds"]:
        train = [manifest_path.parent / p for p in fold["train"]]
        test = [manifest_path.parent / p for p in fold["test"]]
        # Validate ordering before any results are selected. Paths are immutable input artifacts.
        train_rows = [r for p in train for r in read_events(p)]
        test_rows = [r for p in test for r in read_events(p)]
        if not train_rows or not test_rows:
            raise ValueError("Empty fold")
        if max(r["received"] for r in train_rows) >= min(r["received"] for r in test_rows):
            raise ValueError("Training and holdout overlap")
        if min(r["received"] for r in test_rows) <= last_test_end:
            raise ValueError("Holdout folds overlap or go backwards")
        last_test_end = max(r["received"] for r in test_rows)
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
            report.append(dict(status="INSUFFICIENT_TRAINING_DATA", training=scores))
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
