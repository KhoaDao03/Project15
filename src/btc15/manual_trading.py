"""User-confirmed real orders, isolated from the automated paper executor."""

import asyncio
import json
import re
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .api import KalshiClient, read_timings
from .config import Settings
from .domain import timestamp

TICKER = re.compile(r"KX(?:BTC|ETH|SOL|XRP)15M-[A-Z0-9-]{1,60}\Z")
UNRESOLVED = ("submitting", "accepted", "unknown")


def confirmed_resting(row):
    return (
        row.get("resting_take_profit") is True
        and row.get("origin") == "bot"
        and row["state"] == "accepted"
        and (row.get("exchange_order") or {}).get("status") == "resting"
        and not row.get("cancel_requested_at")
    )


def snap_buy_limit(limit, side, ranges):
    """Snap down in the purchased outcome's price space on the venue YES grid."""
    yes_limit = limit if side == "yes" else 1 - limit
    prices = []
    for band in ranges:
        start, end, step = (Decimal(band[k]) for k in ("start", "end", "step"))
        if step <= 0:
            raise ValueError("Invalid tick grid")
        if side == "yes":
            target = min(end, yes_limit)
            price = start + ((target - start) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        else:
            target = max(start, yes_limit)
            price = start + ((target - start) / step).to_integral_value(rounding=ROUND_CEILING) * step
        if start <= price <= end and 0 < price < 1:
            selected = price if side == "yes" else 1 - price
            if selected <= limit:
                prices.append(selected)
    if not prices:
        raise HTTPException(409, "No supported buy limit within the price cap")
    return max(prices)


class ManualOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_order_id: UUID
    ticker: str
    action: Literal["buy", "sell"]
    side: Literal["yes", "no"]
    count: Decimal = Field(gt=0, le=100000, max_digits=8, decimal_places=2)
    limit_cents: Decimal = Field(gt=0, lt=100, max_digits=4, decimal_places=2)
    confirm: Literal["REAL_MONEY"]


def exchange_order(order, exchange_index, *, resting=False):
    """V2 always quotes the YES book, including orders for NO contracts."""
    price = Decimal(order.limit_cents) / 100
    return dict(
        ticker=order.ticker,
        client_order_id=str(order.client_order_id),
        side="bid" if (order.action == "buy") == (order.side == "yes") else "ask",
        price=f"{price if order.side == 'yes' else 1 - price:.4f}",
        count=f"{order.count:.2f}",
        time_in_force="good_till_canceled"
        if resting
        else ("fill_or_kill" if order.action == "buy" else "immediate_or_cancel"),
        reduce_only=order.action == "sell",
        self_trade_prevention_type="taker_at_cross",
        cancel_order_on_pause=True,
        subaccount=0,
        exchange_index=exchange_index,
    )


def local_request(request: Request, response: Response):
    if request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "Manual trading is available only on the local dashboard")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site requests are not allowed")
    if request.method == "POST" and (
        request.headers.get("origin") != str(request.base_url).rstrip("/")
        or request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        raise HTTPException(403, "Use this dashboard to confirm a real-money order")
    response.headers["Cache-Control"] = "no-store"


class ManualTrading:
    def __init__(self, path, settings=None, client_factory=KalshiClient):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings or Settings.env()
        self.client_factory = client_factory
        self.order_lock = asyncio.Lock()
        self.before_manual_order = None
        self._client = None
        self.fill_wakeup = asyncio.Event()
        self.fill_stream = dict(connected=False, last_event_at=None)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS manual_orders (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

            for name, expression in (
                ("state", "$.state"),
                ("ticker", "$.request.ticker"),
            ):
                db.execute(
                    f"CREATE INDEX IF NOT EXISTS manual_orders_{name} "
                    f"ON manual_orders(json_extract(body, '{expression}'))"
                )

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rows(self, *, ticker=None, tickers=None, unresolved=False, origin=None, limit=None):
        """Read only the needed journal records; never cache authorization/order state."""
        clauses, parameters = [], []
        if ticker is not None:
            clauses.append("json_extract(body, '$.request.ticker') = ?")
            parameters.append(ticker)
        if tickers is not None:
            if not tickers:
                return []
            clauses.append(
                "json_extract(body, '$.request.ticker') IN (" + ",".join("?" for _ in tickers) + ")"
            )
            parameters.extend(tickers)
        if unresolved:
            clauses.append("json_extract(body, '$.state') IN (" + ",".join("?" for _ in UNRESOLVED) + ")")
            parameters.extend(UNRESOLVED)
        if origin is not None:
            clauses.append("json_extract(body, '$.origin') = ?")
            parameters.append(origin)
        query = "SELECT body FROM manual_orders"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY rowid DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute(query, parameters)]

    def row(self, order_id):
        with self.db() as db:
            result = db.execute("SELECT body FROM manual_orders WHERE id=?", (order_id,)).fetchone()
        return json.loads(result[0]) if result else None

    def purchases(self):
        """Cumulative confirmed dashboard buy fills by market, not current holdings."""
        totals = {}
        with self.db() as db:
            rows = db.execute(
                "SELECT json_extract(body, '$.request.ticker'), "
                "json_extract(body, '$.request.side'), json_extract(body, '$.state'), "
                "coalesce(json_extract(body, '$.exchange_order.fill_count_fp'), '0') "
                "FROM manual_orders WHERE json_extract(body, '$.request.action') = 'buy' "
                "ORDER BY rowid DESC"
            ).fetchall()
        for ticker, side, state, quantity in rows:
            market = totals.setdefault(ticker, dict(yes=Decimal(0), no=Decimal(0), pending=0))
            filled = Decimal(quantity)
            if not filled.is_finite() or filled < 0:
                raise ValueError("Invalid recorded fill quantity")
            market[side] += filled
            market["pending"] += state in UNRESOLVED
        return {
            ticker: dict(yes=str(v["yes"]), no=str(v["no"]), pending=v["pending"])
            for ticker, v in totals.items()
        }

    def save(self, row):
        row["updated_at"] = time.time()
        with self.db() as db:
            current = db.execute("SELECT body FROM manual_orders WHERE id=?", (row["id"],)).fetchone()
            if current:
                previous = json.loads(current[0])
                if previous.get("resting_take_profit"):
                    if previous["state"] == "complete" and row["state"] in UNRESOLVED:
                        return previous
                    old_fill = Decimal((previous.get("exchange_order") or {}).get("fill_count_fp", "0"))
                    new_fill = Decimal((row.get("exchange_order") or {}).get("fill_count_fp", "0"))
                    if new_fill < old_fill:
                        raise ValueError("Resting sale fill count regressed; reconciliation required")
                    if previous.get("cancel_requested_at"):
                        row["cancel_requested_at"] = previous["cancel_requested_at"]
                row["timing"] = {**previous.get("timing", {}), **row.get("timing", {})}
                if "fill_notification" in previous:
                    row["fill_notification"] = previous["fill_notification"]
            db.execute("UPDATE manual_orders SET body=? WHERE id=?", (json.dumps(row), row["id"]))
        return row

    def claim(self, order, origin="manual", reason=None, *, resting=False):
        payload = order.model_dump(mode="json")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM manual_orders WHERE id=? OR json_extract(body, '$.state') IN ("
                    + ",".join("?" for _ in UNRESOLVED)
                    + ")",
                    (str(order.client_order_id), *UNRESOLVED),
                )
            ]
            for row in rows:
                if row["id"] == str(order.client_order_id):
                    if row["request"] != payload or bool(row.get("resting_take_profit")) != resting:
                        raise HTTPException(409, "This order ID was already used for different instructions")
                    return row, False
            if any(
                r["state"] in UNRESOLVED
                and not (confirmed_resting(r) and r["request"]["ticker"] != order.ticker)
                and (origin != "bot" or order.action != "sell" or r["request"]["ticker"] == order.ticker)
                for r in rows
            ):
                raise HTTPException(409, "Check the unresolved order before submitting another real order")
            row = dict(
                id=str(order.client_order_id),
                request=payload,
                state="submitting",
                created_at=time.time(),
                origin=origin,
                automation_reason=reason,
            )
            if resting:
                row["resting_take_profit"] = True
            db.execute("INSERT INTO manual_orders VALUES (?, ?)", (row["id"], json.dumps(row)))
        return row, True

    @asynccontextmanager
    async def client(self):
        if not self.settings.api_key_id or not self.settings.private_key_path:
            raise HTTPException(503, "Configure Kalshi credentials on the server first")
        # The manual ticket is explicitly production/primary-account only.
        if self.settings.rest_url != Settings.rest_url:
            raise HTTPException(503, "Manual trading requires the configured Kalshi production endpoint")
        try:
            if self._client is None:
                self._client = self.client_factory(self.settings)
            client = self._client
        except (OSError, ValueError):
            raise HTTPException(503, "Kalshi credentials could not be loaded") from None
        yield client

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    def receive_fill(self, message):
        """Report authenticated fills without changing authoritative order/retry state."""
        if message.get("type") != "fill":
            return False
        fill = message.get("msg", {})
        client_id = fill.get("client_order_id")
        if not client_id or not fill.get("trade_id") or not fill.get("order_id"):
            return False
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("SELECT body FROM manual_orders WHERE id=?", (client_id,)).fetchone()
            if not result:
                return False
            row = json.loads(result[0])
            request = row["request"]
            if (
                fill.get("market_ticker") != request["ticker"]
                or fill.get("exchange_index") != row.get("exchange_index")
                or fill.get("subaccount") != 0
                or fill.get("action") != request["action"]
                or fill.get("purchased_side", fill.get("side")) != request["side"]
                or (row.get("order_id") and fill["order_id"] != row["order_id"])
            ):
                return False
            note = row.get("fill_notification", dict(trade_ids=[], count_fp="0", order_id=fill["order_id"]))
            if note["order_id"] != fill["order_id"] or fill["trade_id"] in note["trade_ids"]:
                return False
            try:
                count = Decimal(fill["count_fp"])
                total = Decimal(note["count_fp"]) + count
                if not count.is_finite() or count <= 0 or total > Decimal(request["count"]):
                    return False
            except (KeyError, InvalidOperation, ValueError, TypeError):
                return False
            now = time.time()
            note.update(count_fp=str(total), received_at=now, exchange_ts_ms=fill.get("ts_ms"))
            note["trade_ids"].append(fill["trade_id"])
            row["fill_notification"] = note
            row.setdefault("timing", {}).setdefault("first_fill_received_at", now)
            row["timing"]["last_fill_received_at"] = now
            db.execute("UPDATE manual_orders SET body=? WHERE id=?", (json.dumps(row), client_id))
        self.fill_wakeup.set()
        return True

    async def watch_fills(self):
        if not self.settings.api_key_id or not self.settings.private_key_path:
            return
        while True:
            try:
                async with self.client() as client:
                    async with websockets.connect(
                        self.settings.ws_url,
                        additional_headers=client.headers("GET", urlparse(self.settings.ws_url).path),
                        ping_interval=20,
                        ping_timeout=20,
                        max_queue=64,
                    ) as socket:
                        await socket.send(
                            json.dumps(dict(id=1, cmd="subscribe", params=dict(channels=["fill"])))
                        )
                        async for raw in socket:
                            message = json.loads(raw)
                            if message.get("type") == "error":
                                raise ValueError("Fill subscription rejected")
                            if message.get("type") == "subscribed":
                                self.fill_stream.update(connected=True, error=None)
                                self.fill_wakeup.set()  # REST also covers events missed during disconnects.
                            if message.get("type") == "fill":
                                self.fill_stream["last_event_at"] = time.time()
                                self.receive_fill(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.fill_stream["error"] = type(exc).__name__
            finally:
                self.fill_stream["connected"] = False
            await asyncio.sleep(2)

    async def market(self, client, ticker):
        if not TICKER.fullmatch(ticker):
            raise HTTPException(422, "Select a BTC, ETH, SOL or XRP 15-minute contract")
        series_ticker = ticker.split("-", 1)[0]
        series = (await client.get("series/" + series_ticker))["series"]
        exchange_index = series.get("exchange_index")
        if series.get("ticker") != series_ticker or type(exchange_index) is not int or exchange_index < 0:
            raise HTTPException(409, "Exchange identity could not be verified")
        market = (await client.get("markets/" + ticker, {"exchange_index": exchange_index}))["market"]
        if market.get("ticker") != ticker or market.get("exchange_index") != exchange_index:
            raise HTTPException(409, "Market identity could not be verified")
        if market.get("status") != "active" or not timestamp(market["open_time"]) <= time.time() < timestamp(
            market["close_time"]
        ):
            raise HTTPException(409, "This contract is not open for trading")
        return market

    async def holdings(self, client, ticker, exchange_index):
        rows = [
            r
            async for r in client.pages(
                "portfolio/positions",
                "market_positions",
                dict(ticker=ticker, subaccount=0, exchange_index=exchange_index),
                True,
            )
        ]
        value = sum(
            (
                Decimal(r["position_fp"])
                for r in rows
                if r["ticker"] == ticker and r.get("exchange_index") == exchange_index
            ),
            Decimal(0),
        )
        if not value.is_finite():
            raise ValueError("Invalid position")
        return dict(yes=str(max(value, 0)), no=str(max(-value, 0)))

    async def context(self, ticker):
        try:
            async with self.client() as client:
                market = await self.market(client, ticker)
                holdings = await self.holdings(client, ticker, market["exchange_index"])
                balance = await client.get(
                    "portfolio/balance", {"subaccount": 0, "exchange_index": market["exchange_index"]}, True
                )
                book = (await client.get("markets/" + ticker + "/orderbook"))["orderbook_fp"]
                bids = {
                    side: max(
                        (Decimal(p) for p, q in book[side + "_dollars"] if Decimal(q) > 0), default=None
                    )
                    for side in ("yes", "no")
                }
                quotes = {
                    side: dict(
                        bid=str(bids[side]) if bids[side] is not None else None,
                        ask=str(1 - bids[other]) if bids[other] is not None else None,
                    )
                    for side, other in (("yes", "no"), ("no", "yes"))
                }
                return dict(
                    ticker=ticker,
                    title=market.get("title"),
                    close_time=market["close_time"],
                    holdings=holdings,
                    balance_cents=balance["balance"],
                    quotes=quotes,
                    server_time=time.time(),
                    account="Kalshi production · primary account",
                )
        except (httpx.HTTPError, KeyError, ValueError, InvalidOperation):
            raise HTTPException(
                502, "Could not verify live market/account data with Kalshi; no order sent"
            ) from None

    async def reconcile(self, client, row):
        if "exchange_index" not in row:
            return row
        if row.get("order_id"):
            order = (
                await client.get(
                    "portfolio/orders/" + row["order_id"], {"exchange_index": row["exchange_index"]}, True
                )
            )["order"]
        else:
            order = None
            async for candidate in client.pages(
                "portfolio/orders",
                "orders",
                dict(ticker=row["request"]["ticker"], subaccount=0, exchange_index=row["exchange_index"]),
                True,
            ):
                if candidate.get("client_order_id") == row["id"]:
                    order = candidate
                    break
        if order is None:
            return row  # Absence is not proof that a timed-out request was rejected.
        if order.get("client_order_id") != row["id"] or order.get("ticker") != row["request"]["ticker"]:
            raise ValueError("Order identity mismatch")
        row.update(order_id=order["order_id"], exchange_order=order)
        row["state"] = "complete" if order["status"] in ("executed", "canceled") else "accepted"
        if row["state"] == "complete":
            row.setdefault("timing", {}).setdefault("fill_confirmed_at", time.time())
        row["message"] = "Exchange order status: " + order["status"]
        return self.save(row)

    async def cancel_resting(self, row):
        """Cancel our resting take-profit and verify final cumulative fills before replacing."""
        async with self.order_lock:
            row = self.row(row["id"])
            if not row.get("resting_take_profit") or row.get("origin") != "bot":
                raise ValueError("Only bot resting take-profit orders may be canceled here")
            if row["state"] not in UNRESOLVED:
                return row
            row.setdefault("cancel_requested_at", time.time())
            self.save(row)
            async with self.client() as client:
                if not row.get("order_id"):
                    row = await self.reconcile(client, row)
                if row["state"] not in UNRESOLVED or not row.get("order_id"):
                    return row
                path = "portfolio/events/orders/" + row["order_id"]
                headers = client.headers("DELETE", urlparse(client.settings.rest_url).path + "/" + path)
                row["cancel_attempted_at"] = time.time()
                self.save(row)
                try:
                    response = await client.http.delete(
                        path, params={"exchange_index": row["exchange_index"]}, headers=headers
                    )
                    if response.status_code != 404:
                        response.raise_for_status()
                except httpx.HTTPError:
                    # A lost cancellation response may still have canceled or filled the order.
                    # Only authoritative reconciliation can allow another sale.
                    row["message"] = "Cancellation unconfirmed; reconciling before any replacement"
                    self.save(row)
                return await self.reconcile(client, row)

    async def submit(self, order, *, authorize=None, reason=None, timing=None, resting=False):
        if resting and not (
            authorize
            and order.action == "sell"
            and order.limit_cents == 99
            and reason == "RESTING_TAKE_PROFIT"
        ):
            raise ValueError("Resting orders are restricted to the bot's 99-cent take-profit")
        if authorize is None and self.before_manual_order:
            self.before_manual_order(order.ticker)
        timing = dict(timing or {}, submission_requested_at=time.time())
        wait_start = time.monotonic()
        async with self.order_lock:
            timing["coordination_wait_ms"] = (time.monotonic() - wait_start) * 1000
            timing["reads"] = []
            token = read_timings.set(timing["reads"])
            try:
                return await self._submit(
                    order, authorize=authorize, reason=reason, timing=timing, resting=resting
                )
            finally:
                read_timings.reset(token)

    async def _submit(self, order, *, authorize=None, reason=None, timing=None, resting=False):
        if not TICKER.fullmatch(order.ticker):
            raise HTTPException(422, "Unsupported market")
        row, fresh = self.claim(
            order, origin="bot" if authorize else "manual", reason=reason, resting=resting
        )
        if not fresh:
            return row  # Repeated clicks/requests must never send another order.
        row["timing"] = dict(timing or {}, preflight_started_at=time.time())
        self.save(row)
        preflight_start = time.monotonic()
        sent = False
        try:
            async with self.client() as client:
                market = await self.market(client, order.ticker)
                balance = None
                if authorize and order.action == "buy":
                    order = order.model_copy(
                        update={
                            "limit_cents": snap_buy_limit(
                                order.limit_cents / 100, order.side, market.get("price_ranges", [])
                            )
                            * 100
                        }
                    )
                    row["effective_limit_cents"] = str(order.limit_cents)
                    async with asyncio.TaskGroup() as group:
                        positions_task = group.create_task(
                            self.holdings(client, order.ticker, market["exchange_index"])
                        )
                        balance_task = group.create_task(
                            client.get(
                                "portfolio/balance",
                                {"subaccount": 0, "exchange_index": market["exchange_index"]},
                                True,
                            )
                        )
                    holdings, balance = positions_task.result(), balance_task.result()
                else:
                    holdings = await self.holdings(client, order.ticker, market["exchange_index"])
                if order.action == "sell" and Decimal(holdings[order.side]) < order.count:
                    raise HTTPException(409, "Sell quantity exceeds your real position on this side")
                opposite = "no" if order.side == "yes" else "yes"
                if order.action == "buy" and Decimal(holdings[opposite]) > 0:
                    raise HTTPException(
                        409, "Sell the opposite position first; buying this side would net it out"
                    )
                if authorize:
                    if order.action == "buy" and any(Decimal(v) > 0 for v in holdings.values()):
                        raise HTTPException(409, "Automatic entry requires a flat real position")
                    if order.action == "buy":
                        cash = (
                            Decimal(balance["balance_dollars"])
                            if "balance_dollars" in balance
                            else Decimal(balance["balance"]) / 100
                        )
                        price = order.limit_cents / 100
                        maximum_cost = order.count * (price + Decimal(".07") * price * (1 - price)) + Decimal(
                            ".01"
                        )
                        if not cash.is_finite() or cash < maximum_cost:
                            raise HTTPException(409, "Insufficient real cash for automatic entry and fees")
                payload = exchange_order(order, market["exchange_index"], resting=resting)
                row["exchange_index"] = market["exchange_index"]
                self.save(row)
                price = Decimal(payload["price"])
                if not any(
                    Decimal(b["start"]) <= price <= Decimal(b["end"])
                    and (price - Decimal(b["start"])) % Decimal(b["step"]) == 0
                    for b in market.get("price_ranges", [])
                ):
                    raise HTTPException(422, "Limit price is not on this market's tick grid")
                path = "portfolio/events/orders"
                headers = client.headers("POST", urlparse(client.settings.rest_url).path + "/" + path)
                row["timing"].update(
                    preflight_completed_at=time.time(),
                    preflight_ms=(time.monotonic() - preflight_start) * 1000,
                )
                self.save(row)
                if authorize:
                    authorize()  # Recheck after journal I/O, immediately before the POST.
                row["timing"]["submitted_at"] = time.time()
                post_start = time.monotonic()
                sent = True
                # No retry: network failures and 5xx may have accepted the order.
                response = await client.http.post(path, json=payload, headers=headers)
                row["timing"].update(
                    acknowledged_at=time.time(), submission_ms=(time.monotonic() - post_start) * 1000
                )
                if response.status_code in (400, 401, 403, 404, 422, 429):
                    row.update(
                        state="rejected",
                        message=f"Kalshi rejected the order (HTTP {response.status_code}); check balance, permissions and price",
                    )
                    return self.save(row)
                response.raise_for_status()
                ack = response.json()
                if ack.get("client_order_id") != row["id"] or not ack.get("order_id"):
                    raise ValueError("Unverified order acknowledgement")
                row.update(
                    state="accepted",
                    order_id=ack["order_id"],
                    acknowledgement=ack,
                    message="Submitted. Check status to confirm fills and cancellation.",
                )
                self.save(row)
                return await self.reconcile(client, row)
        except HTTPException as exc:
            row.update(state="rejected", message=exc.detail)
            return self.save(row)
        except (httpx.HTTPError, KeyError, ValueError, InvalidOperation, RuntimeError, ExceptionGroup):
            row.update(
                state="unknown" if sent else "rejected",
                message="Submission outcome unconfirmed. Check status; do not submit a replacement."
                if sent
                else "Preflight failed; no order sent. Check Kalshi connectivity and credentials.",
            )
            return self.save(row)


def install_manual_trading(app, path, settings=None, client_factory=KalshiClient):
    manual = ManualTrading(path, settings, client_factory)
    router = APIRouter(prefix="/api/manual", dependencies=[Depends(local_request)])

    @router.get("/status")
    def status():
        return dict(
            configured=bool(manual.settings.api_key_id and manual.settings.private_key_path),
            orders=manual.rows(limit=50),
            environment="production",
            fill_stream=manual.fill_stream,
            automated_live_enabled=False,
            live_controls_available=manual.before_manual_order is not None,
        )

    @router.get("/balance")
    async def balance():
        try:
            async with asyncio.timeout(15), manual.client() as client:
                data = await client.get("portfolio/balance", {"subaccount": 0}, True)
                # No exchange filter: show cash across the primary account's exchanges.
                cash = (
                    Decimal(data["balance_dollars"])
                    if "balance_dollars" in data
                    else Decimal(data["balance"]) / 100
                )
                if not cash.is_finite():
                    raise ValueError("Invalid balance")
                return dict(
                    available_cash_dollars=str(cash),
                    account="Kalshi production · primary account",
                    server_time=time.time(),
                )
        except (httpx.HTTPError, TimeoutError, KeyError, ValueError, InvalidOperation):
            raise HTTPException(502, "Real account balance is temporarily unavailable") from None

    @router.get("/market")
    async def market(ticker: str):
        return await manual.context(ticker)

    @router.post("/orders")
    async def submit(order: ManualOrder):
        return await manual.submit(order)

    @router.get("/orders/{client_order_id}")
    async def check(client_order_id: UUID):
        row = manual.row(str(client_order_id))
        if row is None:
            raise HTTPException(404, "Manual order not found")
        if row["state"] in UNRESOLVED:
            try:
                async with manual.client() as client:
                    row = await manual.reconcile(client, row)
            except (httpx.HTTPError, KeyError, ValueError, InvalidOperation):
                raise HTTPException(
                    502, "Unable to confirm the order yet; do not submit a replacement"
                ) from None
        return row

    app.include_router(router)
    return manual
