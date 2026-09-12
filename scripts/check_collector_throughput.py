"""Replay a prepared incident interval in an isolated ledger; no network or live writes."""

import argparse
import cProfile
import io
import json
import pstats
import resource
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from btc15.config import Strategy
from btc15.domain import Book, D, dumps
from btc15.engine import Engine
from btc15.recovery import restore_market
from btc15.storage import CompactRecorder, Store, states
from btc15.strategies.settlement_edge.model import Tick

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture", required=True)
parser.add_argument("--checkpoint", required=True)
parser.add_argument("--config", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--profile", action="store_true")
parser.add_argument("--profile-output", help="Write cProfile statistics for offline analysis")
parser.add_argument("--shadow", action="store_true", help="Include shadow processing in the isolated replay")
parser.add_argument("--historical-markets", type=int, default=0)
parser.add_argument("--min-rate", type=float, default=0)
parser.add_argument("--compare", help="Prior output whose portfolio and fill results must match")
args = parser.parse_args()
initial = json.loads(Path(args.fixture).read_text())
snap = json.loads(Path(args.checkpoint).read_text())
rows = initial["rows"]
clock = [rows[0]["received"]]
with tempfile.TemporaryDirectory(prefix="btc15-burst-") as tmp:
    store = Store("sqlite:///" + str(Path(tmp) / "replay.db"))
    e = Engine(
        store,
        Strategy.load(args.config),
        "PAPER",
        run_id="settlement-ioc-v1",
        clock=lambda: clock[0],
        record_evaluations=False,
    )
    e.executor.restore(snap)
    for ticker in snap["positions"]:
        e.markets[ticker] = restore_market(snap["contracts"][ticker])
        for state in (
            "DISCOVER_MARKET",
            "VALIDATE_MARKET",
            "WARMUP",
            "ENTRY_WINDOW",
            "EVALUATING",
            "TRADE_CANDIDATE",
            "ORDER_PENDING",
            "POSITION_OPEN",
        ):
            e.state(ticker, state, clock[0])
        b = initial["books"][ticker]
        e.books[ticker] = Book(
            yes={D(p): D(q) for p, q in b["yes"]},
            no={D(p): D(q) for p, q in b["no"]},
            received=b["received"],
            source_time=b["source_time"],
            valid=b["valid"],
        )
    if args.historical_markets:
        template = next(iter(e.markets.values()))
        with store.transaction() as conn:
            for i in range(args.historical_markets):
                ticker = f"historical-{i}"
                e.markets[ticker] = replace(template, ticker=ticker, close_time=clock[0] - 900)
                e.books[ticker] = Book()
                conn.execute(
                    states.insert().values(run_id=e.run_id, market=ticker, state="CLOSED", version=1)
                )
    e.ticks = [Tick(**r) for r in initial["ticks"]]
    metadata = initial["metadata"]
    e.series_fees = metadata["series"]
    e.series_fee_changes = metadata["series_fee_changes"]
    e.fee_changes = metadata["fee_changes"]
    e.healthy = e.clock_ok = e.exchange_open = True
    e.connection = rows[0]["connection_id"]
    recorder = CompactRecorder(Path(tmp) / "raw")
    shadow = None
    if args.shadow:
        from btc15.stop_shadow import StopShadow

        shadow = StopShadow(e, Path(tmp) / "shadow.db")
        # The fixture predates shadow recording. Seed identical held exposure for
        # a processing-load scenario, not evidence of historical shadow returns.
        shadow.executor.restore({**snap, "mode": "BACKTEST"})
        shadow.connection = e.connection

    def run():
        for offset in range(0, len(rows), 256):
            batch = rows[offset : offset + 256]
            recorder.append_rows(batch)
            for row in batch:
                clock[0] = row["received"]
                assert e.ingest(row), row["id"]
                if shadow is not None:
                    payload = row["payload"]
                    shadow.process(row, json.loads(payload) if isinstance(payload, str) else payload)
                    assert not shadow.failed, "Shadow processing failed"
        recorder.close()
        if hasattr(e, "flush_rejections"):
            e.flush_rejections(clock[0], force=True)

    start = time.perf_counter()
    if args.profile or args.profile_output:
        p = cProfile.Profile()
        p.runcall(run)
        if args.profile_output:
            p.dump_stats(args.profile_output)
        out = io.StringIO()
        pstats.Stats(p, stream=out).sort_stats("cumulative").print_stats(22)
        print(out.getvalue())
    else:
        run()
    elapsed = time.perf_counter() - start
    output = dict(
        ledger_bytes=sum(
            len(dumps(r["body"]).encode())
            for r in store.list(limit=None)
            if r["kind"] not in ("run", "resume")
        ),
        raw_bytes=sum(p.stat().st_size for p in (Path(tmp) / "raw").rglob("*") if p.is_file()),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        events=len(rows),
        shadow_enabled=args.shadow,
        elapsed=elapsed,
        events_per_second=len(rows) / elapsed,
        snapshot=e.executor.snapshot(),
        audit_records=[
            {k: r[k] for k in ("kind", "timestamp", "market", "body")}
            for r in store.list(mode="PAPER", limit=None)
            if r["kind"]
            in (
                "exit_intent",
                "position_monitoring",
                "entry_rejection_summary",
                "hold_to_settlement_comparison",
            )
        ],
        records=[
            {k: r[k] for k in ("kind", "timestamp", "market", "body")}
            for r in store.list(mode="PAPER", limit=None)
            if r["kind"] in ("order", "fill", "trade_result")
        ],
    )
    if shadow is not None:
        output["shadow_snapshot"] = shadow.executor.snapshot()
        output["shadow_records"] = [
            {k: r[k] for k in ("kind", "timestamp", "market", "body")}
            for r in shadow.store.list(limit=None)
            if r["kind"] not in ("run", "resume")
        ]
    Path(args.output).write_text(dumps(output))
    print({k: output[k] for k in ("events", "elapsed", "events_per_second")})
    store.engine.dispose()
    if shadow is not None:
        shadow.store.engine.dispose()

if args.compare:
    previous = json.loads(Path(args.compare).read_text())
    assert previous["snapshot"] == json.loads(dumps(output["snapshot"])), "Portfolio changed"

    def sort_key(row):
        return json.dumps(row, sort_keys=True)

    assert sorted(previous["records"], key=sort_key) == sorted(output["records"], key=sort_key), (
        "Execution results changed"
    )
    if "audit_records" in previous:
        assert sorted(previous["audit_records"], key=sort_key) == sorted(
            output["audit_records"], key=sort_key
        ), "Audit records changed"
    if "shadow_snapshot" in previous:
        assert previous["shadow_snapshot"] == json.loads(dumps(output.get("shadow_snapshot"))), (
            "Shadow portfolio changed"
        )
        assert sorted(previous["shadow_records"], key=sort_key) == sorted(
            output["shadow_records"], key=sort_key
        ), "Shadow records changed"
    print("Portfolio, execution results, and audit records match the comparison run")
    if "shadow_snapshot" in previous:
        print("Shadow portfolio and records match the comparison run")
if output["events_per_second"] < args.min_rate:
    raise SystemExit("Processing rate is below the required threshold")
