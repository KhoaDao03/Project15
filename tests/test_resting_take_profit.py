import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from test_live_automation import live  # noqa: F401
from test_manual_trading import TICKER, venue  # noqa: F401

from btc15.live_automation import LiveAutomation
from btc15.manual_trading import ManualOrder


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def resting(live):  # noqa: F811
    worker, _, manual, control, data, clock = live
    exchange = dict(
        orders={},
        requests=[],
        position=Decimal(0),
        cancel_fills=Decimal(0),
        cancel_error=False,
        cancel_still_resting=False,
        read_error=False,
        lost_ack=False,
        reject_rest=False,
    )

    def fill(order, count):
        count = Decimal(count)
        order["fill_count_fp"] = str(Decimal(order["fill_count_fp"]) + count)
        order["remaining_count_fp"] = str(Decimal(order["remaining_count_fp"]) - count)
        exchange["position"] += count * (1 if order["payload"]["side"] == "bid" else -1)
        if Decimal(order["remaining_count_fp"]) == 0:
            order["status"] = "executed"

    class Client:
        def __init__(self, settings):
            self.settings = settings
            self.http = httpx.AsyncClient(
                base_url=settings.rest_url + "/", transport=httpx.MockTransport(self.send)
            )

        def headers(self, method, path):
            assert method in ("POST", "DELETE")
            assert path.startswith("/trade-api/v2/portfolio/events/orders")
            return {}

        def send(self, request):
            exchange["requests"].append(request.method)
            if request.method == "DELETE":
                order = exchange["orders"][request.url.path.rsplit("/", 1)[1]]
                assert request.url.params["exchange_index"] == "2"
                if exchange["cancel_fills"]:
                    fill(order, exchange["cancel_fills"])
                    exchange["cancel_fills"] = Decimal(0)
                if exchange["cancel_error"]:
                    raise httpx.ReadTimeout("lost cancellation", request=request)
                if not exchange["cancel_still_resting"]:
                    order["status"] = "canceled"
                    order["remaining_count_fp"] = "0"
                return httpx.Response(200, json={"order_id": order["order_id"]})
            payload = json.loads(request.content)
            if payload["time_in_force"] == "good_till_canceled" and exchange["reject_rest"]:
                return httpx.Response(422, json={})
            ident = str(uuid4())
            order = dict(
                order_id=ident,
                client_order_id=payload["client_order_id"],
                ticker=payload["ticker"],
                status="resting",
                fill_count_fp="0",
                remaining_count_fp=payload["count"],
                payload=payload,
            )
            exchange["orders"][ident] = order
            if payload["time_in_force"] != "good_till_canceled":
                fill(
                    order,
                    exchange.get("buy_fill", payload["count"])
                    if not payload["reduce_only"]
                    else payload["count"],
                )
                order["status"] = "canceled"
                order["remaining_count_fp"] = "0"
            if payload["time_in_force"] == "good_till_canceled" and exchange["lost_ack"]:
                raise httpx.ReadTimeout("lost resting acknowledgement", request=request)
            return httpx.Response(201, json=dict(order_id=ident, client_order_id=payload["client_order_id"]))

        async def get(self, path, params=None, authenticated=False):
            if path.startswith("series/"):
                return dict(series=dict(ticker="KXETH15M", exchange_index=2))
            if path.startswith("portfolio/orders/"):
                if exchange["read_error"]:
                    raise httpx.ReadTimeout("order status unavailable")
                return dict(order=copy.deepcopy(exchange["orders"][path.rsplit("/", 1)[1]]))
            if path == "portfolio/balance":
                return dict(balance=10000)
            return dict(
                market=dict(
                    ticker=TICKER,
                    exchange_index=2,
                    status="active",
                    open_time=datetime.fromtimestamp(clock[0] - 900, UTC).isoformat(),
                    close_time=datetime.fromtimestamp(clock[0] + 300, UTC).isoformat(),
                    price_ranges=[dict(start=".01", end=".99", step=".01")],
                )
            )

        async def pages(self, path, key, params=None, authenticated=False):
            if path == "portfolio/positions":
                yield dict(ticker=TICKER, exchange_index=2, position_fp=str(exchange["position"]))
            else:
                for order in exchange["orders"].values():
                    yield copy.deepcopy(order)

        async def close(self):
            await self.http.aclose()

    manual.client_factory = Client
    return worker, manual, control, data, clock, exchange, fill


def resting_order(exchange):
    return next(
        o for o in exchange["orders"].values() if o["payload"]["time_in_force"] == "good_till_canceled"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["yes", "no"])
async def test_places_once_after_confirmed_buy_and_recovers_partial_fills(resting, side):
    worker, manual, control, data, clock, exchange, fill = resting
    data["side"] = side
    await worker.step_market(control)
    order = resting_order(exchange)
    assert order["payload"]["price"] == ("0.9900" if side == "yes" else "0.0100")
    assert order["payload"]["reduce_only"]
    assert len(exchange["orders"]) == 2
    fill(order, 6)
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    clock[0] += 3
    await restarted.reconcile()
    await restarted.step_market(control)
    assert len(exchange["orders"]) == 2
    assert abs(exchange["position"]) == 4
    fill(order, 4)
    clock[0] += 3
    await restarted.reconcile()
    await restarted.step_market(control)
    assert exchange["position"] == 0
    assert len(exchange["orders"]) == 2
    assert "closed" in restarted.messages[TICKER]


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["yes", "no"])
async def test_cancel_below70_no_rearm_and_original_stop_for_remainder(resting, side):
    worker, manual, control, data, clock, exchange, fill = resting
    data["side"] = side
    await worker.step_market(control)
    fill(resting_order(exchange), 6)
    data["bid"] = 0.70
    await worker.step_market(control)
    assert "DELETE" not in exchange["requests"]
    exchange["cancel_fills"] = Decimal(1)
    data["bid"] = 0.699
    await worker.step_market(control)
    assert exchange["requests"].count("DELETE") == 1
    assert len(exchange["orders"]) == 2
    assert abs(exchange["position"]) == 3
    assert worker.controls()[TICKER]["resting_disabled"]
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    data["bid"] = 0.90
    await restarted.step_market(control)
    assert len(exchange["orders"]) == 2
    data["bid"] = 0.55
    await restarted.step_market(control)
    stop = list(exchange["orders"].values())[-1]["payload"]
    assert stop["count"] == "3.00"
    assert stop["price"] == ("0.0100" if side == "yes" else "0.9900")
    assert stop["time_in_force"] == "immediate_or_cancel"
    assert exchange["position"] == 0


@pytest.mark.anyio
async def test_99_fallback_cancels_and_sells_only_unfilled_remainder(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    exchange["cancel_fills"] = Decimal(6)
    data["bid"] = 0.99
    await worker.step_market(control)
    order = list(exchange["orders"].values())[-1]["payload"]
    assert order["count"] == "4.00" and order["price"] == "0.9900"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert exchange["requests"] == ["POST", "POST", "DELETE", "POST"]
    assert exchange["position"] == 0


@pytest.mark.anyio
async def test_full_fill_during_cancel_never_double_sells(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    exchange["cancel_fills"] = Decimal(10)
    data["bid"] = 0.99
    await worker.step_market(control)
    assert len(exchange["orders"]) == 2
    assert exchange["position"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("error", ["cancel_error", "cancel_still_resting", "read_error"])
async def test_jump_to_stop_waits_for_cancel_then_survives_rebound_and_feed_loss(resting, error):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    exchange[error] = True
    data["bid"] = 0.40
    try:
        await worker.step_market(control)
    except httpx.ReadTimeout:
        assert error == "read_error"
    assert len(exchange["orders"]) == 2
    assert worker.controls()[TICKER]["exit_reason"] == "HARD_STOP"
    exchange[error] = False
    data["fresh"] = False
    data["bid"] = None
    restarted = LiveAutomation(manual, worker.members, worker.stores)
    restarted.running = True
    await restarted.step_market(control)
    assert list(exchange["orders"].values())[-1]["payload"]["price"] == "0.0100"
    assert exchange["position"] == 0


@pytest.mark.anyio
async def test_lost_resting_ack_reconciles_without_duplicate(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    exchange["lost_ack"] = True
    await worker.step_market(control)
    assert manual.rows()[0]["state"] == "unknown"
    await worker.step_market(control)
    assert len(exchange["orders"]) == 2
    await worker.reconcile()
    await worker.step_market(control)
    assert len(exchange["orders"]) == 2


@pytest.mark.anyio
async def test_rejected_resting_order_keeps_ioc_fallback(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    exchange["reject_rest"] = True
    await worker.step_market(control)
    assert manual.rows()[0]["state"] == "rejected"
    data["bid"] = 0.99
    await worker.step_market(control)
    assert exchange["position"] == 0
    assert list(exchange["orders"].values())[-1]["payload"]["time_in_force"] == "immediate_or_cancel"


@pytest.mark.anyio
async def test_takeover_cancels_resting_offer_without_stop_sale(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    worker.takeover(TICKER)
    data["bid"] = 0.4
    await worker.step_market(control)
    assert len(exchange["orders"]) == 2
    assert exchange["requests"][-1] == "DELETE"
    assert exchange["position"] == 10


@pytest.mark.anyio
async def test_confirmed_resting_other_market_does_not_block_buys(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    other = ManualOrder(
        client_order_id=uuid4(),
        ticker="KXSOL15M-26SEP140145-45",
        action="buy",
        side="yes",
        count=10,
        limit_cents=90,
        confirm="REAL_MONEY",
    )
    row, fresh = manual.claim(other, origin="bot")
    assert fresh
    same = other.model_copy(update={"client_order_id": uuid4(), "ticker": TICKER, "action": "sell"})
    with pytest.raises(HTTPException):
        manual.claim(same, origin="bot")


@pytest.mark.anyio
async def test_stale_reconciliation_cannot_resurrect_canceled_resting_order(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    old = copy.deepcopy(manual.rows()[0])
    data["bid"] = 0.69
    await worker.step_market(control)
    assert manual.save(old)["state"] == "complete"
    assert manual.rows()[0]["state"] == "complete"


@pytest.mark.anyio
async def test_resting_quantity_uses_confirmed_buy_fills(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    exchange["buy_fill"] = "3.50"
    await worker.step_market(control)
    assert resting_order(exchange)["payload"]["count"] == "3.50"


@pytest.mark.anyio
async def test_shutdown_waits_for_resting_cancellation_even_after_takeover(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    worker.takeover(TICKER)
    with pytest.raises(HTTPException, match="cancellation"):
        worker.prepare_shutdown()
    await worker.step_market(control)
    worker.prepare_shutdown()


@pytest.mark.anyio
async def test_canceled_below70_can_later_use_original_profit_exit(resting):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    data["bid"] = 0.69
    await worker.step_market(control)
    data["bid"] = 0.99
    await worker.step_market(control)
    last = list(exchange["orders"].values())[-1]["payload"]
    assert last["time_in_force"] == "immediate_or_cancel" and last["price"] == "0.9900"
    assert exchange["position"] == 0


@pytest.mark.anyio
async def test_expired_controls_skip_journal_scans_but_pending_orders_are_reconciled(resting, monkeypatch):
    worker, manual, control, data, clock, exchange, fill = resting
    await worker.step_market(control)
    clock[0] = control["close_time"] + 1
    original = manual.rows

    def no_scan():
        raise AssertionError("Expired market must not scan the full order journal")

    monkeypatch.setattr(manual, "rows", no_scan)
    await worker.step_market(control)
    monkeypatch.setattr(manual, "rows", original)
    await worker.reconcile()
    assert resting_order(exchange)["status"] == "canceled"
