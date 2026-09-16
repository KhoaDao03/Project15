import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_manual_trading import TICKER, venue  # noqa: F401

from btc15.config import Strategy
from btc15.live_automation import LiveAutomation, LiveControl, consumes_entry_attempt, snap_buy_limit


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def live(venue, monkeypatch):  # noqa: F811
    _, state, manual, _ = venue
    config = Strategy.load("config/settlement-edge-eth-paper.json")
    now = time.time()
    clock = [now]
    data = dict(bid=0.89, ask=0.90, reasons=[], side="yes", healthy=True, fresh=True)

    class Store:
        def read_market_display(self, key=None):
            if key:
                return dict(
                    market=TICKER,
                    body=dict(
                        timestamp=clock[0],
                        model_evaluated_at=clock[0],
                        versions={"config": config.version},
                        reasons=data["reasons"],
                        decision="TRADE_CANDIDATE",
                        side=data["side"],
                        probability={"p_yes": 0.95, "p_no": 0.95},
                    ),
                )
            return dict(
                run_id="eth-paper",
                connected=data["fresh"],
                published_at=clock[0],
                markets=[
                    dict(
                        ticker=TICKER,
                        close_time=datetime.fromtimestamp(now + 300, UTC).isoformat(),
                        fresh=True,
                        book=dict(
                            yes_bid=data["bid"], yes_ask=data["ask"], no_bid=data["bid"], no_ask=data["ask"]
                        ),
                    )
                ],
            )

    import btc15.live_automation as module

    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(module, "health", lambda *args: dict(healthy=data["healthy"]))
    worker = LiveAutomation(manual, {"ETH": dict(config=config, run_id="eth-paper")}, {"ETH": Store()})
    worker.running = True
    state["fill_count"] = "10.00"
    control = worker.configure(
        LiveControl(ticker=TICKER, enabled=True, contracts=10, revision=0, confirm="ENABLE_REAL_TRADING")
    )
    return worker, state, manual, control, data, clock


@pytest.mark.parametrize("quantity", [0, 21, 1.5, True, "10"])
def test_hard_contract_limit(quantity):
    with pytest.raises(ValidationError):
        LiveControl(ticker=TICKER, enabled=True, contracts=quantity, revision=0)


def test_cap_snaps_in_yes_and_no_price_space():
    grid = [dict(start=".01", end=".99", step=".01")]
    assert snap_buy_limit(Decimal(".892"), "yes", grid) == Decimal(".89")
    assert snap_buy_limit(Decimal(".892"), "no", grid) == Decimal(".89")


@pytest.mark.anyio
async def test_buy_full_quantity_once_and_restart(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    assert state["posts"][0]["time_in_force"] == "fill_or_kill"
    assert state["posts"][0]["count"] == "10.00"
    assert state["posts"][0]["price"] == "0.9600"
    assert manual.rows()[0]["origin"] == "bot"
    state["position"] = "10"
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    await restarted.step_market(restarted.controls()[TICKER])
    assert len(state["posts"]) == 2
    assert state["posts"][-1]["time_in_force"] == "good_till_canceled"
    assert state["posts"][-1]["price"] == "0.9900"


@pytest.mark.anyio
async def test_disabled_buys_keep_stop_exits_and_sell_only_held(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    control = worker.configure(LiveControl(ticker=TICKER, enabled=False, contracts=20, revision=1))
    data["bid"] = 0.55
    await worker.step_market(control)
    order = state["posts"][-1]
    assert order["reduce_only"] and order["time_in_force"] == "immediate_or_cancel"
    assert order["price"] == "0.0100" and order["count"] == "10.00"
    assert worker.controls()[TICKER]["exit_reason"] == "HARD_STOP"


@pytest.mark.anyio
async def test_take_profit_floor_and_hard_stop_override_after_no_fill(live):
    worker, state, manual, control, data, clock = live
    state["price_ranges"] = [dict(start=".001", end=".999", step=".001")]
    await worker.step_market(control)
    state["position"] = "10"
    data["bid"] = 0.99
    state["fill_count"] = "0.00"
    await worker.step_market(control)
    assert state["posts"][-1]["price"] == "0.9900"
    clock[0] += 3
    data["bid"] = 0.50
    await worker.step_market(worker.controls()[TICKER])
    assert state["posts"][-1]["price"] == "0.0100"


@pytest.mark.anyio
async def test_manual_takeover_blocks_all_automation(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    worker.takeover(TICKER)
    data["bid"] = 0.50
    await worker.step_market(worker.controls()[TICKER])
    assert len(state["posts"]) == 1
    assert worker.controls()[TICKER]["paused"]


@pytest.mark.anyio
async def test_unknown_ack_never_creates_replacement(live):
    worker, state, manual, control, data, clock = live
    state["post_error"] = "timeout"
    await worker.step_market(control)
    assert manual.rows()[0]["state"] == "unknown"
    clock[0] += 10
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    await worker.reconcile()
    assert len(state["posts"]) == 1


@pytest.mark.parametrize("block", ["stale", "health", "probability", "price"])
@pytest.mark.anyio
async def test_entry_gates_block_real_post(live, block):
    worker, state, manual, control, data, clock = live
    if block == "stale":
        data["fresh"] = False
    elif block == "health":
        data["healthy"] = False
    elif block == "price":
        data["ask"] = 0.96
    else:
        data["reasons"] = [{"code": "BLEEP_MIN_PROBABILITY"}]
    with pytest.raises(HTTPException):
        await worker.step_market(control)
    assert not state["posts"]


def test_revision_and_explicit_enable_confirmation(live):
    worker, *_ = live
    with pytest.raises(HTTPException):
        worker.configure(
            LiveControl(ticker=TICKER, enabled=True, contracts=20, revision=0, confirm="ENABLE_REAL_TRADING")
        )
    with pytest.raises(HTTPException):
        worker.configure(LiveControl(ticker=TICKER, enabled=True, contracts=20, revision=1))


@pytest.mark.anyio
async def test_count_20_and_no_side_stop_payload(live):
    worker, state, manual, control, data, clock = live
    data["side"] = "no"
    state["fill_count"] = "20.00"
    control = worker.configure(
        LiveControl(ticker=TICKER, enabled=True, contracts=20, revision=1, confirm="ENABLE_REAL_TRADING")
    )
    await worker.step_market(control)
    assert state["posts"][0]["count"] == "20.00" and state["posts"][0]["price"] == "0.0400"
    state["position"] = "-20"
    data["bid"] = 0.50
    await worker.step_market(control)
    assert state["posts"][-1]["side"] == "bid" and state["posts"][-1]["price"] == "0.9900"
    assert state["posts"][-1]["reduce_only"]


@pytest.mark.anyio
async def test_partial_exit_retries_only_confirmed_remainder_after_restart(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    data["bid"] = 0.50
    state["fill_count"] = "6.00"
    await worker.step_market(control)
    assert state["posts"][-1]["count"] == "10.00"
    state["position"] = "4"
    state["fill_count"] = "4.00"
    clock[0] += 3
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    await restarted.step_market(restarted.controls()[TICKER])
    assert state["posts"][-1]["count"] == "4.00"
    await restarted.step_market(restarted.controls()[TICKER])
    assert len(state["posts"]) == 3


@pytest.mark.anyio
async def test_control_change_during_preflight_stops_post(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    original = manual.market
    calls = 0

    async def changed(*args):
        nonlocal calls
        result = await original(*args)
        calls += 1
        if calls == 1:
            worker.takeover(TICKER)
        return result

    monkeypatch.setattr(manual, "market", changed)
    await worker.step_market(control)
    assert not state["posts"]
    assert manual.rows()[0]["state"] == "rejected"


@pytest.mark.anyio
async def test_insufficient_cash_never_posts(live):
    worker, state, manual, control, data, clock = live
    state["balance_response"] = {"balance": 100}
    await worker.step_market(control)
    assert not state["posts"] and manual.rows()[0]["state"] == "rejected"


@pytest.mark.anyio
async def test_other_market_unknown_does_not_block_known_position_exit(live):
    from test_manual_trading import request_body

    from btc15.manual_trading import ManualOrder

    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    body = request_body()
    body["ticker"] = TICKER + "-OTHER"
    row, _ = manual.claim(ManualOrder(**body))
    row["state"] = "unknown"
    manual.save(row)
    data["bid"] = 0.50
    await worker.step_market(control)
    assert len(state["posts"]) == 2 and state["posts"][-1]["reduce_only"]


@pytest.mark.anyio
async def test_single_live_worker_owns_journal(live, monkeypatch):
    import asyncio

    worker, *_ = live
    second = LiveAutomation(worker.manual, worker.members, worker.stores)
    event = asyncio.Event()

    async def wait():
        await event.wait()

    monkeypatch.setattr(worker, "reconcile", wait)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    await second.run()
    assert worker.running and not second.running
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not worker.running


@pytest.mark.anyio
async def test_shutdown_requires_resolving_or_taking_over_live_position(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    with pytest.raises(HTTPException, match="Live position"):
        worker.prepare_shutdown()
    worker.takeover(TICKER)
    worker.prepare_shutdown()
    assert not worker.controls()[TICKER]["enabled"]


def test_asset_setting_survives_rollover_and_restart(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    worker.configure(
        LiveControl(ticker=TICKER, enabled=True, contracts=17, revision=1, confirm="ENABLE_REAL_TRADING")
    )
    store = worker.stores["ETH"]
    original = store.read_market_display
    next_ticker = TICKER + "-NEXT"

    def next_market(key=None):
        result = original(key)
        if key:
            result["market"] = next_ticker
        else:
            result["markets"][0]["ticker"] = next_ticker
        return result

    monkeypatch.setattr(store, "read_market_display", next_market)
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    restarted.sync_markets()
    assert restarted.controls()[next_ticker]["enabled"]
    assert restarted.controls()[next_ticker]["contracts"] == 17
    assert restarted.assets()["ETH"]["revision"] == 2
    assert set(restarted.assets()) == {"ETH"}
    restarted.configure(LiveControl(ticker=next_ticker, enabled=False, contracts=17, revision=2))
    assert all(not c["enabled"] for c in restarted.controls().values())
    assert not restarted.controls()[TICKER]["paused"]
    next_ticker += "-AGAIN"
    restarted.sync_markets()
    assert next_ticker not in restarted.controls()
    assert not state["posts"]


def test_takeover_disables_persistent_asset_policy(live):
    worker, *_ = live
    worker.takeover(TICKER)
    assert not worker.assets()["ETH"]["enabled"]
    worker.sync_markets()
    assert worker.controls()[TICKER]["paused"]


def test_shutdown_disables_persistent_asset_policy(live):
    worker, *_ = live
    worker.prepare_shutdown()
    assert not worker.assets()["ETH"]["enabled"]


def test_existing_user_switch_migrates_to_persistent_policy(live):
    worker, state, manual, control, data, clock = live
    with manual.db() as db:
        db.execute("DELETE FROM live_assets")
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    assert restarted.assets()["ETH"]["enabled"]
    assert restarted.assets()["ETH"]["contracts"] == 10
    restarted.takeover(TICKER)
    again = LiveAutomation(manual, worker.members, worker.stores)
    assert not again.assets()["ETH"]["enabled"]


@pytest.mark.anyio
async def test_committed_stop_retries_through_feed_loss_and_empty_fills(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    data["bid"] = 0.50
    state["fill_count"] = "6.00"
    await worker.step_market(control)
    state["position"] = "4"
    data["fresh"] = False
    data["bid"] = None
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    state["fill_count"] = "0.00"
    for _ in range(5):
        clock[0] += 3
        await restarted.step_market(restarted.controls()[TICKER])
        assert state["posts"][-1]["count"] == "4.00"
        assert state["posts"][-1]["price"] == "0.0100"
        assert state["posts"][-1]["reduce_only"]
    state["fill_count"] = "4.00"
    clock[0] += 3
    await restarted.step_market(restarted.controls()[TICKER])
    posted = len(state["posts"])
    clock[0] += 3
    await restarted.step_market(restarted.controls()[TICKER])
    assert len(state["posts"]) == posted
    assert "closed" in restarted.messages[TICKER]


@pytest.mark.anyio
async def test_zero_holdings_read_does_not_abandon_committed_exit(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    data["bid"] = 0.50
    state["position"] = "0"
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    assert not worker.controls()[TICKER]["paused"]
    assert worker.controls()[TICKER]["exit_reason"] == "HARD_STOP"
    state["position"] = "10"
    await worker.step_market(control)
    assert state["posts"][-1]["reduce_only"]
    assert state["posts"][-1]["count"] == "10.00"


@pytest.mark.anyio
async def test_unknown_sell_blocks_replacement_even_after_feed_loss(live):
    worker, state, manual, control, data, clock = live
    await worker.step_market(control)
    state["position"] = "10"
    data["bid"] = 0.50
    state["post_error"] = "timeout"
    await worker.step_market(control)
    assert manual.rows()[0]["state"] == "unknown"
    data["fresh"] = False
    clock[0] += 10
    await worker.step_market(control)
    assert len(state["posts"]) == 2


@pytest.mark.anyio
async def test_profit_retry_keeps_floor_without_quotes(live):
    worker, state, manual, control, data, clock = live
    state["price_ranges"] = [dict(start=".001", end=".999", step=".001")]
    await worker.step_market(control)
    state["position"] = "10"
    state["fill_count"] = "0.00"
    data["bid"] = 0.99
    await worker.step_market(control)
    data["fresh"] = False
    clock[0] += 3
    await worker.step_market(control)
    assert len(state["posts"]) == 3
    assert state["posts"][-1]["price"] == "0.9900"


@pytest.mark.anyio
async def test_empty_buys_retry_immediately_but_remain_bounded(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    import btc15.manual_trading as manual_module

    monkeypatch.setattr(
        manual_module, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic)
    )
    assert worker.members["ETH"]["config"].entry_retry_cooldown == 0
    state["fill_count"] = "0.00"
    await worker.step_market(control)
    assert len(state["posts"]) == 3
    assert all(p["time_in_force"] == "fill_or_kill" for p in state["posts"])
    await worker.step_market(control)
    assert len(state["posts"]) == 3


@pytest.mark.anyio
async def test_preflight_failures_preserve_attempts_across_restart(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    import btc15.manual_trading as manual_module

    monkeypatch.setattr(
        manual_module, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic)
    )
    original = manual.market

    async def unavailable(*args):
        raise HTTPException(409, "Waiting for a fresh connected market book")

    monkeypatch.setattr(manual, "market", unavailable)
    for count in range(4):
        await worker.step_market(control)
        # No recursive preflight loop, and even four local failures do not exhaust entries.
        assert len(manual.rows()) == count + 1
        assert not state["posts"]
    assert all(not consumes_entry_attempt(row) for row in manual.rows())
    monkeypatch.setattr(manual, "market", original)
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    state["fill_count"] = "0.00"
    await restarted.step_market(restarted.controls()[TICKER])
    assert len(state["posts"]) == 3
    await restarted.step_market(restarted.controls()[TICKER])
    assert len(state["posts"]) == 3


@pytest.mark.parametrize("extra", [
    {"timing": {}},  # Legacy records cannot prove the order was never sent.
    {"timing": {"preflight_started_at": 1, "submitted_at": 2}},
    {"state": "unknown"},
    {"order_id": "exchange-order"},
    {"acknowledgement": {"order_id": "exchange-order"}},
    {"exchange_order": {"fill_count_fp": "0"}},
])
def test_uncertain_or_exchange_attempts_still_consume_allowance(extra):
    row = {"state": "rejected", "timing": {"preflight_started_at": 1}, **extra}
    assert consumes_entry_attempt(row)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("bid,exits", [(0.989, False), (0.99, True), (0.999, True)])
async def test_live_take_profit_99_cent_boundary(live, side, bid, exits):
    worker, state, manual, control, data, clock = live
    data["side"] = side
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    state["position"] = "10" if side == "yes" else "-10"
    data["bid"] = bid
    await worker.step_market(control)
    assert len(state["posts"]) == 2
    if exits:
        assert worker.controls()[TICKER]["exit_reason"] == "TAKE_PROFIT"
        order = state["posts"][-1]
        assert order["price"] == ("0.9900" if side == "yes" else "0.0100")
        assert order["reduce_only"]
    else:
        assert not worker.controls()[TICKER].get("exit_reason")
        assert state["posts"][-1]["time_in_force"] == "good_till_canceled"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["yes", "no"])
async def test_live_buy_headroom_preserves_95_cent_entry_filter(live, side):
    worker, state, manual, control, data, clock = live
    data.update(side=side, bid=0.94, ask=0.951)
    with pytest.raises(HTTPException, match="entry price/spread"):
        await worker.step_market(control)
    assert not state["posts"]
    data["ask"] = 0.95
    await worker.step_market(control)
    order = state["posts"][-1]
    assert order["price"] == ("0.9600" if side == "yes" else "0.0400")
    assert order["time_in_force"] == "fill_or_kill"
