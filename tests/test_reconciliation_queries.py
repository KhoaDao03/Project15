import json

import pytest
from test_live_automation import live  # noqa: F401
from test_manual_trading import venue  # noqa: F401


@pytest.mark.anyio
async def test_reconcile_scopes_controls_to_pending_tickers(live, monkeypatch):  # noqa: F811
    worker, _, manual, control, _, _ = live
    with manual.db() as db:
        for i in range(100):
            body = {**control, "ticker": f"old-{i}", "close_time": 0}
            db.execute("INSERT INTO live_controls VALUES (?,?)", (body["ticker"], json.dumps(body)))
        for ident, ticker in (("one", control["ticker"]), ("two", "missing-control")):
            row = dict(id=ident, state="unknown", request=dict(ticker=ticker), updated_at=0)
            db.execute("INSERT INTO manual_orders VALUES (?,?)", (ident, json.dumps(row)))
    scopes, calls = [], []
    original = worker.controls

    def controls(tickers=None):
        assert tickers is not None
        scopes.append(tickers)
        return original(tickers)

    async def reconcile(client, row):
        calls.append(row["id"])

    monkeypatch.setattr(worker, "controls", controls)
    monkeypatch.setattr(manual, "reconcile", reconcile)
    assert await worker.reconcile()
    assert scopes == [["missing-control", control["ticker"]]]
    assert calls == ["two", "one"]

    # Recheck the order immediately before acting, including updates after selection.
    def changed(tickers=None):
        result = controls(tickers)
        with manual.db() as db:
            db.execute("UPDATE manual_orders SET body=json_set(body,'$.state','complete') WHERE id='one'")
        return result

    calls.clear()
    monkeypatch.setattr(worker, "controls", changed)
    await worker.reconcile()
    assert calls == ["two"]
