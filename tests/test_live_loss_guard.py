# ruff: noqa: F811
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from test_live_automation import live, venue  # noqa: F401

from btc15.live_automation import LiveAutomation, LiveControl
from btc15.live_loss_guard import daily_pnl

DAY = 86400


def row(action, qty, paid, *, fee="0", at=DAY + 100, ticker="KXETH15M-test", side="yes"):
    return dict(
        request=dict(ticker=ticker, action=action, side=side),
        origin="bot",
        created_at=at,
        exchange_order=dict(
            fill_count_fp=str(qty),
            maker_fill_cost_dollars="0",
            taker_fill_cost_dollars=str(paid),
            maker_fees_dollars="0",
            taker_fees_dollars=fee,
            outcome_side=side if action == "buy" else ("no" if side == "yes" else "yes"),
        ),
    )


@pytest.mark.parametrize("side", ["yes", "no"])
def test_partial_sale_and_settlement_realize_cost_and_fees(side):
    rows = [row("buy", 10, 9, fee=".10", side=side), row("sell", 4, 2, fee=".02", side=side)]
    assert daily_pnl(rows, {}, DAY + 200) == Decimal("-1.66")
    settlement = {
        "KXETH15M-test": dict(timestamp=DAY + 150, body=dict(result="no" if side == "yes" else "yes"))
    }
    assert daily_pnl(rows, settlement, DAY + 200) == Decimal("-7.12")


def test_utc_day_and_open_inventory_are_not_unrealized_losses():
    rows = [row("buy", 10, 9, at=DAY - 100), row("sell", 5, 3, at=DAY - 50), row("sell", 5, 3, at=DAY + 10)]
    assert daily_pnl(rows, {}, DAY + 200) == Decimal("-2.5")
    assert daily_pnl([row("buy", 10, 9)], {}, DAY + 200) == 0
    assert daily_pnl(rows, {}, 2 * DAY + 200) == 0


def test_missing_economics_and_cross_day_sale_are_not_zero_loss():
    rows = [row("buy", 10, 9), row("sell", 10, 6)]
    del rows[1]["exchange_order"]["taker_fill_cost_dollars"]
    with pytest.raises(KeyError):
        daily_pnl(rows, {}, DAY + 200)
    sale = row("sell", 10, 6, at=DAY - 10)
    sale["exchange_order"]["last_update_time"] = datetime.fromtimestamp(DAY + 10, UTC).isoformat()
    with pytest.raises(ValueError, match="Cross-day"):
        daily_pnl([row("buy", 10, 9, at=DAY - 20), sale], {}, DAY + 200)


@pytest.mark.parametrize("live", ["ETH", "GOLD", "SILVER", "WTI"], indirect=True)
@pytest.mark.parametrize("pnl,trips", [("-19.99", False), ("-20", True), ("-20.01", True)])
def test_threshold_persists_and_only_disables_affected_asset(live, monkeypatch, pnl, trips):
    worker, _, manual, control, _, clock = live
    asset = control["asset"]
    worker.write_asset(dict(asset="BTC", enabled=True, revision=1, contracts=10))
    monkeypatch.setattr("btc15.live_automation.daily_pnl", lambda *args: Decimal(pnl))
    if not trips:
        worker.check_daily_loss(asset, clock[0])
        assert worker.assets()[asset]["enabled"]
        return
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        worker.check_daily_loss(asset, clock[0])
    assert not worker.assets()[asset]["enabled"]
    assert worker.assets()["BTC"]["enabled"]
    saved = worker.control(control["ticker"])
    assert not saved["enabled"] and not saved["paused"]
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    monkeypatch.setattr("btc15.live_automation.daily_pnl", lambda *args: Decimal("100"))
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        restarted.check_daily_loss(asset, clock[0])
    restarted.check_daily_loss(asset, clock[0] + DAY)
    assert not restarted.assets()[asset]["enabled"]  # No automatic rearm at midnight.


@pytest.mark.anyio
async def test_loss_latch_preserves_hard_stop_exits(live):
    worker, state, _, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    policy = worker.assets()["ETH"]
    policy.update(enabled=False, loss_guard=dict(day=int(clock[0] // DAY), pnl="-20"))
    worker.write_asset(policy)
    control.update(enabled=False)
    worker.write(control)
    data["bid"] = 0.55
    await worker.step_market(control)
    assert state["posts"][-1]["reduce_only"]
    assert state["posts"][-1]["time_in_force"] == "immediate_or_cancel"
    assert worker.control(control["ticker"])["exit_reason"] == "HARD_STOP"


def test_initial_enable_still_checks_loss_limit(live, monkeypatch):
    worker, _, _, control, _, _ = live
    monkeypatch.setattr("btc15.live_automation.daily_pnl", lambda *args: Decimal("-20"))
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        worker.configure(
            LiveControl(
                ticker=control["ticker"],
                enabled=True,
                contracts=10,
                revision=1,
                confirm="ENABLE_REAL_TRADING",
            )
        )
    assert not worker.assets()["ETH"]["enabled"]


@pytest.mark.parametrize("live", ["ETH", "GOLD", "SILVER", "WTI"], indirect=True)
def test_same_day_reenable_resets_baseline_and_retrips_after_restart(live, monkeypatch):
    worker, _, manual, control, _, clock = live
    asset = control["asset"]
    pnl = [Decimal("-21")]
    monkeypatch.setattr("btc15.live_automation.daily_pnl", lambda *args: pnl[0])
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        worker.check_daily_loss(asset, clock[0])

    def configure(enabled=True, confirm="ENABLE_REAL_TRADING"):
        return worker.configure(
            LiveControl(
                ticker=control["ticker"],
                enabled=enabled,
                contracts=10,
                revision=worker.assets()[asset]["revision"],
                confirm=confirm,
            )
        )

    # Applying disabled settings must retain the latch; confirmation is required.
    configure(enabled=False)
    with pytest.raises(HTTPException, match="Confirm enabling"):
        configure(confirm="")
    assert not worker.assets()[asset]["enabled"]
    assert "loss_guard" in worker.assets()[asset]
    configure()
    assert worker.assets()[asset]["enabled"]
    assert worker.control(control["ticker"])["enabled"]
    assert "loss_guard" not in worker.assets()[asset]
    assert worker.assets()[asset]["loss_guard_baseline"]["pnl"] == "-21"
    # Editing settings does not reset an already active budget.
    pnl[0] = Decimal("-30")
    configure()
    assert worker.assets()[asset]["loss_guard_baseline"]["pnl"] == "-21"
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    pnl[0] = Decimal("-40.99")
    restarted.check_daily_loss(asset, clock[0])
    pnl[0] = Decimal("-41")
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        restarted.check_daily_loss(asset, clock[0])
    configure()
    assert worker.assets()[asset]["loss_guard_baseline"]["pnl"] == "-41"
    # The previous day's baseline must not enlarge the next day's budget.
    pnl[0] = Decimal("-20")
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        restarted.check_daily_loss(asset, clock[0] + DAY)


def test_reenable_still_requires_verified_pnl(live, monkeypatch):
    worker, _, _, control, _, clock = live
    monkeypatch.setattr("btc15.live_automation.daily_pnl", lambda *args: Decimal("-20"))
    with pytest.raises(HTTPException, match="Daily live loss limit"):
        worker.check_daily_loss("ETH", clock[0])
    before = worker.assets()["ETH"]

    def unavailable(*args):
        raise ValueError("Incomplete accounting")

    monkeypatch.setattr("btc15.live_automation.daily_pnl", unavailable)
    with pytest.raises(HTTPException, match="P&L unavailable"):
        worker.configure(
            LiveControl(
                ticker=control["ticker"],
                enabled=True,
                contracts=10,
                revision=before["revision"],
                confirm="ENABLE_REAL_TRADING",
            )
        )
    assert worker.assets()["ETH"] == before


def test_multiple_markets_accumulate_exactly():
    rows = []
    for n in range(5):
        ticker = f"KXETH15M-{n}"
        rows.extend([row("buy", 10, 9, ticker=ticker), row("sell", 10, 5, ticker=ticker)])
    assert daily_pnl(rows, {}, DAY + 200) == Decimal("-20")


def test_missing_current_day_settlement_blocks_new_buys(live, monkeypatch):
    worker, _, manual, control, _, clock = live
    control["close_time"] = clock[0] - 1
    worker.write(control)
    buy = row("buy", 10, 9, ticker=control["ticker"], at=clock[0] - 100)
    monkeypatch.setattr(manual, "rows", lambda **kwargs: [buy])
    with pytest.raises(HTTPException, match="P&L unavailable"):
        worker.check_daily_loss("ETH", clock[0])
    assert not worker.control(control["ticker"])["paused"]
    settlement = dict(market=control["ticker"], timestamp=clock[0], body=dict(result="no"))
    monkeypatch.setattr(worker.stores["ETH"], "list", lambda *args, **kwargs: [settlement])
    worker.check_daily_loss("ETH", clock[0])  # Confirmed $9 loss is below the $20 limit.
