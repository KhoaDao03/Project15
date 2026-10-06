"""Offline full-ladder CPU/memory check; temporary databases, no exchange traffic."""

import json
import math
import resource
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path

from btc15.config import Strategy
from btc15.domain import Book, parse_market
from btc15.engine import Engine
from btc15.hourly import publish_candidates
from btc15.storage import Store
from btc15.strategies.settlement_edge.model import Tick


def benchmark(root, asset, count):
    captured = json.loads(Path(f"tests/fixtures/{asset.lower()}-hourly-20261006.json").read_text())
    original = parse_market(captured["market"], captured["series"])
    config = Strategy.load(f"config/settlement-edge-{asset.lower()}-paper.json")
    now = original.close_time - 300
    tracemalloc.start()
    store = Store("sqlite:///" + str(root / f"{asset}.sqlite"))
    engine = Engine(
        store, config, mode="PAPER", run_id=asset, execute=False, signal_only=True, record_evaluations=False
    )
    engine.healthy = engine.clock_ok = engine.exchange_open = True
    engine.series_fees = captured["series"]
    engine.series_fee_changes = []
    spot = original.spec.strike
    amplitude = spot * 0.0005
    engine.ticks = [
        Tick(now - 3599 + i, now - 3599 + i, spot + amplitude * math.sin(i / 13)) for i in range(3600)
    ]
    for i in range(count):
        strike = round(spot * (1 + (i - count // 2) * 0.0005), config.asset_spec.round_digits)
        ticker = original.event_ticker + "-T" + str(strike)
        raw = dict(original.raw, ticker=ticker, floor_strike=strike, volume_fp=str(count - i))
        market = replace(original, ticker=ticker, raw=raw, spec=replace(original.spec, strike=strike))
        engine.markets[ticker] = market
        book = Book()
        book.snapshot(dict(yes_dollars_fp=[[".89", "100"]], no_dollars_fp=[[".90", "100"]]), now)
        engine.books[ticker] = book
        engine.fee_changes[market.event_ticker] = []
        for phase in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
            engine.state(ticker, phase, now)
    published = {}

    def process(at):
        engine.ticks.append(Tick(at, at, spot))
        for book in engine.books.values():
            book.received = book.source_time = at
        engine.process(at, str(at), "cfbenchmarks_value", {})
        publish_candidates(engine, published)
        assert len(engine.latest) == count
        assert all(d.get("probability") for d in engine.latest.values())

    process(now + 1)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    times = []
    for i in range(2, 9):
        wall, cpu = time.perf_counter(), time.process_time()
        process(now + i)
        times.append(((time.perf_counter() - wall) * 1000, (time.process_time() - cpu) * 1000))
    result = dict(
        asset=asset,
        strikes=count,
        median_wall_ms=round(statistics.median(t[0] for t in times), 2),
        median_cpu_ms=round(statistics.median(t[1] for t in times), 2),
        max_wall_ms=round(max(t[0] for t in times), 2),
        allocated_retained_mib=round(retained / 2**20, 2),
        allocated_peak_mib=round(peak / 2**20, 2),
        process_peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        candidates=len(store.read_market_display(f"hourly_candidates:{asset}") or {}),
    )
    store.engine.dispose()
    return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hourly-benchmark-") as directory:
        print(
            json.dumps(
                [benchmark(Path(directory), "ETHD", 300), benchmark(Path(directory), "XRPD", 75)], indent=2
            )
        )
