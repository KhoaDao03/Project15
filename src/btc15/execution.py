"""Conservative paper matching; no network writes."""

import copy
import math
import uuid
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, InvalidOperation
from functools import wraps

from .domain import D, order_direction
from .recovery import contract_hash, evidence_hash, restore_market, validate_final_evidence
from .strategies.settlement_edge.rules import FeeAccumulator, Risk, fee_bound, passive_price


@dataclass
class PaperOrder:
    id: str
    opportunity_id: str
    market: str
    side: str
    limit: float
    quantity: float
    created: float
    eligible: float
    queue: float
    original_queue: float
    remaining: float
    active: bool = True
    fees: FeeAccumulator = field(default_factory=FeeAccumulator)
    entry_evidence: dict | None = None


@dataclass
class Position:
    opportunity_id: str
    market: str
    side: str
    quantity: float = 0
    bought: float = 0
    cost: float = 0
    fees: float = 0
    proceeds: float = 0
    opened: float = 0
    max_favorable: float = 0
    max_adverse: float = 0
    exit_reason: str = ""


def atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if getattr(self, "_atomic_depth", 0):
            return method(self, *args, **kwargs)
        before = copy.deepcopy(self.snapshot())
        self._atomic_depth = 1
        try:
            with self.store.transaction():
                result = method(self, *args, **kwargs)
                after = self.snapshot()
                if after != before:
                    self.store.checkpoint(self.run_id, after)
                return result
        except BaseException:
            self.restore(before)
            raise
        finally:
            self._atomic_depth = 0

    return wrapped


class PaperExecutor:
    def __init__(self, store, run_id, mode, config):
        self.store, self.run_id, self.mode, self.config = store, run_id, mode, config
        from .models import identity

        if mode not in ("PAPER", "BACKTEST"):
            raise ValueError("Paper executor accepts research modes only")
        self.model_identity = identity(config)
        self.risk = Risk(config)
        self.orders, self.positions = {}, {}
        self.seen_trades = set()
        self.last_exit_event = {}
        self.exit_consumed = {}
        self.maker_rates = {}
        self.contracts, self.quarantines = {}, {}

    def snapshot(self):
        return dict(
            maker_rates=self.maker_rates,
            contracts=self.contracts,
            quarantines=self.quarantines,
            config_version=self.config.version,
            mode=self.mode,
            orders={k: asdict(v) for k, v in self.orders.items()},
            positions={k: asdict(v) for k, v in self.positions.items()},
            risk=dict(
                reserved=self.risk.reserved,
                realized=self.risk.realized,
                daily=self.risk.daily,
                halted=self.risk.halted,
            ),
            seen_trades=sorted(self.seen_trades),
            last_exit_event=self.last_exit_event,
            exit_consumed=[[k[0], str(k[1]), str(v)] for k, v in self.exit_consumed.items()],
            exit_eligible=getattr(self, "_exit_eligible", {}),
        )

    def restore(self, data):
        data = copy.deepcopy(data)
        if data["config_version"] != self.config.version or data["mode"] != self.mode:
            raise ValueError("Checkpoint configuration/mode mismatch")
        self.maker_rates = data.get("maker_rates", {})
        self.contracts = data.get("contracts", {})
        self.quarantines = data.get("quarantines", {})
        self.orders = {}
        for k, v in data["orders"].items():
            f = v.pop("fees")
            v["fees"] = FeeAccumulator(f["precision"], D(f["carry"]))
            self.orders[k] = PaperOrder(**v)
        self.positions = {k: Position(**v) for k, v in data["positions"].items()}
        for k, v in data["risk"].items():
            setattr(self.risk, k, v)
        self.seen_trades = set(data["seen_trades"])
        self.last_exit_event = data["last_exit_event"]
        self.exit_consumed = {}
        for market, price, value in data["exit_consumed"]:
            consumed = D(value)
            nearest = consumed.quantize(D(".01"))
            if abs(consumed - nearest) <= D("1e-9"):
                consumed = nearest
            self.exit_consumed[(market, D(price))] = consumed
        self._exit_eligible = data["exit_eligible"]

    @atomic
    def halt(self, now):
        self.risk.halted = True
        for ticker in list(self.orders):
            self.cancel(ticker, now, "kill_switch")

    def restore_daily_history(self):
        for r in self.store.list(kind="order", mode="PAPER", limit=None):
            if not self.same_portfolio(r):
                continue
            if r["body"].get("status") == "submitted":
                d = self.risk.day(r["timestamp"])
                d["trades"] += 1
                d["exposure"] += r["body"].get("risk_reserved", r["body"]["quantity"])
        for r in self.store.list(kind="trade_result", mode="PAPER", limit=None):
            if not self.same_portfolio(r):
                continue
            pnl = r["body"]["net_pnl"]
            self.risk.realized += pnl
            self.risk.day(r["timestamp"])["pnl"] += pnl

    def same_portfolio(self, row):
        model = row["body"].get("model")
        return model is None or model["model_id"] == "settlement-edge"

    def record(self, kind, body, now, market, op):
        body = {**body, "model": self.model_identity, "trade_id": op or None}
        if kind == "fill" and body["action"] == "buy":
            body["total_debit"] = body["quantity"] * body["price"] + body["fee"]
        if kind == "trade_result":
            debit = body["cost"] + body["fees"]
            body["return_on_capital"] = body["net_pnl"] / debit if debit else None
            body["win"] = body["net_pnl"] > 0
        return self.store.add(kind, body, self.run_id, self.mode, now, market, op)

    def state(self, market, target, now, op=""):
        self.store.transition(self.run_id, self.mode, market, target, now, op)

    def reject_submission(self, market, opportunity_id, now, reason, message, **details):
        self.record(
            "execution_rejection",
            dict(
                reason=reason,
                message=message,
                stage="submission",
                config_version=self.config.version,
                details=details,
            ),
            now,
            market.ticker,
            opportunity_id,
        )
        return None

    @atomic
    def submit(
        self,
        market,
        book,
        decision,
        opportunity_id,
        now,
        freshness_ok,
        *,
        entry_evidence=None,
        recheck_reasons=(),
        submission_context=None,
    ):
        def reject(code, message, **details):
            return self.reject_submission(
                market,
                opportunity_id,
                now,
                code,
                message,
                **{**(submission_context or {}), **details},
            )

        c = self.config
        if not c.enabled:
            return reject("STRATEGY_DISABLED", "Strategy entries are disabled")
        if market.ticker in self.quarantines:
            return reject("METADATA_QUARANTINED", "Contract metadata requires settlement recovery")
        if decision["decision"] != "TRADE_CANDIDATE":
            return reject(
                "SIGNAL_NOT_CANDIDATE",
                "Signal did not pass the entry filters",
                decision=decision["decision"],
                signal_reasons=decision.get("reasons", []),
            )
        if not freshness_ok:
            if recheck_reasons:
                first = recheck_reasons[0]
                return reject(first["code"], first["message"], rechecks=list(recheck_reasons))
            return reject(
                "EXECUTION_GATE_BLOCKED", "Caller did not authorize execution; inspect its health checks"
            )
        if not market.tradable(now):
            return reject(
                "MARKET_NOT_TRADABLE",
                "Market is not active at submission time",
                status=market.status,
                checked_at=now,
                open_time=market.open_time,
                close_time=market.close_time,
            )
        remaining = market.close_time - now
        if not c.no_new_entry < remaining <= c.entry_window_start:
            return reject(
                "ENTRY_WINDOW",
                "Submission is outside the configured entry window",
                seconds_remaining=remaining,
                minimum_exclusive=c.no_new_entry,
                maximum_inclusive=c.entry_window_start,
            )
        side = decision["side"]
        if side not in ("yes", "no"):
            return reject("INVALID_SIDE", "A valid YES or NO side is required", side=side)
        if not book.valid:
            return reject("BOOK_INVALID", "Order book has not passed validation")
        if not 0 <= now - book.received <= c.book_max_age:
            return reject(
                "BOOK_RECEIVE_AGE",
                "Book receive age is outside the allowed range",
                actual=now - book.received,
                required=[0, c.book_max_age],
            )
        ask = book.ask(side)
        if ask is None:
            return reject("NO_ASK", "No executable ask is available", side=side)
        conservative = decision["conservative_probability"]
        if (
            type(conservative) not in (int, float)
            or not math.isfinite(conservative)
            or not 0 <= conservative <= 1
        ):
            return reject(
                "INVALID_PROBABILITY",
                "Conservative probability must be a finite number in [0, 1]",
                actual=str(conservative),
            )
        fee = fee_bound(ask, c)
        net_ev = conservative - ask - fee - c.slippage
        required = max(c.min_ev, c.min_edge)
        if net_ev < required:
            return reject(
                "NET_EDGE_RECHECK",
                "Current ask no longer leaves the required net edge",
                ask=ask,
                evaluated_ask=decision.get("expected_fill_price"),
                conservative_probability=conservative,
                estimated_fee=fee,
                slippage=c.slippage,
                actual=net_ev,
                required=required,
            )
        if c.passive and book.bid(side) is None:
            return reject("NO_BID", "Passive pricing requires a bid as well as an ask", side=side)
        try:
            price = (
                passive_price(market, book, side, conservative, c)
                if c.passive
                else market.snap(ask + c.slippage)
            )
        except ValueError as exc:
            return reject(
                "UNSUPPORTED_ENTRY_TICK", "No supported entry price could be selected", detail=str(exc)
            )
        if not market.valid_tick(price):
            return reject(
                "UNSUPPORTED_ENTRY_TICK", "Selected entry price is outside the supported grid", price=price
            )
        if market.ticker in self.positions:
            return reject("EXISTING_POSITION", "This market already has filled inventory")
        if market.ticker in self.orders:
            old = self.orders[market.ticker]
            return reject(
                "ORDER_ALREADY_ATTEMPTED",
                "Only one entry attempt per market/run is allowed",
                order_id=old.id,
                active=old.active,
                remaining=old.remaining,
            )
        sizing = self.risk.size_details(max(price, ask), now)
        quantity = sizing["quantity"]
        if not quantity:
            reason = sizing["reasons"][0]
            return reject(reason["code"], reason["message"], sizing=sizing)
        if not self.store.claim(self.run_id, "entry:" + market.ticker):
            return reject("ENTRY_ALREADY_CLAIMED", "A durable entry claim already exists for this market/run")
        self.contracts.setdefault(market.ticker, asdict(market))
        self.risk.reserve(market.ticker, max(price, ask), quantity, now)
        levels = book.yes if side == "yes" else book.no
        # Round queue ahead up, never executable volume up. Keep numeric checkpoint fields.
        queue = sum((q for p, q in levels.items() if p >= D(price)), D(0)) * D(c.queue_multiplier)
        queue = float(queue.quantize(D(".01"), rounding=ROUND_CEILING))
        order = PaperOrder(
            str(uuid.uuid4()),
            opportunity_id,
            market.ticker,
            side,
            price,
            quantity,
            now,
            now + c.latency_seconds,
            queue,
            queue,
            quantity,
            fees=FeeAccumulator(c.fee_balance_precision),
            entry_evidence=copy.deepcopy(entry_evidence),
        )
        self.orders[market.ticker] = order
        self.record(
            "order",
            dict(
                **{k: v for k, v in asdict(order).items() if k not in ("fees", "entry_evidence")},
                status="submitted",
                theoretical_price=ask,
                passive=c.passive,
                risk_reserved=self.risk.reserved[market.ticker],
            ),
            now,
            market.ticker,
            opportunity_id,
        )
        self.state(market.ticker, "ORDER_PENDING", now, opportunity_id)
        return order

    @atomic
    def cancel(self, market, now, reason):
        order = self.orders.get(market)
        if not order or not order.active:
            return
        order.active = False
        order.entry_evidence = None
        self.record(
            "order",
            dict(id=order.id, status="cancelled", reason=reason, remaining=order.remaining),
            now,
            market,
            order.opportunity_id,
        )
        self.state(market, "ORDER_CANCELLED", now, order.opportunity_id)
        if market in self.positions:
            self.state(market, "POSITION_OPEN", now, order.opportunity_id)
        else:
            self.risk.reserved.pop(market, None)

    def revalidate(self, market, decision, now, healthy):
        order = self.orders.get(market.ticker)
        if (
            order
            and order.active
            and (
                not healthy
                or decision["decision"] != "TRADE_CANDIDATE"
                or now - order.created >= self.config.max_wait
                or market.close_time - now <= self.config.no_new_entry
                or not market.tradable(now)
            )
        ):
            self.cancel(market.ticker, now, "signal_invalid_or_timeout")

    @atomic
    def fill(self, order, quantity, price, now, maker):
        if self.orders.get(order.market) is not order:
            raise ValueError("Stale paper order reference")
        if not D(price).is_finite() or not 0 < price < 1 or price > order.limit:
            raise ValueError("Fill violates order price")
        if not order.active or now < order.eligible or order.market in self.quarantines:
            return
        if not D(quantity).is_finite() or D(quantity) <= 0 or D(quantity) % D(".01"):
            raise ValueError("Invalid fill quantity")
        quantity = float(min(D(quantity), D(order.remaining)).quantize(D(".01")))
        if quantity <= 0:
            return
        if order.entry_evidence is not None:
            evidence = order.entry_evidence
            self.store.add(
                "opportunity",
                evidence,
                self.run_id,
                self.mode,
                evidence["timestamp"],
                order.market,
                order.opportunity_id,
                record_id=order.opportunity_id,
            )
            order.entry_evidence = None
        fee = order.fees.charge(
            price,
            quantity,
            self.maker_rates.get(order.market, self.config.maker_fee_rate)
            if maker
            else self.config.taker_fee_rate,
        )
        order.remaining = float(D(order.remaining) - D(quantity))
        pos = self.positions.setdefault(
            order.market, Position(order.opportunity_id, order.market, order.side, opened=now)
        )
        pos.quantity = float(D(pos.quantity) + D(quantity))
        pos.bought = float(D(pos.bought) + D(quantity))
        pos.cost += quantity * price
        pos.fees += fee
        self.record(
            "fill",
            dict(
                order_id=order.id,
                action="buy",
                side=order.side,
                quantity=quantity,
                price=price,
                fee=fee,
                maker=maker,
                fee_rate=self.maker_rates.get(order.market, self.config.maker_fee_rate)
                if maker
                else self.config.taker_fee_rate,
                balance_precision=self.config.fee_balance_precision,
                latency=now - order.created,
                queue_ahead=order.queue,
                remaining=order.remaining,
                slippage=price - order.limit,
            ),
            now,
            order.market,
            order.opportunity_id,
        )
        if order.remaining <= 0:
            order.active = False
            self.state(order.market, "POSITION_OPEN", now, order.opportunity_id)
        else:
            self.state(order.market, "ORDER_PARTIALLY_FILLED", now, order.opportunity_id)

    @atomic
    def trade(self, market, msg, now):
        tid = msg.get("trade_id")
        if not tid or tid in self.seen_trades:
            return
        self.seen_trades.add(tid)
        order = self.orders.get(market.ticker)
        if not order or not order.active or not self.config.passive or now < order.eligible:
            return
        source = msg.get("ts_ms", 0) / 1000
        if (
            source < order.eligible
            or source > now + self.config.max_clock_skew
            or now - source > self.config.book_max_age
        ):
            return
        taker = msg.get("taker_outcome_side")
        if taker not in ("yes", "no") or taker == order.side:
            return
        yes_price = float(msg["yes_price_dollars"])
        price = yes_price if order.side == "yes" else 1 - yes_price
        if price > order.limit + 1e-9:
            return
        try:
            quantity = D(msg["count_fp"])
        except InvalidOperation as exc:
            raise ValueError("Invalid trade quantity") from exc
        if not quantity.is_finite() or quantity <= 0 or quantity % D(".01"):
            raise ValueError("Invalid trade quantity")
        queue = D(order.queue)
        consumed = min(queue, quantity)
        order.queue = float(queue - consumed)
        # Legacy checkpoints can contain sub-cent queues. Discard a sub-cent residual
        # rather than reject valid source volume or fabricate a minimum-sized fill.
        quantity = (quantity - consumed).quantize(D(".01"), rounding=ROUND_FLOOR)
        if quantity > 0:
            self.fill(order, quantity, order.limit, now, True)

    @atomic
    def observe_exit_liquidity(self, market, book, now, *, continuous, source_time):
        """Account for visible removal of already-consumed depth; never execute a sale.

        Unchanged depth retains its consumption. A drop caps outstanding consumption
        at the remaining displayed quantity (zero when a level disappears). Later
        growth is thus available without resetting the same snapshot on each event.
        A first snapshot after a gap/restart cannot establish continuous depletion.
        """
        ticker = market.ticker
        if (
            not continuous
            or ticker not in self.positions
            or ticker in self.quarantines
            or not book.valid
            or not math.isfinite(source_time)
            or not 0 <= now - book.received <= self.config.book_max_age
            or not -self.config.max_clock_skew <= now - source_time <= self.config.book_max_age
        ):
            return
        side = self.positions[ticker].side
        levels = book.yes if side == "yes" else book.no
        for key, consumed in list(self.exit_consumed.items()):
            if key[0] == ticker:
                displayed = levels.get(key[1], D(0))
                self.exit_consumed[key] = min(D(consumed), displayed)

    @atomic
    def aggressive(self, market, book, now):
        order = self.orders.get(market.ticker)
        if self.config.passive or not order or not order.active or now < order.eligible:
            return
        # Called only on a new book event. IOC: unfilled remainder is cancelled.
        for price, quantity in book.asks(order.side):
            price = market.snap(price + self.config.slippage, up=True)
            if price > order.limit:
                break
            self.fill(order, quantity, price, now, False)
            if not order.active:
                break
        if order.active:
            self.cancel(market.ticker, now, "ioc_remainder")

    @atomic
    def monitor(self, market, book, probability, now, event_id):
        pos = self.positions.get(market.ticker)
        if not pos or market.ticker in self.quarantines or not book.valid or not market.tradable(now):
            return
        bid = book.bid(pos.side)
        if bid is None or now - book.received > self.config.book_max_age:
            return
        entry = pos.cost / pos.bought
        mark = bid - entry
        pos.max_favorable = max(pos.max_favorable, mark)
        pos.max_adverse = min(pos.max_adverse, mark)
        c = self.config
        # The engine may authorize price-only risk reduction while its model is
        # warming up or unavailable. Missing/invalid probability is not a zero.
        conservative = probability.get("conservative_" + pos.side) if probability else None
        model_available = (
            type(conservative) in (int, float) and math.isfinite(conservative) and 0 <= conservative <= 1
        )
        try:
            target = market.snap(c.take_profit, up=True) if c.take_profit is not None else None
        except ValueError:
            target = None  # Unreachable TP never suppresses the independent hard stop.
            self.record(
                "exit_warning",
                {"reason": "UNSUPPORTED_TAKE_PROFIT_TICK"},
                now,
                market.ticker,
                pos.opportunity_id,
            )
        reason = (
            "HARD_STOP"
            if bid <= entry * c.stop_multiplier
            else "TAKE_PROFIT"
            if target is not None and bid >= target
            else "INVALIDATION"
            if model_available
            and (
                conservative < c.exit_probability
                or conservative - (bid - fee_bound(bid, c) - c.slippage) < c.min_hold_ev
            )
            else ""
        )
        if not reason:
            return
        # An exit consumes each observed depth event at most once, after modeled latency.
        if self.last_exit_event.get(market.ticker) == event_id:
            return
        if not pos.exit_reason:
            pos.exit_reason = reason
            self.last_exit_event[market.ticker] = event_id
            self.record(
                "exit_intent",
                dict(reason=reason, eligible=now + c.latency_seconds),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            self._exit_eligible = getattr(self, "_exit_eligible", {})
            self._exit_eligible[market.ticker] = now + c.latency_seconds
            return
        if now < self._exit_eligible[market.ticker]:
            return
        self.last_exit_event[market.ticker] = event_id
        self.cancel(market.ticker, now, "exit_requested")
        self.state(market.ticker, "EXITING", now, pos.opportunity_id)
        levels = book.yes if pos.side == "yes" else book.no
        fees = FeeAccumulator(c.fee_balance_precision)
        for price, quantity in sorted(levels.items(), reverse=True):
            key = (market.ticker, price)
            consumed = D(self.exit_consumed.get(key, 0))
            available = max(D(0), quantity - consumed)
            position_quantity = D(pos.quantity)
            q = min(position_quantity, available).quantize(D(".01"), rounding=ROUND_FLOOR)
            if q <= 0 or float(price) <= c.slippage:
                continue
            fill_price = market.snap(float(price) - c.slippage)
            if reason == "TAKE_PROFIT" and fill_price < target:
                continue
            self.exit_consumed[key] = consumed + q
            q_float = float(q)
            fee = fees.charge(fill_price, q_float, c.taker_fee_rate, "sell")
            remaining = (position_quantity - q).quantize(D(".01"))
            pos.quantity = float(remaining)
            pos.proceeds += q_float * fill_price
            pos.fees += fee
            self.record(
                "fill",
                dict(
                    action="sell",
                    side=pos.side,
                    quantity=q_float,
                    price=fill_price,
                    fee=fee,
                    reason=reason,
                ),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            if remaining == 0:
                self.finish(market.ticker, now, reason)
                return
        self.state(market.ticker, "POSITION_OPEN", now, pos.opportunity_id)

    def finish(self, market, now, reason, settlement_result=None):
        pos = self.positions.pop(market)
        pnl = pos.proceeds - pos.cost - pos.fees
        self.risk.close(market, pnl, now)
        self.record(
            "trade_result",
            dict(
                **asdict(pos),
                gross_pnl=pos.proceeds - pos.cost,
                net_pnl=pnl,
                holding_seconds=now - pos.opened,
                reason=reason,
                settlement_result=settlement_result,
                settlement_timestamp=now if settlement_result is not None else None,
            ),
            now,
            market,
            pos.opportunity_id,
        )
        self.state(market, "CLOSED", now, pos.opportunity_id)

    @atomic
    def quarantine(self, market, now, reason, observed=None):
        """Block trading without discarding the identity or accounting of inventory."""
        ticker = market.ticker
        state = self.store.state(self.run_id, ticker)
        if state in ("CLOSED", "ERROR"):
            return
        if state == "HALTED" and ticker not in self.quarantines and reason != "LEGACY_METADATA_QUARANTINE":
            # A later metadata issue cannot relabel an unrelated operator/error halt.
            return
        self.contracts.setdefault(ticker, asdict(market))
        if ticker in self.quarantines:
            return
        self.cancel(ticker, now, reason)
        body = dict(
            reason=reason,
            since=now,
            expected_contract_hash=contract_hash(restore_market(self.contracts[ticker])),
        )
        self.quarantines[ticker] = body
        order = self.orders.get(ticker)
        op = order.opportunity_id if order else ""
        self.record("metadata_quarantine", dict(**body, observed=observed), now, ticker, op)
        self.state(ticker, "HALTED", now, op)

    def _blocked_settlement(self, ticker, now, result, reason, evidence_id=None):
        # Repeated WS hints/polls are not new accounting, nor a reconnect request.
        key = f"settlement-blocked:{ticker}:{result}:{reason}:{evidence_id}"
        if self.store.claim(self.run_id, key):
            order = self.orders.get(ticker)
            self.record(
                "settlement_blocked",
                dict(result=result, reason=reason, evidence_id=evidence_id),
                now,
                ticker,
                order.opportunity_id if order else "",
            )
        return "BLOCKED"

    @atomic
    def settle(self, market, result, now, *, evidence=None, review=None):
        ticker = market.ticker
        market = restore_market(self.contracts[ticker]) if ticker in self.contracts else market
        if not math.isfinite(now) or now < market.close_time or result not in ("yes", "no"):
            raise ValueError("Premature/invalid settlement")
        prior = self.store.list(kind="settlement", run_id=self.run_id, market=ticker, limit=1)
        if prior:
            if prior[0]["body"]["result"] != result:
                return self._blocked_settlement(ticker, now, result, "CONFLICTING_SETTLEMENT_RESULT")
            return "ALREADY_SETTLED"
        state = self.store.state(self.run_id, ticker)
        if state == "ERROR" or (state == "HALTED" and ticker not in self.quarantines):
            return self._blocked_settlement(ticker, now, result, "UNCLASSIFIED_QUARANTINE")
        final, evidence_id, digest = None, None, None
        if evidence is not None:
            digest = evidence_hash(evidence)
            existing = self.store.list(
                kind="settlement_evidence", run_id=self.run_id, market=ticker, limit=None
            )
            record = next((r for r in existing if r["body"]["evidence_hash"] == digest), None)
            evidence_id = (
                record["id"]
                if record
                else self.record(
                    "settlement_evidence",
                    dict(
                        result=result,
                        evidence=evidence,
                        evidence_hash=digest,
                        expected_contract_hash=contract_hash(market),
                    ),
                    now,
                    ticker,
                    self.orders[ticker].opportunity_id if ticker in self.orders else "",
                )
            )
            try:
                final = validate_final_evidence(market, result, now, evidence)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.quarantine(market, now, "FINAL_METADATA_INVALID", evidence)
                return self._blocked_settlement(ticker, now, result, str(exc), evidence_id)
            if contract_hash(final) != contract_hash(market):
                self.quarantine(market, now, "FINAL_METADATA_CHANGED", evidence)
        recovery_id = None
        if ticker in self.quarantines:
            if final is None:
                return self._blocked_settlement(ticker, now, result, "FINAL_REST_METADATA_REQUIRED")
            if review is not None:
                if (
                    review.get("evidence_id") != evidence_id
                    or review.get("evidence_hash") != digest
                    or not isinstance(review.get("reason"), str)
                    or not review["reason"].strip()
                ):
                    raise ValueError("Explicit evidence review is required")
            elif contract_hash(final) != contract_hash(market):
                return self._blocked_settlement(
                    ticker, now, result, "CHANGED_TERMS_REVIEW_REQUIRED", evidence_id
                )
            elif any(
                r["body"]["result"] != result
                for r in self.store.list(
                    kind="settlement_evidence", run_id=self.run_id, market=ticker, limit=None
                )
            ):
                return self._blocked_settlement(
                    ticker, now, result, "CONFLICTING_FINAL_EVIDENCE", evidence_id
                )
            recovery_id = self.record(
                "settlement_recovery",
                dict(
                    result=result,
                    evidence_id=evidence_id,
                    evidence_hash=digest,
                    expected_contract_hash=contract_hash(market),
                    final_contract_hash=contract_hash(final),
                    method="operator_review" if review else "matching_final_metadata",
                    review=review,
                ),
                now,
                ticker,
                self.orders[ticker].opportunity_id if ticker in self.orders else "",
            )
        elif review is not None:
            raise ValueError("Only a quarantined contract can use settlement recovery")
        if not self.store.claim(self.run_id, "settlement:" + ticker):
            return "ALREADY_SETTLED"
        self.record(
            "settlement",
            dict(
                result=result,
                **(dict(evidence_id=evidence_id, recovery_id=recovery_id) if evidence_id else {}),
            ),
            now,
            ticker,
            "",
        )
        self.cancel(ticker, now, "settlement")
        pos = self.positions.get(ticker)
        if recovery_id:
            # This audited escape can only enter settlement, never reopen trading.
            self.store.transition(
                self.run_id,
                self.mode,
                ticker,
                "SETTLEMENT_PENDING",
                now,
                pos.opportunity_id if pos else "",
                settlement_recovery_id=recovery_id,
            )
        if pos:
            self.state(ticker, "SETTLEMENT_PENDING", now, pos.opportunity_id)
            pos.proceeds += pos.quantity * (1 if pos.side == result else 0)
            pos.quantity = 0
            self.finish(ticker, now, "SETTLEMENT", result)
        elif self.store.state(self.run_id, ticker) not in ("CLOSED", "HALTED", "ERROR"):
            self.state(ticker, "SETTLEMENT_PENDING", now)
            self.state(ticker, "CLOSED", now)
        self.quarantines.pop(ticker, None)
        return "SETTLED"


def live_payload(market, action, side, price, quantity, client_order_id):
    if not market.valid_tick(price) or quantity <= 0 or D(quantity).as_tuple().exponent < -2:
        raise ValueError("Invalid live order")
    book_side, yes_price = order_direction(action, side, price)
    return dict(
        ticker=market.ticker,
        client_order_id=client_order_id,
        side=book_side,
        count=f"{quantity:.2f}",
        price=f"{yes_price:.4f}",
        time_in_force="immediate_or_cancel",
        self_trade_prevention_type="taker_at_cross",
        cancel_order_on_pause=True,
        reduce_only=action == "sell",
        exchange_index=market.exchange_index,
    )


class LiveTrader:
    def submit(self, *args, **kwargs):
        raise RuntimeError("LIVE submission disabled pending explicit approval and validation")
