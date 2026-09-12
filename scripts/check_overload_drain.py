"""Check real recorded drain transitions against independent depth accounting.

Uses temporary ledgers. Strategy evaluation is disabled to isolate ingestion;
these timings do not measure strategy throughput or trading performance.
"""

import argparse
import importlib.util
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from btc15.config import Strategy
from btc15.engine import Engine
from btc15.storage import Store, read_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--drains", type=int, default=3)
    parser.add_argument("--baseline-engine", help="Saved engine module; report its failures for comparison")
    args = parser.parse_args()
    if args.drains < 1:
        parser.error("--drains must be positive")
    engine_class = Engine
    if args.baseline_engine:
        spec = importlib.util.spec_from_file_location("btc15._drain_baseline", args.baseline_engine)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine_class = module.Engine
    expected = {}
    total = suspended_deltas = rejected = mismatches = 0
    drain_seconds = 0
    draining, completed = set(), set()
    first_suspended = None
    with tempfile.TemporaryDirectory(prefix="btc15-recorded-drain-") as tmp:
        store = Store("sqlite:///" + str(Path(tmp) / "replay.db"))
        try:
            engine = engine_class(
                store, Strategy.load(args.config), "PAPER", execute=False, record_evaluations=False
            )
            engine.process = lambda *a: None
            for row in read_events(args.input):
                total += 1
                payload = row["payload"]
                kind, msg = payload.get("type"), payload.get("msg", {})
                suspended = row.get("analysis_suspended", False)
                connection = row.get("connection_id")
                if suspended and kind == "orderbook_delta":
                    draining.add(connection)
                    first_suspended = first_suspended or row["received"]
                ticker = msg.get("market_ticker")
                if kind == "orderbook_snapshot":
                    # Keep both sides in the source's YES-price coordinate system.
                    expected[ticker] = {
                        side: {
                            Decimal(p): Decimal(q)
                            for p, q in (msg.get(side + "_dollars_fp") or [])
                            if Decimal(q) > 0
                        }
                        for side in ("yes", "no")
                    }
                elif kind == "orderbook_delta" and ticker in expected:
                    levels = expected[ticker][msg["side"]]
                    price = Decimal(msg["price_dollars"])
                    quantity = levels.get(price, Decimal(0)) + Decimal(msg["delta_fp"])
                    assert quantity >= 0, "Source depth became negative"
                    if quantity:
                        levels[price] = quantity
                    else:
                        levels.pop(price, None)
                    suspended_deltas += bool(suspended)
                started = time.perf_counter()
                valid = engine.ingest(row)
                elapsed = time.perf_counter() - started
                if suspended:
                    drain_seconds += elapsed
                if kind in ("orderbook_snapshot", "orderbook_delta") and ticker in engine.books:
                    rejected += not valid
                    book = engine.books[ticker]
                    levels = expected[ticker]
                    mismatches += book.yes != levels["yes"] or book.no != {
                        1 - p: q for p, q in levels["no"].items()
                    }
                if kind == "disconnect" and connection in draining:
                    completed.add(connection)
                    if len(completed) >= args.drains:
                        break
            result = dict(
                events=total,
                completed_drains=len(completed),
                suspended_deltas=suspended_deltas,
                rejected_book_events=rejected,
                depth_mismatches=mismatches,
                drain_processing_seconds=drain_seconds,
                first_suspended=first_suspended,
                fills=len(store.list(kind="fill")),
                positions=engine.executor.snapshot()["positions"],
            )
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
            assert len(completed) == args.drains and suspended_deltas > 0, (
                "Insufficient recorded drain coverage"
            )
            if not args.baseline_engine:
                assert rejected == mismatches == 0, "Recorded book continuity failed"
            assert not result["fills"] and not result["positions"]
        finally:
            store.engine.dispose()


if __name__ == "__main__":
    main()
