"""Replay a captured burst into a temporary database; never connects to an exchange."""

import argparse
import itertools
import json
import math
import tempfile
import time
from pathlib import Path

from btc15.config import Strategy
from btc15.models import ModelGroup, activate, register
from btc15.storage import RawRecorder, Store, read_events
from btc15.strategies.momentum import Momentum, volatility_model
from btc15.strategies.settlement_edge.model import Tick

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("journal")
parser.add_argument("--limit", type=int, default=60000)
parser.add_argument("--warm-history", action="store_true")
parser.add_argument("--trades-only", action="store_true", help="Measure paper retention without raw archives")
args = parser.parse_args()
rows = list(itertools.islice(read_events(args.journal), args.limit))
if len(rows) < 2:
    raise SystemExit("Need at least two events")
with tempfile.TemporaryDirectory(prefix="btc15-throughput-") as directory:
    store = Store("sqlite:///" + directory + "/test.db")
    for model in [Momentum(), volatility_model()]:
        activate(store, register(store, model), True)
    group = ModelGroup(
        store, Strategy(), mode="BACKTEST", execute=True, record_evaluations=not args.trades_only
    )
    if args.warm_history:
        start = math.floor(rows[0]["received"])
        for engine in group.engines:
            engine.ticks = [
                Tick(start - 3600 + i, start - 3600 + i, 79200 + math.sin(i) * 2) for i in range(3600)
            ]
    for row in rows:
        row["payload"] = json.dumps(row["payload"])
    recorder = None if args.trades_only else RawRecorder(Path(directory) / "raw")
    started = time.perf_counter()
    for offset in range(0, len(rows), 256):
        batch = rows[offset : offset + 256]
        if recorder:
            recorder.append_rows(batch)
        group.apply_activation(store)
        for row in batch:
            group.ingest(row)
    if recorder:
        recorder.close()
    elapsed = time.perf_counter() - started
    span = rows[-1]["received"] - rows[0]["received"]
    print(
        json.dumps(
            dict(
                events=len(rows),
                recorded_seconds=span,
                processing_seconds=elapsed,
                realtime_headroom=span / elapsed,
                warm_history=args.warm_history,
                models=len(group.engines),
                raw_events=sum(1 for _ in read_events(recorder.directory / (recorder.session + ".jsonl")))
                if recorder
                else 0,
                recording="trades_only" if args.trades_only else "full",
                saved_evaluations=len(store.list(kind="opportunity", limit=None)),
                fills=len(store.list(kind="fill", limit=None)),
            )
        )
    )
    if elapsed >= span:
        raise SystemExit("FAIL: replay did not keep pace with the recorded input")
