"""Paced captured-feed replay with concurrent HTTP polling and SSE; uses a temporary DB."""

import argparse
import bisect
import itertools
import json
import math
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import uvicorn

from btc15.config import Strategy
from btc15.dashboard import create_app
from btc15.models import ModelGroup, activate, register
from btc15.storage import CompactRecorder, Store, read_events
from btc15.strategies.momentum import Momentum, volatility_model
from btc15.strategies.settlement_edge.model import Tick

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("journal")
parser.add_argument("--limit", type=int, default=30000)
args = parser.parse_args()
rows = list(itertools.islice(read_events(args.journal), args.limit))
if len(rows) < 2:
    raise SystemExit("Need at least two events")
arrivals = [r["received"] - rows[0]["received"] for r in rows]
if arrivals != sorted(arrivals):
    raise SystemExit("Benchmark requires a capture without a wall-clock reversal")

with tempfile.TemporaryDirectory(prefix="btc15-dashboard-load-") as directory:
    store = Store("sqlite:///" + directory + "/test.db")
    for model in [Momentum(), volatility_model()]:
        activate(store, register(store, model), True)
    group = ModelGroup(store, Strategy(), mode="BACKTEST", execute=True, record_evaluations=False)
    # Exercise warmed models; this synthetic history is a load fixture, not trading evidence.
    start = math.floor(rows[0]["received"])
    for engine in group.engines:
        engine.ticks = [
            Tick(start - 3600 + i, start - 3600 + i, 79200 + math.sin(i) * 2) for i in range(3600)
        ]
        engine.raw_archive = True
    recorder = CompactRecorder(Path(directory) / "raw")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(store), log_level="error"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Dashboard failed to start")
        time.sleep(0.01)
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    stopped = threading.Event()
    requests, errors, stream_messages = [], [], []

    def poll():
        with httpx.Client(base_url=base, timeout=10) as client:
            while not stopped.is_set():
                for endpoint in ("health", "evaluation", "strategies", "runs", "records"):
                    before = time.perf_counter()
                    try:
                        params = {"mode": "BACKTEST"}
                        if endpoint == "evaluation":
                            params["run_id"] = group.run_id
                        response = client.get("/api/" + endpoint, params=params)
                        response.raise_for_status()
                        requests.append(
                            (endpoint, (time.perf_counter() - before) * 1000, len(response.content))
                        )
                    except Exception as exc:
                        errors.append(str(exc))
                stopped.wait(1)

    def stream():
        try:
            with httpx.stream("GET", base + "/api/market-stream", timeout=10) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        stream_messages.append(1)
                    if stopped.is_set():
                        break
        except Exception as exc:
            errors.append(str(exc))

    clients = [threading.Thread(target=poll), threading.Thread(target=stream)]
    for client in clients:
        client.start()
    started = time.perf_counter()
    offset, maximum_queue, last_publish, last_display = 0, 0, -1, -1
    lags = []
    try:
        while offset < len(rows):
            elapsed = time.perf_counter() - started
            available = bisect.bisect_right(arrivals, elapsed)
            if available <= offset:
                time.sleep(min(0.05, arrivals[offset] - elapsed))
                continue
            maximum_queue = max(maximum_queue, available - offset)
            end = min(available, offset + 256)
            batch = rows[offset:end]
            recorder.append_rows(batch)
            group.apply_activation(store)
            for index, row in enumerate(batch, offset):
                group.ingest(row)
                lags.append(max(0, time.perf_counter() - started - arrivals[index]))
            offset = end
            if elapsed - last_display >= 0.05:
                now = time.time()
                store.publish_market_display(
                    dict(
                        published_at=now,
                        connected=True,
                        run_id=group.run_id,
                        processing_lag=lags[-1],
                        markets=[
                            dict(ticker=ticker, book=group.books[ticker].summary())
                            for ticker in group.markets
                            if ticker in group.books
                        ],
                    )
                )
                store.publish_market_display(
                    dict(
                        price=group.ticks[-1].price if group.ticks else None,
                        received=now,
                        published_at=now,
                    ),
                    "reference",
                )
                last_display = elapsed
            if elapsed - last_publish >= 1:
                for engine in group.engines:
                    if engine.latest:
                        latest = max(engine.latest.values(), key=lambda b: b["timestamp"])
                        store.publish_record(
                            "evaluation",
                            latest,
                            engine.run_id,
                            engine.mode,
                            latest["timestamp"],
                            latest["ticker"],
                            engine._last_op[latest["ticker"]],
                        )
                store.publish_record(
                    "status",
                    dict(
                        connected=True,
                        live_enabled=False,
                        processing_lag=lags[-1],
                        maximum_queue=maximum_queue,
                    ),
                    group.run_id,
                    "BACKTEST",
                    time.time(),
                )
                last_publish = elapsed
    finally:
        recorder.close()
        elapsed = time.perf_counter() - started
        stopped.set()
        for client in clients:
            client.join(timeout=12)
        server.should_exit = True
        server_thread.join(timeout=10)
        listener.close()
    report = dict(
        events=len(rows),
        recorded_seconds=arrivals[-1],
        elapsed_seconds=elapsed,
        models=len(group.engines),
        synthetic_warmup=True,
        maximum_queue=maximum_queue,
        processing_lag_ms=dict(p95=float(np.percentile(lags, 95)) * 1000, maximum=max(lags) * 1000),
        compact_bytes=recorder.path.stat().st_size,
        replay_events=sum(1 for _ in read_events(recorder.path)),
        sse_messages=len(stream_messages),
        errors=errors,
        requests={},
    )
    for endpoint in sorted({r[0] for r in requests}):
        samples = [r for r in requests if r[0] == endpoint]
        report["requests"][endpoint] = dict(
            count=len(samples),
            p95_ms=float(np.percentile([r[1] for r in samples], 95)),
            maximum_ms=max(r[1] for r in samples),
            maximum_bytes=max(r[2] for r in samples),
        )
    print(json.dumps(report, indent=2))
    if errors or not stream_messages or report["replay_events"] != len(rows) or max(lags) >= 3:
        raise SystemExit("FAIL: request errors, missing input/stream data, or processing lag >= 3 seconds")
