"""Bounded endurance and crash checks using temporary databases only."""

import gc
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_live_fallback import fallback  # noqa: F401

from btc15 import order_history
from btc15.engine import Engine


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_concurrent_history_reads_writes_and_reopens(fallback, journal_mode):  # noqa: F811
    view, _, save = fallback
    save("buy", "buy", 4, 3.6)
    with sqlite3.connect(view.journal) as db:
        assert db.execute("PRAGMA journal_mode=" + journal_mode).fetchone()[0].upper() == journal_mode
        order_history.initialize(db)
    stop = Event()

    def read():
        count = 0
        while not stop.is_set():
            rows = view.fallback()
            assert rows and rows[0]["body"]["quantity"] > 0
            count += 1
        return count

    with ThreadPoolExecutor(max_workers=3) as pool:
        readers = [pool.submit(read) for _ in range(3)]
        try:
            with sqlite3.connect(view.journal, timeout=10) as db:
                for n in range(4, 304):
                    with db:
                        db.execute(
                            "UPDATE manual_orders SET body=json_set(body,'$.exchange_order.fill_count_fp',?)",
                            (str(n),),
                        )
                    if n % 50 == 0:
                        view.close_history()
            assert view.fallback()[0]["body"]["quantity"] == 303
        finally:
            stop.set()
        assert all(f.result(timeout=15) > 0 for f in readers)
    gc.collect()
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(100):
        view.close_history()
        assert view.fallback()[0]["body"]["quantity"] == 303
    view.close_history()
    gc.collect()
    assert len(os.listdir("/proc/self/fd")) <= before + 1
    with sqlite3.connect(view.journal) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("commit", [False, True])
def test_abrupt_writer_exit_keeps_order_and_revision_atomic(fallback, commit):  # noqa: F811
    view, _, save = fallback
    save("buy", "buy", 4, 3.6)
    with sqlite3.connect(view.journal) as db:
        order_history.initialize(db)
    prior = view.history_revision("PAPER")
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE manual_orders SET body=json_set(body,'$.exchange_order.fill_count_fp','9')")
if sys.argv[2] == 'True':
    db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(view.journal), str(commit)], check=True, timeout=15)
    assert (view.history_revision("PAPER") != prior) is commit
    assert view.fallback()[0]["body"]["quantity"] == (9 if commit else 4)
    view.close_history()
    assert view.fallback()[0]["body"]["quantity"] == (9 if commit else 4)


def test_10000_settlements_bound_derived_caches(store, config):
    engine = Engine(store, config, execute=False, record_evaluations=False)
    caches = [
        engine._model_cache,
        engine._lead_history,
        engine._decision_keys,
        engine._signal_quote_inputs,
        engine._management_gaps,
        engine.last_evaluation,
    ]
    for n in range(10000):
        ticker = f"settled-{n}"
        for cache in caches:
            cache[ticker] = {"derived": n}
        engine.latest[ticker] = {"timestamp": n}
        # Settle the prior market again to simulate delayed duplicate evidence.
        if n:
            engine._release_settled_cache(f"settled-{n - 1}")
        engine._release_settled_cache(ticker)
        assert all(not cache for cache in caches)
        assert len(engine.latest) == 1
    assert engine.latest == {"settled-9999": {"timestamp": 9999}}
