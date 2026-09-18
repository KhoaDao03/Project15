import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from btc15.config import Settings
from btc15.manual_trading import ManualOrder, exchange_order, install_manual_trading

TICKER = "KXETH15M-26SEP140145-45"
ORIGIN = "http://127.0.0.1:8000"


def request_body(**changes):
    return dict(
        client_order_id=str(uuid4()),
        ticker=TICKER,
        action="buy",
        side="yes",
        count=3,
        limit_cents=70,
        confirm="REAL_MONEY",
        **changes,
    )


@pytest.fixture
def venue(tmp_path):
    state = dict(posts=[], reads=[], position="0.00", status="active", post_error=None, read_error=False)
    settings = replace(Settings(), api_key_id="test-key", private_key_path="test-key.pem")

    class Client:
        def __init__(self, settings):
            self.settings = settings
            self.http = httpx.AsyncClient(
                base_url=settings.rest_url + "/", transport=httpx.MockTransport(self.post)
            )

        def headers(self, method, path):
            assert method == "POST" and path == "/trade-api/v2/portfolio/events/orders"
            return {"KALSHI-ACCESS-SIGNATURE": "test-signature"}

        def post(self, request):
            import json

            payload = json.loads(request.content)
            state["posts"].append(payload)
            assert request.method == "POST"
            assert request.url.path == "/trade-api/v2/portfolio/events/orders"
            if state["post_error"] == "timeout":
                raise httpx.ReadTimeout("lost acknowledgement", request=request)
            if isinstance(state["post_error"], int):
                return httpx.Response(state["post_error"], json={"code": "test"})
            state["last"] = payload
            return httpx.Response(
                201,
                json=dict(
                    order_id="exchange-order",
                    client_order_id=payload["client_order_id"],
                    fill_count="2.00",
                    remaining_count="1.00",
                ),
            )

        async def get(self, path, params=None, authenticated=False):
            state["reads"].append(path)
            if path.startswith("series/"):
                return dict(
                    series=dict(ticker=state.get("ticker", TICKER).split("-", 1)[0], exchange_index=2)
                )
            if path.startswith("portfolio/orders/"):
                if state["read_error"]:
                    raise httpx.ReadTimeout("status unavailable")
                return dict(
                    order=dict(
                        order_id="exchange-order",
                        client_order_id=state["last"]["client_order_id"],
                        ticker=TICKER,
                        status="canceled",
                        fill_count_fp=state.get("fill_count", "2.00"),
                        remaining_count_fp="0.00",
                        taker_fees_dollars="0.0300",
                        maker_fees_dollars="0",
                        maker_fill_cost_dollars="0",
                        taker_fill_cost_dollars=str(float(state.get("fill_count", "2.00")) * 0.9),
                        outcome_side="yes" if state["last"]["side"] == "bid" else "no",
                    )
                )
            if path == "portfolio/balance":
                assert authenticated and params["subaccount"] == 0
                state["balance_params"] = params
                if state.get("balance_error"):
                    raise httpx.ReadTimeout("balance unavailable")
                return state.get("balance_response", dict(balance=10000))
            if path.endswith("/orderbook"):
                return dict(orderbook_fp=dict(yes_dollars=[["0.61", "2.00"]], no_dollars=[["0.37", "3.00"]]))
            now = datetime.now(UTC).timestamp()
            return dict(
                market=dict(
                    ticker=state.get("ticker", TICKER),
                    exchange_index=2,
                    status=state["status"],
                    open_time=datetime.fromtimestamp(now - 900, UTC).isoformat(),
                    close_time=datetime.fromtimestamp(now + 900, UTC).isoformat(),
                    price_ranges=state.get("price_ranges", [dict(start="0.01", end="0.99", step="0.01")]),
                )
            )

        async def pages(self, path, key, params=None, authenticated=False):
            assert authenticated and params["subaccount"] == 0 and params["exchange_index"] == 2
            if path == "portfolio/positions":
                yield dict(ticker=TICKER, exchange_index=2, position_fp=state["position"])
            elif state.get("found"):
                yield (await self.get("portfolio/orders/exchange-order"))["order"]

        async def close(self):
            await self.http.aclose()

    path = tmp_path / "manual.sqlite"
    app = FastAPI()
    manual = install_manual_trading(app, path, settings, Client)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client, state, manual, Client


@pytest.mark.parametrize(
    "action,side,book,price",
    [
        ("buy", "yes", "bid", "0.7000"),
        ("buy", "no", "ask", "0.3000"),
        ("sell", "yes", "ask", "0.7000"),
        ("sell", "no", "bid", "0.3000"),
    ],
)
def test_yes_no_v2_conversion(action, side, book, price):
    body = request_body()
    body.update(action=action, side=side)
    payload = exchange_order(ManualOrder(**body), 2)
    assert payload["side"] == book and payload["price"] == price
    assert payload["reduce_only"] == (action == "sell")
    assert payload["time_in_force"] == ("fill_or_kill" if action == "buy" else "immediate_or_cancel")
    assert payload["count"] == "3.00"
    assert payload["exchange_index"] == 2


def test_live_context_quotes_and_real_holdings(venue):
    client, state, *_ = venue
    state["position"] = "-4.50"
    data = client.get("/api/manual/market", params=dict(ticker=TICKER)).json()
    assert data["holdings"] == dict(yes="0", no="4.50")
    assert data["quotes"]["yes"] == dict(bid="0.61", ask="0.63")
    assert data["quotes"]["no"] == dict(bid="0.37", ask="0.39")
    assert data["balance_cents"] == 10000 and not state["posts"]


def test_partial_sell_reports_exchange_result_and_never_reposts(venue):
    client, state, manual, _ = venue
    state["position"] = "3"
    body = request_body()
    body["action"] = "sell"
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "complete"
    assert row["exchange_order"]["fill_count_fp"] == "2.00"
    assert row["exchange_order"]["taker_fees_dollars"] == "0.0300"
    assert client.post("/api/manual/orders", json=body).json()["id"] == row["id"]
    assert len(state["posts"]) == 1
    changed = {**body, "count": 4}
    assert client.post("/api/manual/orders", json=changed).status_code == 409
    assert len(manual.rows()) == 1


@pytest.mark.parametrize("damage", ["oversell", "opposite", "closed", "wrong_market"])
def test_preflight_rejections_never_submit(venue, damage):
    client, state, *_ = venue
    body = request_body()
    if damage == "oversell":
        body["action"] = "sell"
        state["position"] = "2.00"
    elif damage == "opposite":
        state["position"] = "-2.00"
    elif damage == "closed":
        state["status"] = "closed"
    else:
        state["ticker"] = "OTHER"
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "rejected" and not state["posts"]


def test_sell_no_at_one_cent_caps_price_and_position(venue):
    client, state, *_ = venue
    state["position"] = "-3"
    body = request_body()
    body.update(action="sell", side="no", limit_cents=1)
    client.post("/api/manual/orders", json=body)
    assert state["posts"][0]["reduce_only"] is True
    assert state["posts"][0]["side"] == "bid"
    assert state["posts"][0]["price"] == "0.9900"


@pytest.mark.parametrize("error", ["timeout", 500, 409])
def test_unknown_submission_persists_across_restart_and_blocks_replacement(venue, error):
    client, state, manual, factory = venue
    state["post_error"] = error
    body = request_body()
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "unknown"
    restarted = FastAPI()
    install_manual_trading(restarted, manual.path, manual.settings, factory)
    with TestClient(restarted, base_url=ORIGIN, headers={"Origin": ORIGIN}) as second:
        assert second.post("/api/manual/orders", json=body).json()["state"] == "unknown"
        assert second.post("/api/manual/orders", json=request_body()).status_code == 409
        assert second.get("/api/manual/orders/" + body["client_order_id"]).json()["state"] == "unknown"
    assert len(state["posts"]) == 1
    state["found"] = True
    state["last"] = exchange_order(ManualOrder(**body), 2)
    assert client.get("/api/manual/orders/" + body["client_order_id"]).json()["state"] == "complete"
    assert len(state["posts"]) == 1


def test_rejected_exchange_order_is_not_retried(venue):
    client, state, *_ = venue
    state["post_error"] = 403
    row = client.post("/api/manual/orders", json=request_body()).json()
    assert row["state"] == "rejected" and len(state["posts"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        dict(count=0),
        dict(count=True),
        dict(count=1.001),
        dict(limit_cents=0),
        dict(limit_cents=100),
        dict(limit_cents=True),
        dict(limit_cents="12.345"),
        dict(confirm="PAPER"),
        dict(ticker="../portfolio/orders"),
        dict(action="short"),
        dict(extra=True),
    ],
)
def test_invalid_requests_cannot_submit(venue, changes):
    client, state, *_ = venue
    body = request_body()
    body.update(changes)
    assert client.post("/api/manual/orders", json=body).status_code == 422
    assert not state["posts"]


def test_same_origin_required_and_paper_guard_remains(venue):
    client, state, *_ = venue
    assert (
        client.post(
            "/api/manual/orders", json=request_body(), headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert client.get("/api/manual/status", headers={"Host": "evil.test"}).status_code == 403
    assert client.get("/api/manual/status").headers["cache-control"] == "no-store"
    with pytest.raises(RuntimeError, match="LIVE DISABLED"):
        replace(Settings(), enable_live=True).guard()
    assert not state["posts"]


def test_concurrent_claim_allows_only_one_unresolved_order(venue):
    _, _, manual, _ = venue

    async def claim():
        return await asyncio.gather(
            *(asyncio.to_thread(manual.claim, ManualOrder(**request_body())) for _ in range(2)),
            return_exceptions=True,
        )

    results = asyncio.run(claim())
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert len(manual.rows()) == 1


def test_fractional_remainder_can_be_sold(venue):
    client, state, *_ = venue
    state["position"] = "0.25"
    body = request_body()
    body.update(action="sell", count="0.25")
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "complete"
    assert state["posts"][0]["count"] == "0.25"
    assert state["posts"][0]["reduce_only"] is True


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"balance": 12345}, "123.45"),
        ({"balance": 12345, "balance_dollars": "123.4567"}, "123.4567"),
        ({"balance": 0}, "0"),
    ],
)
def test_account_balance_units_and_primary_account_scope(venue, response, expected):
    client, state, *_ = venue
    state["balance_response"] = response
    result = client.get("/api/manual/balance")
    assert result.status_code == 200
    assert result.json()["available_cash_dollars"] == expected
    assert state["balance_params"] == {"subaccount": 0}
    assert result.headers["cache-control"] == "no-store"
    assert not state["posts"]


def test_account_balance_failure_is_not_reported_as_zero(venue):
    client, state, *_ = venue
    state["balance_error"] = True
    result = client.get("/api/manual/balance")
    assert result.status_code == 502
    assert "available_cash_dollars" not in result.json()
    assert not state["posts"]


def test_subcent_best_ask_is_preserved_in_live_order_payload(venue):
    client, state, *_ = venue
    state["price_ranges"] = [dict(start="0.90", end="1.00", step="0.001")]
    body = request_body()
    body["limit_cents"] = "99.70"
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "complete"
    assert state["posts"][0]["price"] == "0.9970"


def test_subcent_price_must_match_market_tick(venue):
    client, state, *_ = venue
    body = request_body()
    body["limit_cents"] = "70.50"
    row = client.post("/api/manual/orders", json=body).json()
    assert row["state"] == "rejected"
    assert not state["posts"]


@pytest.mark.parametrize("side,price", [("yes", "0.9500"), ("no", "0.0500")])
def test_manual_buy_uses_explicit_cap_and_exchange_all_or_none(venue, side, price):
    client, state, *_ = venue
    body = request_body()
    body.update(side=side, limit_cents=95, count=10)
    client.post("/api/manual/orders", json=body)
    payload = state["posts"][0]
    assert payload["time_in_force"] == "fill_or_kill"
    assert payload["price"] == price and payload["count"] == "10.00"
    assert payload["reduce_only"] is False


def test_purchase_totals_count_fills_by_market_and_side_not_requests(venue):
    _, _, manual, _ = venue
    for side, action, ticker, filled, status in [
        ("yes", "buy", TICKER, "3.25", "complete"),
        ("yes", "buy", TICKER, "2.00", "complete"),
        ("no", "buy", TICKER, "1.50", "accepted"),
        ("yes", "sell", TICKER, "3.00", "complete"),
        ("yes", "buy", TICKER + "-OLD", "10.00", "complete"),
        ("yes", "buy", TICKER, "0", "unknown"),
    ]:
        body = request_body()
        body.update(side=side, action=action, ticker=ticker, count=10)
        row, _ = manual.claim(ManualOrder(**body))
        row.update(state="complete", exchange_order={"fill_count_fp": filled})
        manual.save(row)
        # Apply pending states after seeding; normal claim blocks unresolved orders.
        if status != "complete":
            row["desired_state"] = status
            manual.save(row)
    for row in manual.rows():
        if "desired_state" in row:
            row["state"] = row.pop("desired_state")
            manual.save(row)
    totals = manual.purchases()
    assert totals[TICKER] == dict(yes="5.25", no="1.50", pending=2)
    assert totals[TICKER + "-OLD"] == dict(yes="10.00", no="0", pending=0)
