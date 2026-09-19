import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from btc15.dashboard import create_app
from btc15.live_fallback import LiveFallbackStore
from btc15.storage import Store


@pytest.fixture
def fallback(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "paper.db"))
    journal = tmp_path / "manual-orders.sqlite"
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TABLE manual_orders (id TEXT PRIMARY KEY, body TEXT)")
    view = LiveFallbackStore(store, journal, "sol-paper", "SOL")

    def save(ident, action, qty, paid, fees="0.1", origin="bot"):
        at = 100 if action == "buy" else 110
        row = dict(
            id=ident,
            origin=origin,
            state="complete",
            created_at=at,
            request=dict(ticker="KXSOL15M-test", side="no", action=action),
            automation_reason="HARD_STOP" if action == "sell" else "STRATEGY_ENTRY",
            exchange_order=dict(
                fill_count_fp=str(qty),
                maker_fill_cost_dollars="0",
                taker_fill_cost_dollars=str(paid),
                maker_fees_dollars="0",
                taker_fees_dollars=fees,
                outcome_side="no" if action == "buy" else "yes",
            ),
        )
        with sqlite3.connect(journal) as db:
            db.execute("INSERT OR REPLACE INTO manual_orders VALUES (?,?)", (ident, json.dumps(row)))

    yield view, store, save
    view.close_history()
    store.engine.dispose()


def test_cached_history_detects_updates_to_existing_orders(fallback, monkeypatch):
    view, store, save = fallback
    save("buy", "buy", 4, 3.6)
    original = view._fallback
    rebuilds = []

    def rebuild():
        rebuilds.append(True)
        return original()

    monkeypatch.setattr(view, "_fallback", rebuild)
    first = view.fallback()
    assert view.fallback() is first
    assert len(rebuilds) == 1

    # Reconciliation changes an existing order without increasing the row count.
    save("buy", "buy", 10, 9)
    updated = view.fallback()
    assert updated != first
    assert len(rebuilds) == 2
    assert view.fallback() is updated

    store.add("settlement", dict(result="no"), "sol-paper", "PAPER", 200, "KXSOL15M-test")
    assert view.list("trade_result")[0]["body"]["net_pnl"] == pytest.approx(0.9)
    assert len(rebuilds) == 3


def test_cleared_history_preserves_open_positions_and_daily_loss_accounting(fallback):
    from btc15.live_loss_guard import daily_pnl

    view, store, save = fallback
    save("buy", "buy", 10, 9)
    with sqlite3.connect(view.journal) as db:
        row = json.loads(db.execute("SELECT body FROM manual_orders WHERE id='buy'").fetchone()[0])
        row["dashboard_history_cleared"] = True
        db.execute("UPDATE manual_orders SET body=? WHERE id='buy'", (json.dumps(row),))
    # A reset marker must never conceal a remaining position.
    assert len(view.list("fill")) == 1
    save("sell", "sell", 10, 6)
    assert view.list("fill") == []
    assert view.list("trade_result") == []
    with sqlite3.connect(view.journal) as db:
        orders = [json.loads(body) for body, in db.execute("SELECT body FROM manual_orders")]
    assert float(daily_pnl(orders, {}, 200)) == pytest.approx(-5.2)
    # Subsequent unmarked trades are still displayed.
    save("buy", "buy", 10, 9)
    assert len(view.list("trade_result")) == 1


def test_live_open_partial_and_closed_accounting(fallback):
    view, store, save = fallback
    save("buy", "buy", 10, 9)
    save("sell1", "sell", 4, 1.6)
    assert not view.list("trade_result")
    assert len(view.list("fill")) == 2
    app = create_app(view)
    with TestClient(app) as client:
        data = client.get("/api/trades?include_open=true").json()
        b = data["rows"][0]["body"]
        assert b["source"] == "LIVE_FALLBACK" and b["quantity"] == 6 and b["proceeds"] == 2.4
    save("sell2", "sell", 6, 2.4)
    rows = view.list("trade_result")
    assert len(rows) == 1
    assert rows[0]["body"]["proceeds"] == 6
    assert rows[0]["body"]["net_pnl"] == pytest.approx(-3.3)
    assert not store.list("fill") and not store.list("trade_result")
    assert not view.list("fill", mode="BACKTEST")
    assert not view.list("fill", run_id="other")
    assert len(LiveFallbackStore(store, view.journal, "sol-paper", "SOL").list("trade_result")) == 1


def test_paper_purchase_wins_and_no_duplicates(fallback):
    view, store, save = fallback
    save("buy", "buy", 10, 9)
    assert len(view.list("fill")) == 1
    store.add(
        "fill",
        dict(action="buy", quantity=10, price=0.9, side="no", fee=0.1),
        "sol-paper",
        "PAPER",
        101,
        "KXSOL15M-test",
        "paper-op",
    )
    assert len(view.list("fill")) == 1
    assert view.list("fill")[0]["body"].get("source") is None


def test_zero_fills_manual_purchases_and_uncertain_are_not_invented(fallback):
    view, store, save = fallback
    save("empty", "buy", 0, 0)
    assert not view.list("fill")
    save("manual", "buy", 10, 9, origin="manual")
    assert not view.list("fill")
    save("bot", "buy", 10, 9)
    assert not view.list("fill")  # Mixed manual/bot inventory cannot be allocated.


def test_settlement_closes_only_on_recorded_outcome(fallback):
    view, store, save = fallback
    save("buy", "buy", 10, 9)
    assert not view.list("trade_result")
    store.add("settlement", dict(result="no"), "sol-paper", "PAPER", 200, "KXSOL15M-test")
    b = view.list("trade_result")[0]["body"]
    assert b["net_pnl"] == pytest.approx(0.9)
    assert b["reason"] == "SETTLEMENT"


def test_history_snapshot_freezes_live_fill_reads(fallback):
    view, _, save = fallback
    save("buy", "buy", 10, 8)
    with view.history_snapshot():
        before = view.fallback()
        save("sell", "sell", 10, 3)
        assert view.fallback() == before
        assert not any(r["kind"] == "trade_result" for r in before)
    assert any(r["kind"] == "trade_result" for r in view.fallback())


def test_history_snapshot_does_not_mix_paper_commits(fallback):
    view, store, _ = fallback
    writer = Store(str(store.engine.url))
    try:
        with view.history_snapshot():
            assert not view.list("trade_result", run_id=view.run_id, mode="PAPER")
            writer.add("trade_result", dict(net_pnl=1), view.run_id, "PAPER", 120, "new-market", "new-trade")
            assert not view.list("trade_result", run_id=view.run_id, mode="PAPER")
        assert len(view.list("trade_result", run_id=view.run_id, mode="PAPER")) == 1
    finally:
        writer.engine.dispose()
