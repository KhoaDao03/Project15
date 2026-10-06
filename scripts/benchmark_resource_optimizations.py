"""Offline CPU/allocation checks using synthetic data; never opens the production database."""

import copy
import gc
import json
import math
import sqlite3
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

from btc15 import order_history
from btc15.config import Strategy
from btc15.engine import Engine
from btc15.strategies.settlement_edge.model import Tick, features


def timed(function, samples=15):
    times = []
    for _ in range(samples):
        wall, cpu = time.perf_counter(), time.process_time()
        function()
        times.append(((time.perf_counter() - wall) * 1000, (time.process_time() - cpu) * 1000))
    return dict(
        wall_ms=round(statistics.median(t[0] for t in times), 3),
        cpu_ms=round(statistics.median(t[1] for t in times), 3),
    )


def main():
    assets = ["BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "DOGE"]
    with tempfile.TemporaryDirectory(prefix="resource-optimization-") as root:
        databases = []
        rows = [
            (
                str(i),
                json.dumps(
                    dict(
                        id=str(i),
                        request=dict(ticker=f"KX{assets[i % 7]}15M-{i:05}"),
                        state="accepted",
                        exchange_order=dict(fill_count_fp="1"),
                        diagnostic="x" * 512,
                    )
                ),
            )
            for i in range(8694)
        ]
        for version in ("before", "after"):
            db = sqlite3.connect(Path(root) / (version + ".sqlite"))
            db.execute("CREATE TABLE manual_orders(id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            db.execute(
                "CREATE INDEX manual_orders_ticker ON manual_orders(json_extract(body,'$.request.ticker'))"
            )
            db.execute("CREATE INDEX manual_orders_state ON manual_orders(json_extract(body,'$.state'))")
            db.executemany("INSERT INTO manual_orders VALUES (?,?)", rows)
            db.commit()
            if version == "after":
                order_history.initialize(db)
                db.commit()
            databases.append(db)

        def read(db):
            return [
                db.execute(
                    "SELECT body FROM manual_orders WHERE json_extract(body,'$.request.ticker') "
                    "LIKE ? ORDER BY rowid",
                    ("KX" + a + "15M-%",),
                ).fetchall()
                for a in assets
            ]

        assert read(databases[0]) == read(databases[1])
        reads = {label: timed(lambda db=db: read(db), 7) for label, db in zip(("before", "after"), databases)}
        writes = {}
        for label, db in zip(("before", "after"), databases):
            n = [0]

            def write():
                n[0] += 1
                with db:
                    db.execute(
                        "UPDATE manual_orders SET body=json_set(body,'$.exchange_order.fill_count_fp',?) "
                        "WHERE id=?",
                        (str(n[0]), str(n[0] % 100)),
                    )

            writes[label] = timed(write, 100)
        for db in databases:
            db.close()

    now = 1800000000.0
    ticks = [Tick(now - 3599 + i, now - 3599 + i, 90000 + 100 * math.sin(i / 31)) for i in range(3600)]
    config = Strategy()

    def independent():
        return [features(ticks, now, config) for _ in range(3)]

    def shared():
        base = features(ticks, now, config)
        return [copy.deepcopy(base) for _ in range(3)]

    assert independent() == shared()
    calculations = dict(before=timed(independent), after=timed(shared))

    # Measure Python allocations retained by the actual cleanup routine. This
    # deliberately excludes authoritative books/contracts, which must be kept.
    gc.collect()
    tracemalloc.start()
    engine = Engine.__new__(Engine)
    engine.executor = SimpleNamespace(positions={}, orders={}, quarantines={})
    engine._settled_latest = None
    names = (
        "_model_cache",
        "_lead_history",
        "_decision_keys",
        "_signal_quote_inputs",
        "_management_gaps",
        "last_evaluation",
    )
    for name in names:
        setattr(engine, name, {})
    engine.latest = {}
    template = features(ticks, now, config)
    for i in range(300):
        ticker = f"settled-{i}"
        f = copy.deepcopy(template)
        engine._model_cache[ticker] = (now, None, f, {}, None, ticks[-1])
        engine.latest[ticker] = dict(timestamp=now + i, features=f)
        engine._lead_history[ticker] = [dict(source=now)]
    del f
    retained_before = tracemalloc.get_traced_memory()[0]
    for ticker in list(engine.latest):
        engine._release_settled_cache(ticker)
    gc.collect()
    retained_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    print(
        json.dumps(
            dict(
                synthetic_order_rows=len(rows),
                identical_read_bodies_and_order=True,
                seven_history_reads=reads,
                committed_single_order_updates=writes,
                identical_features=True,
                three_market_features=calculations,
                retired_market_cache_allocations=dict(
                    markets=300,
                    before_bytes=retained_before,
                    after_bytes=retained_after,
                    retained_latest=len(engine.latest),
                    remaining_model_cache=len(engine._model_cache),
                ),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
