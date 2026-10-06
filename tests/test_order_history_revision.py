import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_live_fallback import fallback  # noqa: F401

from btc15 import order_history
from btc15.global_loss_guard import GlobalLossGuard
from btc15.live_fallback import LiveFallbackStore
from btc15.manual_trading import ManualTrading


def migrate(view):
    with sqlite3.connect(view.journal) as db:
        order_history.initialize(db)


def test_only_relevant_asset_rebuilds_and_read_only_migration_is_optional(fallback):  # noqa: F811
    view, store, save = fallback
    save("buy", "buy", 4, 3.6)
    before_migration = view.fallback()
    migrate(view)
    first = view.fallback()
    assert first == before_migration
    other = LiveFallbackStore(store, view.journal, "eth-paper", "ETH")
    try:
        other_before = other.history_revision("PAPER")
        with sqlite3.connect(view.journal) as db:
            db.execute("CREATE TABLE unrelated(body TEXT)")
        # A schema change deliberately invalidates once; ordinary unrelated writes do not.
        first = view.fallback()
        with sqlite3.connect(view.journal) as db:
            db.execute("INSERT INTO unrelated VALUES ('control changed')")
        assert view.fallback() is first
        with sqlite3.connect(view.journal) as db:
            db.execute(
                "INSERT INTO manual_orders VALUES ('eth', ?)",
                (json.dumps(dict(request=dict(ticker="KXETH15M-x"))),),
            )
        assert view.fallback() is first
        assert other.history_revision("PAPER") != other_before
        save("buy", "buy", 10, 9)
        assert view.fallback() != first
        stable = view.fallback()
        store.add("settlement", dict(result="no"), "sol-paper", "PAPER", 200, "KXSOL15M-test")
        assert view.fallback() != stable
    finally:
        other.close_history()


@pytest.mark.parametrize("write", ["update", "replace"])
def test_rehoming_invalidates_both_assets_even_without_recursive_triggers(fallback, write):  # noqa: F811
    view, store, save = fallback
    migrate(view)
    save("buy", "buy", 4, 3.6)
    other = LiveFallbackStore(store, view.journal, "eth-paper", "ETH")
    try:
        old_sol, old_eth = view.history_revision("PAPER"), other.history_revision("PAPER")
        with sqlite3.connect(view.journal) as db:
            db.execute("PRAGMA recursive_triggers=OFF")
            body = json.loads(db.execute("SELECT body FROM manual_orders WHERE id='buy'").fetchone()[0])
            body["request"]["ticker"] = "kxeth15m-test"
            if write == "update":
                db.execute("UPDATE manual_orders SET body=? WHERE id='buy'", (json.dumps(body),))
            else:
                db.execute("INSERT OR REPLACE INTO manual_orders VALUES ('buy',?)", (json.dumps(body),))
        assert view.history_revision("PAPER") != old_sol
        assert other.history_revision("PAPER") != old_eth
        assert view.fallback() == []
        assert len(other.fallback()) == 1
    finally:
        other.close_history()


def test_noop_rollback_delete_and_missing_trigger(fallback):  # noqa: F811
    view, _, save = fallback
    migrate(view)
    save("buy", "buy", 4, 3.6)
    first = view.fallback()
    revision = view.history_revision("PAPER")
    with sqlite3.connect(view.journal) as db:
        db.execute("UPDATE manual_orders SET body=body")
    assert view.history_revision("PAPER") == revision
    with sqlite3.connect(view.journal) as db:
        db.execute(
            "UPDATE manual_orders SET body=json_set(body,'$.message','reconciled','$.updated_at',123,'$.state','accepted')"
        )
    assert view.history_revision("PAPER") == revision
    assert view.fallback() is first
    with sqlite3.connect(view.journal) as db:
        db.execute("DELETE FROM manual_orders")
        db.rollback()
    assert view.fallback() is first
    with sqlite3.connect(view.journal) as db:
        db.execute("DELETE FROM manual_orders")
    assert view.fallback() == []
    with sqlite3.connect(view.journal) as db:
        db.execute("DROP TRIGGER manual_history_update")
    save("buy", "buy", 4, 3.6)
    first = view.fallback()
    with sqlite3.connect(view.journal) as db:
        db.execute("UPDATE manual_orders SET body=json_set(body,'$.exchange_order.fill_count_fp','2')")
    assert view.fallback() != first  # Falls back to database-wide invalidation.


def test_history_prefix_uses_index_and_preserves_row_order(tmp_path):
    manual = ManualTrading(tmp_path / "orders.sqlite", settings=SimpleNamespace())
    with manual.db() as db:
        for i, ticker in enumerate(["KXSOL15M-z", "KXETH15M-x", "kxsol15m-a", "KXSOL15M-b"]):
            db.execute(
                "INSERT INTO manual_orders VALUES (?,?)",
                (str(i), json.dumps(dict(request=dict(ticker=ticker)))),
            )
        query = (
            "SELECT id FROM manual_orders WHERE json_extract(body,'$.request.ticker') LIKE ? ORDER BY rowid"
        )
        assert db.execute(query, ("KXSOL15M-%",)).fetchall() == [("0",), ("2",), ("3",)]
        plan = db.execute("EXPLAIN QUERY PLAN " + query, ("KXSOL15M-%",)).fetchall()
        assert any("manual_orders_ticker_prefix" in r[3] and "SEARCH" in r[3] for r in plan)
        order_history.initialize(db)  # Repeated startup is safe and retains revisions.
        assert (
            db.execute("SELECT revision FROM manual_history_revisions WHERE prefix='KXSOL15M'").fetchone()[0]
            == 3
        )


def test_loss_guard_refreshes_real_accounting_but_not_diagnostics(fallback):  # noqa: F811
    view, store, save = fallback
    migrate(view)
    save("buy", "buy", 4, 3.6)
    guard = GlobalLossGuard.__new__(GlobalLossGuard)
    guard.histories = {"SOL": view}
    guard.members = {"SOL": {"run_id": "sol-paper"}}
    guard.cache = {}
    assert guard.calculate() == 0
    store.add("settlement", dict(result="no"), "sol-paper", "PAPER", 200, "KXSOL15M-test")
    assert guard.calculate() == 0.3
    cached = guard.cache["SOL"]
    with sqlite3.connect(view.journal) as db:
        db.execute("UPDATE manual_orders SET body=json_set(body,'$.message','reconciled','$.updated_at',300)")
    assert guard.calculate() == 0.3
    assert guard.cache["SOL"] is cached
    with sqlite3.connect(view.journal) as db:
        db.execute(
            "UPDATE manual_orders SET body=json_set(body,'$.exchange_order.taker_fill_cost_dollars','3')"
        )
    assert guard.calculate() == 0.9
    with sqlite3.connect(view.journal) as db:
        db.execute("UPDATE manual_orders SET body=json_set(body,'$.dashboard_history_cleared',json('true'))")
    assert guard.calculate() == 0
