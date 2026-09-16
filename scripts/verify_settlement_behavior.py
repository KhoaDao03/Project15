"""Compare Settlement Edge behavior across checkouts using identical synthetic inputs.

Run once before a refactor with --output baseline.json; run the changed checkout
with --output candidate.json --compare baseline.json using the same locked environment.
No network access, credentials, production database, or real-money orders are used.
"""

import argparse
import json
import tempfile
from pathlib import Path

from btc15.config import Strategy
from btc15.demo import generate
from btc15.engine import Engine
from btc15.storage import Store, read_events


def capture(config, tape, db):
    store = Store("sqlite:///" + str(db))
    try:
        engine = Engine(store, config, "BACKTEST", execute=True)
        for row in read_events(tape):
            assert engine.ingest(row), row["id"]
        report = {}
        for kind in ("opportunity", "order", "fill", "trade_result", "transition", "settlement", "health"):
            rows = []
            for row in store.list(kind=kind, run_id=engine.run_id, limit=None):
                body = dict(row["body"])
                for field in ("id", "order_id", "opportunity_id", "trade_id", "versions"):
                    body.pop(field, None)
                rows.append([row["timestamp"], body])
            # Record UUIDs must not decide equivalence when several records share a time.
            report[kind] = sorted(rows, key=lambda r: (r[0], json.dumps(r[1], sort_keys=True)))
        report["risk"] = engine.executor.snapshot()["risk"]
        report["positions"] = engine.executor.snapshot()["positions"]
        return report
    finally:
        store.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--compare")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        tape = generate(root / "synthetic.jsonl")
        report = {
            name: capture(config, tape, root / (name + ".db"))
            for name, config in (
                ("original", Strategy()),
                ("moderate", Strategy.load("config/settlement-edge-paper-moderate.json")),
            )
        }
    Path(args.output).write_text(json.dumps(report, sort_keys=True))
    if args.compare:
        expected = json.loads(Path(args.compare).read_text())
        assert report == expected, "Settlement Edge behavior differs from the reference checkout"
    for name, result in report.items():
        print(
            json.dumps(
                {
                    "configuration": name,
                    "input": "SYNTHETIC",
                    "equivalent": bool(args.compare),
                    "evaluations": len(result["opportunity"]),
                    "fills": len(result["fill"]),
                    "completed_trades": len(result["trade_result"]),
                    "open_positions": len(result["positions"]),
                }
            )
        )


if __name__ == "__main__":
    main()
