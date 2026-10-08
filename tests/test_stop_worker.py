import asyncio

# ruff: noqa: F811
from dataclasses import replace

import pytest
from test_live_automation import live  # noqa: F401
from test_manual_trading import TICKER, venue  # noqa: F401

from btc15.live_automation import LiveAutomation


@pytest.fixture(autouse=True)
def outside_entry_blackout(monkeypatch):
    # These tests create a holding to exercise exits, independently of wall time.
    monkeypatch.setattr("btc15.live_automation.entry_blackout", lambda now: None)


async def held_position(live):
    worker, state, manual, control, data, clock = live
    member = worker.members[control["asset"]]
    await worker.step_market(control)
    member["config"] = replace(member["config"], take_profit=None)
    state["position"] = "10"
    return live


@pytest.mark.anyio
async def test_stop_reuses_one_preflight_and_cannot_double_sell(live, monkeypatch):
    worker, state, manual, control, data, clock = await held_position(live)
    data["bid"] = 0.50
    assert len(worker.detect_stops()) == 1
    state["reads"].clear()
    reads = []
    original = manual.holdings

    async def holdings(*args):
        reads.append(args)
        return await original(*args)

    monkeypatch.setattr(manual, "holdings", holdings)
    await asyncio.gather(worker.step_market(control), worker.step_market(control, stops_only=True))
    assert len(state["posts"]) == 2
    assert state["posts"][-1]["reduce_only"]
    assert state["posts"][-1]["price"] == "0.0100"
    assert state["reads"].count("series/KXETH15M") == 0
    assert state["reads"].count("markets/" + TICKER) == 0
    assert len(reads) == 1
    timing = manual.rows()[0]["timing"]
    assert timing["stop_bid"] == 0.50
    assert timing["metadata_cache_hit"] is True
    assert timing["stop_detected_at"] <= timing["submitted_at"]


@pytest.mark.anyio
@pytest.mark.parametrize("block", ["stale", "above", "paused", "closed", "flat"])
async def test_stop_detector_requires_fresh_managed_holdings(live, block):
    worker, state, manual, control, data, clock = await held_position(live)
    data["bid"] = 0.50
    if block == "stale":
        data["fresh"] = False
    elif block == "above":
        data["bid"] = 0.56
    elif block == "paused":
        worker.takeover(TICKER)
    elif block == "closed":
        clock[0] = control["close_time"] + 1
    else:
        row = next(r for r in manual.rows() if r["request"]["action"] == "buy")
        row["exchange_order"]["fill_count_fp"] = "0"
        manual.save(row)
    assert worker.detect_stops() == []
    assert worker.control(TICKER).get("exit_reason") != "HARD_STOP"


@pytest.mark.anyio
async def test_trigger_persists_while_submission_waits_and_survives_restart(live):
    worker, state, manual, control, data, clock = await held_position(live)
    data["bid"] = 0.50
    await manual.order_lock.acquire()
    task = asyncio.create_task(worker.watch_stops())
    try:
        for _ in range(100):
            if worker.control(TICKER).get("exit_reason") == "HARD_STOP":
                break
            await asyncio.sleep(0.001)
        assert worker.control(TICKER)["exit_reason"] == "HARD_STOP"
        assert len(state["posts"]) == 1
        data["bid"] = 0.90
        data["fresh"] = False
        worker.running = False
        worker.stop_wakeup.set()
    finally:
        manual.order_lock.release()
        await asyncio.wait_for(task, 2)
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    assert restarted.detect_stops()
    await restarted.step_market(control, stops_only=True)
    assert len(state["posts"]) == 2
    assert state["posts"][-1]["price"] == "0.0100"


@pytest.mark.anyio
async def test_stop_worker_polls_without_notifications(live):
    worker, state, manual, control, data, clock = await held_position(live)
    task = asyncio.create_task(worker.watch_stops())
    try:
        await asyncio.sleep(0.01)
        data["bid"] = 0.50
        for _ in range(100):
            if len(state["posts"]) == 2:
                break
            await asyncio.sleep(0.005)
        assert len(state["posts"]) == 2
    finally:
        worker.running = False
        worker.stop_wakeup.set()
        await asyncio.wait_for(task, 2)


@pytest.mark.anyio
async def test_queued_stop_precedes_queued_buy_and_cancellation_releases_priority(live):
    _, _, manual, *_ = live
    order = []

    async def acquire(name, **kwargs):
        async with manual.submission_slot(**kwargs):
            order.append(name)

    await manual.order_lock.acquire()
    buy = asyncio.create_task(acquire("buy", buy=True))
    await asyncio.sleep(0)
    stop = asyncio.create_task(acquire("stop", stop=True))
    await asyncio.sleep(0)
    manual.order_lock.release()
    await asyncio.wait_for(asyncio.gather(buy, stop), 1)
    assert order == ["stop", "buy"]
    await manual.order_lock.acquire()
    stop = asyncio.create_task(acquire("cancelled", stop=True))
    await asyncio.sleep(0)
    stop.cancel()
    await asyncio.gather(stop, return_exceptions=True)
    manual.order_lock.release()
    assert manual._pending_stops == 0
    assert manual._stops_drained.is_set()


@pytest.mark.anyio
async def test_slow_exit_does_not_block_detection_in_another_market(live, monkeypatch):
    import copy
    import json

    worker, state, manual, control, data, clock = await held_position(live)
    release = asyncio.Event()
    started = asyncio.Event()
    bids = {TICKER: 0.50}

    def book(control, now, trace=None):
        if trace is not None:
            trace["book_snapshot"] = dict(market={}, published_at=clock[0])
        return dict(yes_bid=bids[control["ticker"]])

    async def blocked_exit(control, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(worker, "book", book)
    monkeypatch.setattr(worker, "step_market", blocked_exit)
    task = asyncio.create_task(worker.watch_stops())
    try:
        await asyncio.wait_for(started.wait(), 1)
        other = TICKER + "-OTHER"
        worker.write({**control, "ticker": other})
        buy = copy.deepcopy(next(r for r in manual.rows() if r["request"]["action"] == "buy"))
        buy["id"] = "other-buy"
        buy["request"]["ticker"] = other
        with manual.db() as db:
            db.execute("INSERT INTO manual_orders VALUES (?, ?)", (buy["id"], json.dumps(buy)))
        bids[other] = 0.49
        worker.stop_wakeup.set()
        for _ in range(100):
            if worker.control(other).get("exit_reason") == "HARD_STOP":
                break
            await asyncio.sleep(0.001)
        assert worker.control(other)["exit_reason"] == "HARD_STOP"
        assert not release.is_set()
    finally:
        worker.running = False
        worker.stop_wakeup.set()
        release.set()
        await asyncio.wait_for(task, 1)


@pytest.mark.anyio
async def test_no_side_stop_uses_no_bid_and_clamps_to_actual_holdings(live, monkeypatch):
    live[4]["side"] = "no"
    worker, state, manual, control, data, clock = await held_position(live)
    state["position"] = "-4"
    state["fill_count"] = "4.00"
    original = worker.book

    def book(*args, **kwargs):
        return {**original(*args, **kwargs), "yes_bid": 0.90, "no_bid": 0.50}

    monkeypatch.setattr(worker, "book", book)
    assert worker.detect_stops()
    await worker.step_market(control, stops_only=True)
    assert state["posts"][-1]["count"] == "4.00"
    assert state["posts"][-1]["reduce_only"]
    assert manual.rows()[0]["request"]["side"] == "no"


@pytest.mark.anyio
async def test_stop_monitor_does_not_load_expired_control_history(live, monkeypatch):
    worker, state, manual, control, data, clock = await held_position(live)
    worker.write({**control, "ticker": "EXPIRED", "close_time": clock[0] - 1})
    data["bid"] = 0.50

    def full_scan(*args, **kwargs):
        raise AssertionError("Stop monitor must query only open controls")

    monkeypatch.setattr(worker, "controls", full_scan)
    assert [c["ticker"] for c in worker.detect_stops()] == [TICKER]
    with manual.db() as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT ticker,body FROM live_controls "
            "WHERE json_extract(body, '$.close_time') > ?",
            (clock[0],),
        ).fetchall()
    assert any("live_controls_close_time" in row[-1] for row in plan)


@pytest.mark.anyio
async def test_stop_submission_reserves_read_priority_and_restores_context(live, monkeypatch):
    from btc15.api import stop_reads

    worker, state, manual, control, data, clock = await held_position(live)
    original = manual.holdings
    seen = []

    async def holdings(*args):
        seen.append(stop_reads.get())
        return await original(*args)

    monkeypatch.setattr(manual, "holdings", holdings)
    data["bid"] = 0.50
    state["post_error"] = "timeout"
    await worker.step_market(control)
    assert seen == [True]
    assert manual.rows()[0]["state"] == "unknown"
    assert not stop_reads.get()


@pytest.mark.anyio
@pytest.mark.parametrize('cache_state', ['cold', 'expired'])
async def test_stop_cache_miss_fetches_metadata_and_still_checks_holdings(live, cache_state):
    worker, state, manual, control, data, clock = await held_position(live)
    if cache_state == 'cold':
        manual._stop_metadata.clear()
    else:
        manual._stop_metadata[TICKER]['expires_at'] = 0
    data['bid'] = .49
    state['position'] = '3'
    state['reads'].clear()
    await worker.step_market(control)
    assert state['reads'].count('series/KXETH15M') == 1
    assert state['reads'].count('markets/' + TICKER) == 1
    assert state['posts'][-1]['count'] == '3.00'
    assert state['posts'][-1]['reduce_only'] is True
    assert manual.rows()[0]['timing']['metadata_cache_hit'] is False


@pytest.mark.anyio
async def test_cached_stop_checks_fresh_holdings_and_zero_blocks_sale(live):
    worker, state, manual, control, data, clock = await held_position(live)
    data['bid'] = .49
    state['position'] = '0'
    await worker.step_market(control)
    assert len(state['posts']) == 1
    assert manual.rows()[0]['timing']['metadata_cache_hit'] is True
    assert 'zero holdings' in manual.rows()[0]['message']


@pytest.mark.anyio
async def test_stop_rejection_invalidates_metadata_without_post_retry(live):
    worker, state, manual, control, data, clock = await held_position(live)
    data['bid'] = .49
    state['post_error'] = 422
    await worker.step_market(control)
    assert len(state['posts']) == 2
    assert TICKER not in manual._stop_metadata
    assert manual.rows()[0]['state'] == 'rejected'


@pytest.mark.anyio
async def test_fresh_market_refresh_invalidates_cache_on_closed_market(live):
    from fastapi import HTTPException
    worker, state, manual, control, data, clock = await held_position(live)
    assert manual.stop_metadata(TICKER)
    state['status'] = 'closed'
    async with manual.client() as client:
        with pytest.raises(HTTPException):
            await manual.market(client, TICKER)
    assert manual.stop_metadata(TICKER) is None


@pytest.mark.anyio
async def test_stop_records_processing_and_first_publication_times(live, monkeypatch):
    worker, state, manual, control, data, clock = await held_position(live)
    def book(control, now, trace=None):
        trace['book_snapshot'] = dict(published_at=100, market=dict(
            book_received=95, book_processing_started_at=96,
            book_processed_at=97, book_first_published_at=98))
        return dict(yes_bid=.49)
    monkeypatch.setattr(worker, 'book', book)
    worker.detect_stops()
    timing = worker.control(TICKER)['stop_timing']
    assert timing['quote_processing_started_at'] == 96
    assert timing['quote_processed_at'] == 97
    assert timing['quote_first_published_at'] == 98
    assert timing['quote_published_at'] == 100
