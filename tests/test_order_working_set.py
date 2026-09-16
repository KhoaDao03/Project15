import json

from btc15.manual_trading import ManualTrading


def test_filtered_reads_preserve_old_obligations_and_refresh_after_save(tmp_path):
    manual = ManualTrading(tmp_path / "orders.sqlite")
    records = [
        dict(
            id=str(n),
            origin="bot" if n % 2 else "manual",
            state="unknown" if n == 0 else "complete",
            request=dict(ticker="old" if n < 98 else "current", action="buy", side="yes"),
            exchange_order=dict(fill_count_fp="0.25"),
        )
        for n in range(100)
    ]
    with manual.db() as db:
        db.executemany("INSERT INTO manual_orders VALUES (?,?)", [(r["id"], json.dumps(r)) for r in records])
    assert [r["id"] for r in manual.rows(unresolved=True)] == ["0"]
    assert [r["id"] for r in manual.rows(ticker="current", origin="bot")] == ["99"]
    assert manual.rows(tickers=[]) == []
    assert manual.rows(tickers=["current"]) == manual.rows(ticker="current")
    assert len(manual.rows(limit=50)) == 50
    assert manual.row("missing") is None
    assert manual.purchases()["old"] == dict(yes="24.50", no="0", pending=1)
    row = manual.row("0")
    row["state"] = "complete"
    manual.save(row)
    assert manual.rows(unresolved=True) == []
    assert manual.purchases()["old"]["pending"] == 0
    with manual.db() as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT body FROM manual_orders WHERE json_extract(body, '$.request.ticker')=?",
            ("current",),
        ).fetchall()
    assert any("manual_orders_ticker" in r[3] for r in plan)
