import json

import pytest
from test_live_automation import live  # noqa: F401
from test_manual_trading import venue  # noqa: F401


def test_five_markets_per_asset_preserves_history(live):  # noqa: F811
    worker, _, manual, original, _, clock = live
    with manual.db() as db:
        db.execute("DELETE FROM live_controls")
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        for n in range(12):
            worker.write(
                {**original, "ticker": f"{asset}-{n:02}", "asset": asset, "close_time": clock[0] - 1200 + n}
            )
    selected = worker.cycle_controls()
    assert len(selected) == 20
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        assert {c["ticker"] for c in selected if c["asset"] == asset} == {
            f"{asset}-{n:02}" for n in range(7, 12)
        }
    assert len(worker.controls()) == 48


def test_older_obligations_are_not_dropped(live):  # noqa: F811
    worker, _, manual, original, _, clock = live
    with manual.db() as db:
        db.execute("DELETE FROM live_controls")
    for n in range(8):
        worker.write({**original, "ticker": f"ETH-{n}", "close_time": clock[0] + 60 + n})
    with manual.db() as db:
        for n, state, action, qty in [
            (0, "complete", "buy", "10"),
            (1, "unknown", "buy", "0"),
            (2, "complete", "buy", "10"),
            (2, "complete", "sell", "10"),
        ]:
            row = dict(
                id=f"{n}-{action}",
                origin="bot",
                state=state,
                request=dict(ticker=f"ETH-{n}", action=action),
                exchange_order=dict(fill_count_fp=qty),
            )
            db.execute("INSERT INTO manual_orders VALUES (?,?)", (row["id"], json.dumps(row)))
    assert {c["ticker"] for c in worker.cycle_controls()} == {f"ETH-{n}" for n in (0, 1, 3, 4, 5, 6, 7)}


@pytest.mark.anyio
async def test_expired_market_uses_point_lookup(live, monkeypatch):  # noqa: F811
    worker, _, _, original, _, clock = live
    expired = {**original, "close_time": clock[0] - 1}
    worker.write(expired)

    def fail():
        raise AssertionError("Full control scan in per-market processing")

    monkeypatch.setattr(worker, "controls", fail)
    await worker.step_market(expired)
    assert worker.control(expired["ticker"])["close_time"] == expired["close_time"]
