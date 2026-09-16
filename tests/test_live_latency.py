import asyncio
import copy
import time

import pytest
from test_live_automation import live  # noqa: F401
from test_manual_trading import TICKER, venue  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_entry_reuses_connection_and_reads_metadata_once(live):  # noqa: F811
    worker, state, manual, control, *_ = live
    async with manual.client() as first:
        async with manual.client() as second:
            assert first is second
    await worker.step_market(control)
    # The buy reads metadata once; the subsequent resting-sale attempt has its own preflight.
    buy_reads = state["reads"][: state["reads"].index("portfolio/orders/exchange-order")]
    assert buy_reads.count("series/KXETH15M") == 1
    assert buy_reads.count("markets/" + TICKER) == 1
    assert state["reads"].count("series/KXETH15M") == 2
    row = manual.rows()[0]
    assert len(state["posts"]) == 1
    t = row["timing"]
    assert t["preflight_started_at"] <= t["submitted_at"] <= t["acknowledged_at"] <= t["fill_confirmed_at"]
    assert t["decision_detected_at"] is not None
    assert t["preflight_ms"] >= 0 and t["submission_ms"] >= 0
    await manual.close()
    assert manual._client is None


@pytest.mark.anyio
async def test_holdings_and_balance_overlap(live, monkeypatch):  # noqa: F811
    worker, state, manual, control, *_ = live
    holdings_started = asyncio.Event()
    balance_started = asyncio.Event()
    original = manual.holdings
    async with manual.client() as client:
        get = client.get

        async def holdings(*args):
            holdings_started.set()
            await asyncio.wait_for(balance_started.wait(), 1)
            return await original(*args)

        async def read(path, *args):
            if path == "portfolio/balance":
                balance_started.set()
                await asyncio.wait_for(holdings_started.wait(), 1)
            return await get(path, *args)

        monkeypatch.setattr(manual, "holdings", holdings)
        monkeypatch.setattr(client, "get", read)
    await worker.step_market(control)
    assert len(state["posts"]) == 1


@pytest.mark.anyio
async def test_stream_fill_is_deduplicated_and_cannot_clear_unknown(live):  # noqa: F811
    worker, state, manual, control, *_ = live
    state["post_error"] = "timeout"
    await worker.step_market(control)
    stale = copy.deepcopy(manual.rows()[0])
    fill = dict(
        type="fill",
        msg=dict(
            client_order_id=stale["id"],
            order_id="exchange-order",
            trade_id="fill-1",
            market_ticker=TICKER,
            exchange_index=2,
            subaccount=0,
            side="yes",
            action="buy",
            count_fp="4.00",
            ts_ms=int(time.time() * 1000),
        ),
    )
    assert manual.receive_fill(fill)
    assert not manual.receive_fill(fill)
    manual.save(stale)  # A REST request started before the notification must not erase it.
    row = manual.rows()[0]
    assert row["state"] == "unknown"
    assert row["fill_notification"]["count_fp"] == "4.00"
    assert row["timing"]["first_fill_received_at"]
    assert manual.fill_wakeup.is_set()
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    for field, value in [
        ("subaccount", 1),
        ("market_ticker", "OTHER"),
        ("exchange_index", 0),
        ("order_id", "other"),
        ("side", "no"),
        ("count_fp", "NaN"),
        ("count_fp", "7"),
    ]:
        wrong = copy.deepcopy(fill)
        wrong["msg"].update(trade_id="fill-2", **{field: value})
        assert not manual.receive_fill(wrong)
    fill["msg"].update(trade_id="fill-2", count_fp="6")
    assert manual.receive_fill(fill)
    assert manual.rows()[0]["fill_notification"]["count_fp"] == "10.00"
    assert manual.rows()[0]["state"] == "unknown"


@pytest.mark.anyio
async def test_fill_stream_reconnects_and_cancels_cleanly(live, monkeypatch):  # noqa: F811
    import json
    from contextlib import asynccontextmanager

    import btc15.manual_trading as module

    _, _, manual, *_ = live
    connections = []
    second_connected = asyncio.Event()
    headers = []
    async with manual.client() as client:
        monkeypatch.setattr(client, "headers", lambda method, path: headers.append((method, path)) or {})

    class Socket:
        async def send(self, raw):
            assert json.loads(raw)["params"]["channels"] == ["fill"]

        def __aiter__(self):
            return self.events()

        async def events(self):
            yield json.dumps(dict(type="subscribed", msg=dict(channel="fill", sid=1)))
            if len(connections) == 1:
                raise OSError("disconnected")
            second_connected.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        connections.append(args)
        yield Socket()

    monkeypatch.setattr(module.websockets, "connect", connect)
    task = asyncio.create_task(manual.watch_fills())
    try:
        await asyncio.wait_for(second_connected.wait(), 4)
        assert manual.fill_stream["connected"]
        assert manual.fill_wakeup.is_set()
        assert len(headers) == 2 and headers[0][0] == "GET"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not manual.fill_stream["connected"]
    assert not manual.rows()


@pytest.mark.anyio
async def test_fill_before_ack_survives_reconciliation(live, monkeypatch):  # noqa: F811
    worker, state, manual, control, *_ = live
    async with manual.client() as client:
        original = client.http.post

        async def post(*args, **kwargs):
            payload = kwargs["json"]
            assert manual.receive_fill(
                dict(
                    type="fill",
                    msg=dict(
                        client_order_id=payload["client_order_id"],
                        order_id="exchange-order",
                        trade_id="first",
                        market_ticker=TICKER,
                        exchange_index=2,
                        subaccount=0,
                        side="yes",
                        action="buy",
                        count_fp="10.00",
                    ),
                )
            )
            return await original(*args, **kwargs)

        monkeypatch.setattr(client.http, "post", post)
    await worker.step_market(control)
    row = manual.rows()[0]
    assert row["state"] == "complete"
    assert row["fill_notification"]["count_fp"] == "10.00"
    assert row["timing"]["first_fill_received_at"] <= row["timing"]["acknowledged_at"]
