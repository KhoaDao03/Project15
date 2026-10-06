"""Synthetic, offline comparison. Run with PYTHONPATH=src:tests; requires dev dependencies."""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from test_live_control_queries import legacy_cycle_controls, legacy_sync_markets, order, seed

from btc15 import live_automation as module
from btc15.manual_trading import ManualTrading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("--samples must be at least 2")
    assets = ["BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "DOGE"]
    original_clock = module.time
    module.time = SimpleNamespace(time=lambda: 1000)
    try:
        with tempfile.TemporaryDirectory(prefix="control-query-benchmark-") as directory:
            manual = ManualTrading(Path(directory) / "orders.sqlite", settings=SimpleNamespace())
            worker = module.LiveAutomation(manual, {}, {})
            controls = [
                dict(
                    ticker=f"{assets[i % 7]}-{i:05}",
                    asset=assets[i % 7],
                    close_time=i - 10000,
                    enabled=True,
                    paused=False,
                    contracts=10,
                    revision=1,
                    config_version="test",
                    stop_price=0.49,
                )
                for i in range(9169)
            ]
            controls += [
                dict(
                    ticker=a + "-active",
                    asset=a,
                    close_time=1100,
                    enabled=True,
                    paused=False,
                    contracts=10,
                    revision=1,
                    config_version="test",
                    stop_price=0.49,
                )
                for a in assets
            ]
            orders = []
            for i, asset in enumerate(assets):
                orders += [
                    order(asset + "-active", f"buy-{i}", quantity="10"),
                    order(asset + "-active", f"sell-{i}", action="sell", quantity="4"),
                    order(controls[i]["ticker"], f"pending-{i}", state="unknown", quantity="0"),
                ]
            seed(worker, controls, orders)
            worker.members = {a: dict(run_id=a) for a in assets}
            worker.stores = {
                a: SimpleNamespace(
                    read_market_display=lambda a=a: dict(
                        run_id=a, markets=[dict(ticker=a + "-active", close_time="1970-01-01T00:18:20+00:00")]
                    )
                )
                for a in assets
            }
            worker.assets = lambda: {a: dict(enabled=True) for a in assets}
            with manual.db() as db:
                db.execute("DROP INDEX live_controls_asset_recent")
            started = time.perf_counter()
            with manual.db() as db:
                db.execute(
                    "CREATE INDEX live_controls_asset_recent ON live_controls("
                    "json_extract(body, '$.asset'), json_extract(body, '$.close_time') DESC, "
                    "json_extract(body, '$.ticker') DESC)"
                )
            index_ms = (time.perf_counter() - started) * 1000
            expected = legacy_cycle_controls(worker)
            assert worker.cycle_controls() == expected
            functions = {
                "cycle": (lambda: legacy_cycle_controls(worker), worker.cycle_controls),
                "sync": (lambda: legacy_sync_markets(worker), worker.sync_markets),
            }
            result = dict(
                controls=len(controls),
                active_controls=7,
                order_rows=len(orders),
                selected_controls=len(expected),
                samples=args.samples,
                equivalent=True,
                index_build_ms=round(index_ms, 3),
                timings={},
            )
            for name, pair in functions.items():
                measurements = [[], []]
                for fn in pair:
                    fn()
                for sample in range(args.samples):
                    for i in (0, 1) if sample % 2 else (1, 0):
                        wall, cpu = time.perf_counter(), time.process_time()
                        pair[i]()
                        measurements[i].append(
                            ((time.perf_counter() - wall) * 1000, (time.process_time() - cpu) * 1000)
                        )
                result["timings"][name] = {
                    label: dict(
                        wall_ms=round(statistics.median(m[0] for m in values), 3),
                        cpu_ms=round(statistics.median(m[1] for m in values), 3),
                    )
                    for label, values in zip(("before", "after"), measurements)
                }
            print(json.dumps(result, indent=2))
    finally:
        module.time = original_clock


if __name__ == "__main__":
    main()
