"""Persistent per-asset real execution, with per-market position management."""

import asyncio
import fcntl
import json
import logging
import math
import time
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .decision_notifications import DecisionListener
from .domain import timestamp
from .live_loss_guard import LIMIT, daily_pnl
from .manual_trading import (  # noqa: F401
    UNRESOLVED,
    ManualOrder,
    confirmed_resting,
    local_request,
    snap_buy_limit,
)
from .operation import health
from .strategies.settlement_edge.bleep import capped_confidence

log = logging.getLogger(__name__)


def consumes_entry_attempt(row):
    timing = row.get("timing") or {}
    # Only refund a known pre-submit rejection. Older records without timing,
    # exchange responses and uncertain submissions remain conservatively counted.
    return not (
        row.get("state") == "rejected"
        and "preflight_started_at" in timing
        and "submitted_at" not in timing
        and not any(row.get(k) for k in ("order_id", "acknowledgement", "exchange_order"))
    )


class LiveControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    enabled: StrictBool
    contracts: StrictInt = Field(ge=1, le=20)
    revision: StrictInt = Field(ge=0)
    confirm: str = ""


class LiveAutomation:
    def __init__(self, manual, members, stores):
        self.manual, self.members, self.stores = manual, members, stores
        self.running = False
        self.last_cycle = None
        self.messages = {}
        self.lock_file = None
        with manual.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS live_controls (ticker TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS live_assets (asset TEXT PRIMARY KEY, body TEXT NOT NULL)")
        # Carry existing user switch choices into the persistent asset controls.
        policies = self.assets()
        latest = {}
        for control in self.controls().values():
            asset = control["asset"]
            if asset not in latest or control["close_time"] > latest[asset]["close_time"]:
                latest[asset] = control
        for asset, control in latest.items():
            if asset not in policies:
                self.write_asset(
                    dict(
                        asset=asset,
                        enabled=control["enabled"] and not control.get("paused", False),
                        contracts=control["contracts"],
                        revision=control["revision"],
                        config_version=control["config_version"],
                        stop_price=control["stop_price"],
                    )
                )
        manual.before_manual_order = self.takeover

    def controls(self):
        with self.manual.db() as db:
            return {
                ticker: json.loads(body)
                for ticker, body in db.execute("SELECT ticker,body FROM live_controls")
            }

    def control(self, ticker):
        """Read one control, including its latest authorization revision."""
        with self.manual.db() as db:
            row = db.execute("SELECT body FROM live_controls WHERE ticker=?", (ticker,)).fetchone()
        return json.loads(row[0]) if row else None

    def cycle_controls(self):
        """Five recent markets per asset, plus older unresolved/open obligations."""
        controls = self.controls()
        selected = {}
        counts = {}
        for control in sorted(controls.values(), key=lambda c: (c["close_time"], c["ticker"]), reverse=True):
            asset = control["asset"]
            if counts.get(asset, 0) < 5:
                selected[control["ticker"]] = control
                counts[asset] = counts.get(asset, 0) + 1
        held = {}
        now = time.time()
        active = [ticker for ticker, control in controls.items() if control["close_time"] > now]
        obligations = {r["id"]: r for r in self.manual.rows(tickers=active, origin="bot")}
        obligations.update({r["id"]: r for r in self.manual.rows(unresolved=True, origin="bot")})
        for row in obligations.values():
            if row.get("origin") != "bot":
                continue
            ticker = row["request"]["ticker"]
            if ticker not in controls:
                continue
            if row["state"] in UNRESOLVED:
                selected[ticker] = controls[ticker]
            quantity = Decimal((row.get("exchange_order") or {}).get("fill_count_fp", "0"))
            held[ticker] = held.get(ticker, Decimal(0)) + (
                quantity if row["request"]["action"] == "buy" else -quantity
            )
        now = time.time()
        for ticker, quantity in held.items():
            if quantity > 0 and controls[ticker]["close_time"] > now:
                selected[ticker] = controls[ticker]
        return sorted(selected.values(), key=lambda c: not bool(c.get("exit_reason")))

    def write(self, control):
        with self.manual.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO live_controls VALUES (?,?)", (control["ticker"], json.dumps(control))
            )

    def assets(self):
        with self.manual.db() as db:
            return {
                asset: json.loads(body) for asset, body in db.execute("SELECT asset,body FROM live_assets")
            }

    def write_asset(self, policy):
        with self.manual.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO live_assets VALUES (?,?)", (policy["asset"], json.dumps(policy))
            )

    def check_daily_loss(self, asset, now):
        policy = self.assets().get(asset)
        day = int(now // 86400)
        if policy and policy.get("loss_guard", {}).get("day") == day:
            raise HTTPException(409, "Daily live loss limit reached; new buys disabled")
        try:
            rows = [r for r in self.manual.rows() if r["request"]["ticker"].startswith(f"KX{asset}15M-")]
            settlements = {}
            if any(Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0 for r in rows):
                member = self.members[asset]
                settlements = {
                    r["market"]: r
                    for r in self.stores[asset].list("settlement", member["run_id"], "PAPER", limit=None)
                }
            for control in self.controls().values():
                if control["asset"] != asset or not day * 86400 <= control["close_time"] <= now:
                    continue
                ticker = control["ticker"]
                orders = [r for r in rows if r["request"]["ticker"] == ticker]
                bought = sum(
                    (
                        Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0"))
                        for r in orders
                        if r.get("origin") == "bot" and r["request"]["action"] == "buy"
                    ),
                    Decimal(0),
                )
                sold = sum(
                    (
                        Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0"))
                        for r in orders
                        if r["request"]["action"] == "sell"
                    ),
                    Decimal(0),
                )
                if bought > sold and ticker not in settlements:
                    raise ValueError("Waiting for official settlement of live remainder")
            pnl = daily_pnl(rows, settlements, now)
        except Exception as exc:
            raise HTTPException(
                409, "Daily live P&L unavailable; new buys blocked, exits remain active"
            ) from exc
        if pnl > LIMIT:
            return
        if policy:
            policy.update(
                enabled=False,
                revision=policy["revision"] + 1,
                loss_guard=dict(
                    day=day,
                    pnl=str(pnl),
                    triggered_at=now,
                    reason="Daily live loss reached $20 (UTC); new buys disabled",
                ),
            )
            # Persist policy first: entry() checks it even if interrupted here.
            self.write_asset(policy)
            for control in self.controls().values():
                if control["asset"] == asset:
                    control.update(enabled=False, revision=control["revision"] + 1)
                    self.write(control)
        raise HTTPException(409, "Daily live loss limit reached; new buys disabled")

    def sync_markets(self):
        controls = self.controls()
        for asset, policy in self.assets().items():
            if not policy["enabled"]:
                continue
            member = self.members[asset]
            snapshot = self.stores[asset].read_market_display() or {}
            if snapshot.get("run_id") != member["run_id"]:
                continue
            for market in snapshot.get("markets", []):
                if market["ticker"] in controls or timestamp(market["close_time"]) <= time.time():
                    continue
                self.write(
                    dict(
                        ticker=market["ticker"],
                        asset=asset,
                        enabled=True,
                        contracts=policy["contracts"],
                        revision=1,
                        paused=False,
                        config_version=policy["config_version"],
                        stop_price=policy["stop_price"],
                        close_time=timestamp(market["close_time"]),
                    )
                )

    def market_info(self, ticker):
        for asset, member in self.members.items():
            snapshot = self.stores[asset].read_market_display() or {}
            for market in snapshot.get("markets", []):
                if market["ticker"] == ticker:
                    return asset, member, snapshot, market
        raise HTTPException(409, "This market is no longer in the current collector feed")

    def configure(self, request):
        current = self.controls().get(request.ticker)
        asset, member, snapshot, market = self.market_info(request.ticker)
        policy = self.assets().get(asset)
        if request.revision != (policy or {}).get("revision", 0):
            raise HTTPException(409, "Controls changed; refresh before applying settings")
        if request.enabled:
            self.check_daily_loss(asset, time.time())
            if request.confirm != "ENABLE_REAL_TRADING":
                raise HTTPException(
                    422, "Confirm enabling real-money automation for this asset and future markets"
                )
            if not self.running:
                raise HTTPException(409, "Live worker is not running")
            if not self.manual.settings.api_key_id or not self.manual.settings.private_key_path:
                raise HTTPException(409, "Real trading credentials are not configured")
        if timestamp(market["close_time"]) <= time.time():
            raise HTTPException(409, "Market has closed")
        control = dict(
            current or {},
            ticker=request.ticker,
            asset=asset,
            enabled=request.enabled,
            contracts=request.contracts,
            revision=request.revision + 1,
            paused=False if request.enabled else (current or {}).get("paused", False),
            config_version=member["config"].version,
            stop_price=member["config"].fixed_stop_price,
            close_time=timestamp(market["close_time"]),
        )
        self.write_asset(
            dict(
                asset=asset,
                enabled=request.enabled,
                contracts=request.contracts,
                revision=request.revision + 1,
                config_version=member["config"].version,
                stop_price=member["config"].fixed_stop_price,
            )
        )
        for previous in self.controls().values():
            if previous["asset"] == asset and previous["ticker"] != request.ticker:
                previous.update(
                    enabled=request.enabled and not previous.get("paused", False),
                    contracts=request.contracts,
                    revision=previous["revision"] + 1,
                )
                self.write(previous)
        control["revision"] = (current or {}).get("revision", 0) + 1
        self.write(control)
        return dict(control, revision=request.revision + 1)

    def takeover(self, ticker):
        control = self.controls().get(ticker)
        if control:
            policy = self.assets().get(control["asset"])
            if policy:
                policy.update(enabled=False, revision=policy["revision"] + 1)
                self.write_asset(policy)
                for previous in self.controls().values():
                    if previous["asset"] == control["asset"] and previous["ticker"] != ticker:
                        previous.update(enabled=False, revision=previous["revision"] + 1)
                        self.write(previous)
            control.update(enabled=False, paused=True, revision=control["revision"] + 1)
            self.write(control)
            self.messages[ticker] = "Manual control: automatic buys and exits paused"

    def prepare_shutdown(self, asset=None):
        if asset is not None and asset not in self.members:
            raise HTTPException(404, "Unknown asset")
        orders = [
            r
            for r in self.manual.rows()
            if asset is None or r["request"]["ticker"].startswith(f"KX{asset}15M-")
        ]
        if any(r.get("resting_take_profit") and r["state"] in UNRESOLVED for r in orders):
            raise HTTPException(409, "Wait for the resting take-profit order cancellation to be confirmed")
        for control in self.controls().values():
            if asset is not None and control["asset"] != asset:
                continue
            if control.get("paused") or time.time() >= control["close_time"]:
                continue
            rows = [
                r for r in orders if r.get("origin") == "bot" and r["request"]["ticker"] == control["ticker"]
            ]
            held = sum(
                (
                    Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0"))
                    * (1 if r["request"]["action"] == "buy" else -1)
                    for r in rows
                ),
                Decimal(0),
            )
            if held > 0 or any(r["state"] in UNRESOLVED for r in rows):
                raise HTTPException(
                    409,
                    "Live position or order still managed; close it or take manual control before stopping feeds",
                )
        for policy in self.assets().values():
            if asset is not None and policy["asset"] != asset:
                continue
            policy.update(enabled=False, revision=policy["revision"] + 1)
            self.write_asset(policy)
        # Invalidate a buy that is still in preflight before the collectors stop.
        for control in self.controls().values():
            if asset is not None and control["asset"] != asset:
                continue
            control.update(enabled=False, revision=control["revision"] + 1)
            self.write(control)

    def state(self):
        return dict(
            running=self.running,
            last_cycle=self.last_cycle,
            max_contracts=20,
            controls=self.controls(),
            assets=self.assets(),
            messages=self.messages,
            exit_policy=dict(resting_take_profit=0.99, cancel_resting_below=0.70, rearm_resting=False),
        )

    def book(self, control, now):
        asset, member, snapshot, market = self.market_info(control["ticker"])
        if (
            snapshot.get("run_id") != member["run_id"]
            or not snapshot.get("connected")
            or not 0 <= now - snapshot.get("published_at", 0) <= 2
            or not market.get("fresh")
        ):
            raise HTTPException(409, "Waiting for a fresh connected market book")
        return market["book"]

    def entry(self, control, now):
        asset = control["asset"]
        self.check_daily_loss(asset, now)
        member = self.members[asset]
        config = member["config"]
        if not control["enabled"] or control.get("paused") or control["config_version"] != config.version:
            raise HTTPException(409, "New buys disabled or strategy changed; review live controls")
        policy = self.assets().get(asset)
        if policy and not policy["enabled"]:
            raise HTTPException(409, "Asset live buys disabled")
        report = health(self.stores[asset], member["run_id"], now)
        if not report["healthy"]:
            raise HTTPException(409, "Collector health blocks automatic entry")
        record = self.stores[asset].read_market_display("evaluation:" + member["run_id"]) or {}
        d = record.get("body", {})
        if (
            record.get("market") != control["ticker"]
            or d.get("versions", {}).get("config") != config.version
            or not 0 <= now - d.get("timestamp", 0) <= 2
            or not 0 <= now - d.get("model_evaluated_at", 0) <= 2
        ):
            raise HTTPException(409, "Waiting for a fresh matching strategy decision")
        # Paper inventory and its cooldown do not represent the real account.
        reasons = [
            r for r in d.get("reasons", []) if r["code"] not in ("EXISTING_ENTRY", "POST_CLOSE_COOLDOWN")
        ]
        if reasons or d.get("decision") not in ("NO_TRADE", "TRADE_CANDIDATE"):
            raise HTTPException(409, "Strategy entry filters: " + ", ".join(r["code"] for r in reasons))
        remaining = control["close_time"] - now
        if not config.entry_cutoff < remaining <= config.entry_window_start:
            raise HTTPException(409, "Outside entry window")
        side = d.get("side")
        p = d.get("probability", {}).get("p_" + str(side))
        if (
            side not in ("yes", "no")
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or p < config.probability_floor(remaining <= config.no_new_entry)
        ):
            raise HTTPException(409, "Probability floor not met")
        book = self.book(control, now)
        ask, bid = book.get(side + "_ask"), book.get(side + "_bid")
        confidence = capped_confidence(p, bid, ask, asset)
        if confidence is None or confidence < config.probability_floor(remaining <= config.no_new_entry):
            raise HTTPException(409, "Confidence floor not met after market-respect cap")
        if (
            ask is None
            or bid is None
            or not config.min_entry_price <= ask <= config.max_entry_price
            or not 0 < Decimal(str(ask)) - Decimal(str(bid)) <= Decimal(str(config.max_spread))
        ):
            raise HTTPException(409, "Current entry price/spread outside strategy limits")
        # The tick grid is validated again against venue metadata before submission.
        # Allow one cent of execution headroom while retaining the quoted entry-price filter.
        limit = min(Decimal(".99"), Decimal(str(config.max_entry_price)) + Decimal(".01"))
        return side, limit, d

    async def reconcile(self):
        pending = [
            r
            for r in self.manual.rows(unresolved=True)
            if r["state"] in UNRESOLVED
            and (
                not confirmed_resting(r)
                or time.time() - r["updated_at"] >= 1
                or r.get("fill_notification", {}).get("received_at", 0) > r["updated_at"]
            )
        ]
        if pending:
            controls = self.controls()
            async with self.manual.client() as client:
                for row in pending:
                    try:
                        control = controls.get(row["request"]["ticker"], {})
                        if row.get("resting_take_profit") and time.time() >= control.get(
                            "close_time", float("inf")
                        ):
                            await self.manual.cancel_resting(row)
                        else:
                            await self.manual.reconcile(client, row)
                    except Exception:
                        self.messages[row["request"]["ticker"]] = (
                            "Order outcome unresolved; waiting for exchange reconciliation"
                        )
                        log.exception("Order reconciliation unavailable")
        return bool(self.manual.rows(unresolved=True, limit=1))

    async def manage_resting(self, control):
        """Keep one resting offer, or cancel/reconcile it before an IOC exit or takeover."""
        rows = [
            r
            for r in self.manual.rows(ticker=control["ticker"], origin="bot", unresolved=True)
            if r.get("origin") == "bot"
            and r["request"]["ticker"] == control["ticker"]
            and r.get("resting_take_profit")
            and r["state"] in UNRESOLVED
        ]
        if not rows:
            return False
        bid = None
        try:
            bid = self.book(control, time.time()).get(rows[0]["request"]["side"] + "_bid")
        except HTTPException:
            pass
        if bid is not None and bid < 0.70:
            control["resting_disabled"] = True
        if not control.get("paused") and bid is not None:
            if bid <= control["stop_price"]:
                control["exit_reason"] = "HARD_STOP"
            elif bid >= 0.99 and not control.get("exit_reason"):
                control["exit_reason"] = "TAKE_PROFIT"
        cancel = (
            control.get("paused")
            or time.time() >= control["close_time"]
            or control.get("resting_disabled")
            or control.get("exit_reason")
            or any(r.get("cancel_requested_at") for r in rows)
        )
        if cancel:
            self.write(control)
            for row in rows:
                await self.manual.cancel_resting(row)
            remaining = [
                r
                for r in self.manual.rows(ticker=control["ticker"], unresolved=True)
                if r["id"] in {v["id"] for v in rows} and r["state"] in UNRESOLVED
            ]
            if remaining:
                self.messages[control["ticker"]] = (
                    "Resting sale cancellation pending; verifying remaining contracts"
                )
                return True
            return False  # Caller reloads cumulative fills before sizing an IOC replacement.
        self.messages[control["ticker"]] = (
            "99¢ resting sale active; monitoring fills and the 70¢ cancellation threshold"
        )
        return True

    async def step_market(self, control):
        ticker = control["ticker"]
        control = self.control(ticker) or control
        now = time.time()
        timing = {}
        resting = False
        # Historical controls are numerous. Do not scan the order journal for each expired market.
        # Any expired resting orders are handled centrally by reconcile().
        if now >= control["close_time"]:
            self.messages[ticker] = "Market closed; remaining contracts settle at the exchange"
            return
        if await self.manage_resting(control):
            return
        control = self.control(ticker) or control
        if control.get("paused"):
            return
        if now >= control["close_time"]:
            self.messages[ticker] = "Market closed; remaining contracts settle at the exchange"
            return
        orders = self.manual.rows(ticker=ticker, origin="bot")
        buys = [r for r in orders if r["request"]["action"] == "buy"]

        def filled(r):
            return Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0"))

        bought = sum((filled(r) for r in buys), Decimal(0))
        sold = sum((filled(r) for r in orders if r["request"]["action"] == "sell"), Decimal(0))
        held = max(Decimal(0), bought - sold)
        if bought and not held:
            self.messages[ticker] = "Bot position closed; no further buys in this market"
            return
        if any(
            r["state"] in UNRESOLVED
            and (not held or r["request"]["ticker"] == ticker)
            and not (confirmed_resting(r) and r["request"]["ticker"] != ticker)
            for r in self.manual.rows(unresolved=True)
        ):
            self.messages[ticker] = "Order outcome pending; reconciling before any replacement"
            return
        if held:
            side = next(r["request"]["side"] for r in buys if filled(r) > 0)
            reason = control.get("exit_reason")
            try:
                bid = self.book(control, now).get(side + "_bid")
            except HTTPException:
                if not reason:
                    raise
                bid = None
            # Fresh quotes establish a trigger; a committed exit survives feed loss.
            if bid is not None and bid <= control["stop_price"]:
                reason = "HARD_STOP"
            elif not reason and bid is not None and bid >= 0.99:
                reason = "TAKE_PROFIT"
            if reason and reason != control.get("exit_reason"):
                control["exit_reason"] = reason
                self.write(control)
            if not reason:
                if bid is not None and bid < 0.70:
                    control["resting_disabled"] = True
                    self.write(control)
                if control.get("resting_disabled") or any(r.get("resting_take_profit") for r in orders):
                    self.messages[ticker] = (
                        f"Holding {held} {side.upper()} · stop {control['stop_price'] * 100:g}¢ / take profit 99¢"
                    )
                    return
                resting = True
                reason = "RESTING_TAKE_PROFIT"
            sells = [
                r for r in orders if r["request"]["action"] == "sell" and not r.get("resting_take_profit")
            ]
            if sells and now - max(r["created_at"] for r in sells) < 2:
                return
            async with self.manual.client() as client:
                market = await self.manual.market(client, ticker)
                positions = await self.manual.holdings(client, ticker, market["exchange_index"])
            count = min(held, Decimal(positions[side]))
            if count <= 0:
                self.messages[ticker] = (
                    f"Exit pending · journal remainder {held}; exchange reports zero holdings, rechecking"
                )
                return
            limit = Decimal(".01") if reason == "HARD_STOP" else Decimal(".99")
            action = "sell"
        else:
            if not control["enabled"]:
                return
            config = self.members[control["asset"]]["config"]
            attempts = [r for r in buys if consumes_entry_attempt(r)]
            if len(attempts) >= config.max_entry_retries + 1:
                self.messages[ticker] = "Unfilled entry attempt limit reached"
                return
            if buys and now - max(r["created_at"] for r in buys) < config.entry_retry_cooldown:
                return
            side, limit, decision = self.entry(control, now)
            timing = dict(decision_at=decision.get("timestamp"), decision_detected_at=time.time())
            count = Decimal(control["contracts"])
            action = "buy"
            reason = "STRATEGY_ENTRY"
        revision = control["revision"]

        def authorize():
            current = self.control(ticker)
            if not self.running or not current or current["revision"] != revision or current.get("paused"):
                raise HTTPException(409, "Automatic submission interrupted by live controls")
            if time.time() >= current["close_time"]:
                raise HTTPException(409, "Market closed")
            if action == "buy":
                latest_side, latest_limit, _ = self.entry(current, time.time())
                if latest_side != side or latest_limit < limit or not 1 <= count <= 20:
                    raise HTTPException(409, "Entry changed before submission")
            elif current.get("exit_reason") != reason:
                if not resting or current.get("exit_reason") or current.get("resting_disabled"):
                    raise HTTPException(409, "Exit instructions changed before submission")
                bid = self.book(current, time.time()).get(side + "_bid")
                if bid is None or not 0.70 <= bid < 0.99:
                    raise HTTPException(409, "Resting sale price conditions changed before submission")

        order = ManualOrder(
            client_order_id=uuid4(),
            ticker=ticker,
            action=action,
            side=side,
            count=count,
            limit_cents=limit * 100,
            confirm="REAL_MONEY",
        )
        result = await self.manual.submit(
            order, authorize=authorize, reason=reason, timing=timing, resting=resting
        )
        self.messages[ticker] = result["state"] + " · " + result.get("message", "")
        if (
            action == "buy"
            and result["state"] not in UNRESOLVED
            and Decimal((result.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0
        ):
            await self.step_market(self.control(ticker))
        if (
            action == "buy"
            and result["state"] not in UNRESOLVED
            and consumes_entry_attempt(result)
            and Decimal((result.get("exchange_order") or {}).get("fill_count_fp", "0")) == 0
            and self.members[control["asset"]]["config"].entry_retry_cooldown == 0
        ):
            # Revalidate immediately after a confirmed empty attempt. The journal's
            # submitted-attempt count bounds this to the initial buy plus retries.
            # Pre-submit failures return to the worker loop for fresh validation.
            await self.step_market(self.control(ticker))

    async def run(self):
        self.lock_file = open(str(self.manual.path) + ".live.lock", "a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            self.lock_file = None
            log.error("Another live worker owns this journal")
            return
        self.running = True
        listener = DecisionListener(
            [
                store.engine.url.database
                for store in self.stores.values()
                if hasattr(store, "engine")
                and store.engine.dialect.name == "sqlite"
                and store.engine.url.database not in (None, ":memory:")
            ],
            self.manual.fill_wakeup,
        )
        listener.start()
        fill_task = asyncio.create_task(self.manual.watch_fills())
        loss_checked_at = 0
        try:
            while True:
                self.last_cycle = time.time()
                try:
                    self.sync_markets()
                    await self.reconcile()
                    if time.time() - loss_checked_at >= 1:
                        for asset, policy in self.assets().items():
                            if policy["enabled"]:
                                try:
                                    self.check_daily_loss(asset, time.time())
                                except HTTPException as exc:
                                    for control in self.controls().values():
                                        if control["asset"] == asset:
                                            self.messages[control["ticker"]] = str(exc.detail)
                        loss_checked_at = time.time()
                    # Prioritize already committed exits.
                    controls = self.cycle_controls()
                    for control in controls:
                        try:
                            await self.step_market(control)
                        except HTTPException as exc:
                            self.messages[control["ticker"]] = str(exc.detail)
                        except Exception:
                            self.messages[control["ticker"]] = (
                                "Exchange/data unavailable; no replacement until reconciled"
                            )
                            log.exception("Live market processing failed")
                except Exception:
                    log.exception("Live reconciliation failed; submissions paused")
                try:
                    await asyncio.wait_for(self.manual.fill_wakeup.wait(), timeout=0.1)
                except TimeoutError:
                    pass
                self.manual.fill_wakeup.clear()
        finally:
            self.running = False
            listener.close()
            fill_task.cancel()
            await asyncio.gather(fill_task, return_exceptions=True)
            self.lock_file.close()
            self.lock_file = None


def install_live_automation(app, manual, members, stores):
    live = LiveAutomation(manual, members, stores)
    router = APIRouter(prefix="/api/live", dependencies=[Depends(local_request)])

    @router.get("/status")
    def status():
        return live.state()

    @router.post("/control")
    async def control(request: LiveControl):
        return live.configure(request)

    @router.post("/takeover/{ticker}")
    async def takeover(ticker: str):
        live.takeover(ticker)
        return live.state()

    app.include_router(router)
    return live
