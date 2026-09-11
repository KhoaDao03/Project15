"""Conservative paper matching; no network writes."""

import copy
import math
import uuid
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, InvalidOperation
from functools import wraps

from .domain import D, order_direction
from .recovery import contract_hash, evidence_hash, restore_market, validate_final_evidence
from .strategies.settlement_edge.economics import entry_economics
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
    reference_ask: float | None = None
    attempt: int = 1
    cycle: int = 1
    completed_at: float | None = None
    cancelled_at: float | None = None
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
        self.venue_pauses = {}
        # Receipt state belongs to this process, never to a resumable portfolio.
        self.latest_reference_receipt = None
        self.processed_reference_id = None
        self._reference_waits = {}

    def pending_reference(self):
        receipt = self.latest_reference_receipt
        return receipt if receipt and receipt[0] != self.processed_reference_id else None

    def wait_for_reference(self, order, now):
        receipt = self.pending_reference()
        if receipt is None:
            return False
        if self._reference_waits.get(order.id) != receipt[0]:
            self.record(
                "entry_revalidation_wait",
                dict(
                    reason="REFERENCE_UPDATE_PENDING",
                    order_id=order.id,
                    reference_event_id=receipt[0],
                    reference_received=receipt[1],
                ),
                now,
                order.market,
                order.opportunity_id,
            )
            self._reference_waits[order.id] = receipt[0]
        return True

    def snapshot(self):
        return dict(
            maker_rates=self.maker_rates,
            contracts=self.contracts,
            quarantines=self.quarantines,
            venue_pauses=self.venue_pauses,
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
        self.venue_pauses = data.get("venue_pauses", {})
        self.orders = {}
        for k, v in data["orders"].items():
            f = v.pop("fees")
            v["fees"] = FeeAccumulator(f["precision"], D(f["carry"]))
            self.orders[k] = PaperOrder(**v)
        self.positions = {k: Position(**v) for k, v in data["positions"].items()}
        for ticker, order in self.orders.items():
            if order.completed_at is None and not order.active and ticker not in self.positions:
                results = self.store.list(
                    kind="trade_result", run_id=self.run_id, opportunity_id=order.opportunity_id, limit=1
                )
                if results:
                    order.completed_at = results[0]["timestamp"]
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

    @atomic
    def pause_market(self, market, now, event, *, source="lifecycle"):
        """Persist a venue restriction independently of replaceable quote validity."""
        if not math.isfinite(now):
            raise ValueError("Invalid venue pause time")
        ticker = market.ticker
        previous = self.venue_pauses.get(ticker)
        # Neither a later activation hint nor an old message can release a terminal
        # restriction. Legitimate changed-close recovery uses the metadata path.
        if previous and (
            now <= previous["since"]
            or previous["event"] in ("closed", "determined", "disputed", "amended", "finalized")
        ):
            return
        self.contracts.setdefault(ticker, asdict(market))
        self.cancel(ticker, now, "venue_" + event)
        pause = dict(
            event=event,
            since=now,
            source=source,
            contract_hash=contract_hash(restore_market(self.contracts[ticker])),
        )
        self.venue_pauses[ticker] = pause
        order = self.orders.get(ticker)
        self.record("market_pause", pause, now, ticker, order.opportunity_id if order else "")

    @atomic
    def resume_market(self, market, now, *, request_started_at, source, clock_ok):
        """Require a new active REST observation, never a quote or activation hint.

        A poll started before the most recent pause/activation notification is
        in-flight evidence and cannot authorize resumption. The engine invalidates
        the old book on success, so matching still awaits a post-confirmation book.
        """
        ticker = market.ticker
        pause = self.venue_pauses.get(ticker)
        if not pause:
            return False
        if (
            pause["event"] not in ("deactivated", "inactive", "activated", "price_level_structure_updated")
            or ticker in self.quarantines
            or self.store.state(self.run_id, ticker) in ("HALTED", "ERROR", "CLOSED", "SETTLEMENT_PENDING")
            or source != "kalshi_rest"
            or not clock_ok
            or type(request_started_at) not in (int, float)
            or not math.isfinite(request_started_at)
            or not pause["since"] < request_started_at <= now
            or not market.tradable(now)
            or contract_hash(market) != pause["contract_hash"]
        ):
            return False
        order = self.orders.get(ticker)
        self.record(
            "market_resume",
            dict(
                pause=pause,
                source=source,
                request_started_at=request_started_at,
                contract_hash=contract_hash(market),
                status=market.status,
            ),
            now,
            ticker,
            order.opportunity_id if order else "",
        )
        del self.venue_pauses[ticker]
        return True

    def restore_daily_history(self):
        for r in self.store.list(kind="order", mode="PAPER", limit=None):
            if not self.same_portfolio(r):
                continue
            if r["body"].get("status") == "submitted":
                d = self.risk.day(r["timestamp"])
                d["trades"] += 1
                d["exposure"] = float(
                    D(d["exposure"]) + D(r["body"].get("risk_reserved", r["body"]["quantity"]))
                )
        for r in self.store.list(kind="trade_result", mode="PAPER", limit=None):
            if not self.same_portfolio(r):
                continue
            pnl = r["body"]["net_pnl"]
            self.risk.realized = float(D(self.risk.realized) + D(pnl))
            day = self.risk.day(r["timestamp"])
            day["pnl"] = float(D(day["pnl"]) + D(pnl))

    def same_portfolio(self, row):
        model = row["body"].get("model")
        return model is None or model["model_id"] == "settlement-edge"

    def execution_observation(self, book, now):
        book = book if book is not None else getattr(self, "audit_book", None)
        received, reference = getattr(self, "audit_input", (None, None))
        observation = dict(
            processing_lag_seconds=max(0, now - received) if received is not None else None,
            reference_age_seconds=now - reference if reference is not None else None,
        )
        if book is not None:
            observation.update(
                book_received_age_seconds=now - book.received,
                book_source_age_seconds=now - book.source_time if book.source_time else None,
                yes_bid=book.bid("yes"),
                no_bid=book.bid("no"),
                yes_ask=book.ask("yes"),
                no_ask=book.ask("no"),
                yes_ask_quantity=book.ask_level("yes")[1],
                no_ask_quantity=book.ask_level("no")[1],
                yes_bid_quantity=book.yes.get(D(book.bid("yes")), D(0)) if book.bid("yes") is not None else 0,
                no_bid_quantity=book.no.get(D(book.bid("no")), D(0)) if book.bid("no") is not None else 0,
            )
        return observation

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

    def reentry_ready(self, ticker, now):
        order = self.orders.get(ticker)
        return bool(
            order
            and not order.active
            and order.completed_at is not None
            and now - order.completed_at >= self.config.entry_retry_cooldown
            and ticker not in self.positions
            and ticker not in self.quarantines
            and ticker not in self.venue_pauses
            and not self.risk.halted
        )

    def retry_ready(self, ticker, now):
        order = self.orders.get(ticker)
        return bool(
            order
            and not order.active
            and order.remaining == order.quantity
            and ticker not in self.positions
            and ticker not in self.quarantines
            and ticker not in self.venue_pauses
            and not self.risk.halted
            and order.attempt <= self.config.max_entry_retries
            and order.cancelled_at is not None
            and now - order.cancelled_at >= self.config.entry_retry_cooldown
            and self.store.state(self.run_id, ticker) == "ORDER_CANCELLED"
        )

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
        receipt = self.pending_reference()
        if receipt is not None:
            return reject(
                "REFERENCE_UPDATE_PENDING",
                "A received reference update must be processed before entry",
                reference_event_id=receipt[0],
                reference_received=receipt[1],
            )
        if not c.enabled:
            return reject("STRATEGY_DISABLED", "Strategy entries are disabled")
        if market.ticker in self.quarantines:
            return reject("METADATA_QUARANTINED", "Contract metadata requires settlement recovery")
        if market.ticker in self.venue_pauses:
            return reject(
                "VENUE_PAUSED",
                "Market trading is paused or awaiting fresh venue confirmation",
                pause=self.venue_pauses[market.ticker],
            )
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
        if not c.entry_cutoff < remaining <= c.entry_window_start:
            return reject(
                "ENTRY_WINDOW",
                "Submission is outside the configured entry window",
                seconds_remaining=remaining,
                minimum_exclusive=c.entry_cutoff,
                maximum_inclusive=c.entry_window_start,
            )
        if c.sustained_lead_enabled:
            late = c.late_entry_enabled and remaining <= c.no_new_entry
            lead = decision.get("lead", {})
            path = "late_settlement" if late else "standard"
            threshold = c.late_min_lead_sigma if late else c.min_lead_sigma
            if (
                decision.get("entry_path") != path
                or lead.get("side") != decision.get("side")
                or lead.get("lead_sigma", 0) < threshold
                or not lead.get("confirmed_late" if late else "confirmed_normal")
                or (
                    c.entry_probability_deductions
                    and lead.get("stressed_probability", 0) < c.probability_floor(late)
                )
            ):
                return reject(
                    "LEAD_RECHECK",
                    "Current entry path requires confirmed settlement lead and applicable probability evidence",
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
        if not D(c.min_entry_price) <= D(ask) <= D(c.max_entry_price):
            return reject("ENTRY_PRICE_RECHECK", "Current ask is outside the entry price range", ask=ask)
        bid = book.bid(side)
        if bid is None:
            return reject("NO_BID", "Entry validation requires a bid as well as an ask", side=side)
        spread = D(ask) - D(bid)
        if not 0 < spread <= D(c.max_spread):
            return reject(
                "SPREAD_RECHECK",
                "Current spread is outside the allowed range",
                actual=float(spread),
                required=c.max_spread,
            )
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
        net_ev = D(conservative) - D(ask) - D(fee) - D(c.slippage)
        required = max(c.min_ev, c.min_edge)
        if net_ev < D(required):
            return reject(
                "NET_EDGE_RECHECK",
                "Current ask no longer leaves the required net edge",
                ask=ask,
                evaluated_ask=decision.get("expected_fill_price"),
                conservative_probability=conservative,
                estimated_fee=fee,
                slippage=c.slippage,
                actual=float(net_ev),
                required=required,
            )
        try:
            price = (
                passive_price(market, book, side, conservative, c)
                if c.passive
                else market.snap(min(D(ask) + D(c.slippage), D(c.max_entry_price)))
            )
        except ValueError as exc:
            return reject(
                "UNSUPPORTED_ENTRY_TICK", "No supported entry price could be selected", detail=str(exc)
            )
        if not market.valid_tick(price):
            return reject(
                "UNSUPPORTED_ENTRY_TICK", "Selected entry price is outside the supported grid", price=price
            )
        if not c.passive:
            limit_ev = D(conservative) - D(price) - D(fee_bound(price, c))
            if D(price) < D(ask) or limit_ev < D(required):
                return reject(
                    "IOC_LIMIT_EDGE",
                    "IOC price cap does not leave the required net edge",
                    limit=price,
                    actual=float(limit_ev),
                    required=required,
                )
        if market.ticker in self.positions:
            return reject("EXISTING_POSITION", "This market already has filled inventory")
        old = self.orders.get(market.ticker)
        retry = self.retry_ready(market.ticker, now)
        reentry = self.reentry_ready(market.ticker, now)
        if old and not retry and not reentry:
            return reject(
                "ORDER_ALREADY_ATTEMPTED",
                "An active, filled, exhausted, or cooling-down entry blocks submission",
                order_id=old.id,
                active=old.active,
                remaining=old.remaining,
            )
        sizing = self.risk.size_details(max(price, ask), now)
        quantity = sizing["quantity"]
        if not quantity:
            reason = sizing["reasons"][0]
            return reject(reason["code"], reason["message"], sizing=sizing)
        attempt = old.attempt + 1 if retry else 1
        cycle = old.cycle + 1 if reentry else old.cycle if retry else 1
        claim = (
            "entry:"
            + market.ticker
            + (f":cycle:{cycle}" if cycle > 1 else "")
            + (f":retry:{attempt}" if retry else "")
        )
        if not self.store.claim(self.run_id, claim):
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
            reference_ask=ask,
            attempt=attempt,
            cycle=cycle,
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
                entry_economics=entry_economics(
                    market,
                    book,
                    side,
                    price,
                    quantity,
                    conservative,
                    c,
                    now,
                    stage="order_limit",
                    entry_slippage=0,
                ),
                passive=c.passive,
                risk_reserved=self.risk.reserved[market.ticker],
                decision_context=submission_context or {},
                observation=self.execution_observation(book, now),
                entry_checks=dict(
                    entry_path=decision.get("entry_path", "standard"),
                    revalidate_entry_signal=c.revalidate_entry_signal,
                    lead=decision.get("lead", {}),
                    conservative_probability=conservative,
                    entry_probability_basis=decision.get("entry_probability_basis", "adjusted"),
                    min_probability=c.probability_floor(c.late_entry_enabled and remaining <= c.no_new_entry),
                    min_edge=c.min_edge,
                    min_ev=c.min_ev,
                    min_quality=c.min_quality,
                    expected_net_ev=decision.get("net_ev"),
                    max_spread=c.max_spread,
                ),
            ),
            now,
            market.ticker,
            opportunity_id,
        )
        if retry:
            self.state(market.ticker, "EVALUATING", now, opportunity_id)
            self.state(market.ticker, "TRADE_CANDIDATE", now, opportunity_id)
        self.state(market.ticker, "ORDER_PENDING", now, opportunity_id)
        if not c.passive and c.latency_seconds == 0:
            self.aggressive(market, book, now)
        return order

    @atomic
    def cancel(self, market, now, reason, *, details=None):
        order = self.orders.get(market)
        if not order or not order.active:
            return
        order.active = False
        order.cancelled_at = now
        order.entry_evidence = None
        self.record(
            "order",
            dict(
                id=order.id,
                status="cancelled",
                reason=reason,
                remaining=order.remaining,
                details=details or {},
                attempt=order.attempt,
                elapsed_seconds=now - order.created,
                queue_remaining=order.queue,
            ),
            now,
            market,
            order.opportunity_id,
        )
        self.state(market, "ORDER_CANCELLED", now, order.opportunity_id)
        if market in self.positions:
            self.state(market, "POSITION_OPEN", now, order.opportunity_id)
        else:
            self.risk.reserved.pop(market, None)

    def revalidate(self, market, decision, now, healthy, health_reasons=()):
        order = self.orders.get(market.ticker)
        if not order or not order.active:
            return
        reasons = list(health_reasons)
        if self.config.revalidate_entry_signal:
            reasons.extend(decision.get("reasons", []))
        if not healthy:
            reasons.append(dict(code="EXECUTION_HEALTH"))
        if market.ticker in self.venue_pauses:
            reasons.append(dict(code="VENUE_PAUSED"))
        if self.config.revalidate_entry_signal and decision.get("side") not in (None, order.side):
            reasons.append(dict(code="SIDE_CHANGED"))
        if self.config.revalidate_entry_signal and decision["decision"] != "TRADE_CANDIDATE" and not reasons:
            reasons.append(dict(code="SIGNAL_NOT_CANDIDATE"))
        if now - order.created >= self.config.max_wait:
            reasons.append(dict(code="ORDER_TIMEOUT"))
        if market.close_time - now <= self.config.entry_cutoff:
            reasons.append(dict(code="ENTRY_WINDOW"))
        if not market.tradable(now):
            reasons.append(dict(code="MARKET_NOT_TRADABLE"))
        if reasons:
            self.cancel(
                market.ticker,
                now,
                "signal_invalid_or_timeout",
                details=dict(
                    reasons=reasons,
                    limit=order.limit,
                    checked_entry_price=decision.get("expected_fill_price"),
                    conservative_probability=decision.get("conservative_probability"),
                    net_ev=decision.get("net_ev"),
                ),
            )

    @atomic
    def fill(self, order, quantity, price, now, maker, book=None):
        if self.wait_for_reference(order, now):
            return False
        if self.orders.get(order.market) is not order:
            raise ValueError("Stale paper order reference")
        if not D(price).is_finite() or not 0 < price < 1 or price > order.limit:
            raise ValueError("Fill violates order price")
        if (
            not order.active
            or now < order.eligible
            or order.market in self.quarantines
            or order.market in self.venue_pauses
        ):
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
                submitted_at=order.created,
                eligible_at=order.eligible,
                observation=self.execution_observation(book, now),
                queue_ahead=order.queue,
                remaining=order.remaining,
                slippage=float(
                    D(price) - D(order.reference_ask if order.reference_ask is not None else order.limit)
                ),
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
        if (
            not order
            or not order.active
            or not self.config.passive
            or now < order.eligible
            or market.ticker in self.venue_pauses
        ):
            return
        source = msg.get("ts_ms", 0) / 1000
        if self.wait_for_reference(order, now):
            return
        if (
            source < order.eligible
            or source > now + self.config.max_clock_skew
            or now - source > self.config.book_max_age
        ):
            return
        taker = msg.get("taker_outcome_side")
        if taker not in ("yes", "no") or taker == order.side:
            return
        try:
            yes_price = D(msg["yes_price_dollars"])
        except InvalidOperation as exc:
            raise ValueError("Invalid trade price") from exc
        if not yes_price.is_finite() or not 0 < yes_price < 1:
            raise ValueError("Invalid trade price")
        price = yes_price if order.side == "yes" else 1 - yes_price
        if price > D(order.limit):
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
            or ticker in self.venue_pauses
            or not book.valid
            or not math.isfinite(source_time)
            or not 0 <= now - book.received <= self.config.book_max_age
            or not -self.config.max_clock_skew <= now - source_time <= self.config.book_max_age
        ):
            return
        side = self.positions[ticker].side
        levels = book.yes if side == "yes" else book.no
        updates = {}
        for key, consumed in self.exit_consumed.items():
            if key[0] == ticker:
                displayed = levels.get(key[1], D(0))
                if D(consumed) > displayed:
                    updates[key] = displayed
        if updates:
            self._apply_exit_liquidity(updates)

    @atomic
    def _apply_exit_liquidity(self, updates):
        self.exit_consumed.update(updates)

    @atomic
    def aggressive(self, market, book, now):
        order = self.orders.get(market.ticker)
        if (
            self.config.passive
            or not order
            or not order.active
            or now < order.eligible
            or market.ticker in self.venue_pauses
        ):
            return
        # One match after latency, using current displayed asks within the original cap.
        if self.wait_for_reference(order, now):
            return
        # Slippage bounds the cap; it is not an artificial surcharge on every fill.
        reasons = []
        for code, okay in (
            ("BOOK_INVALID", book.valid),
            ("BOOK_RECEIVE_AGE", 0 <= now - book.received <= self.config.book_max_age),
            (
                "BOOK_SOURCE_AGE",
                -self.config.max_clock_skew <= now - book.source_time <= self.config.book_max_age,
            ),
            ("MARKET_NOT_TRADABLE", market.tradable(now)),
            ("ENTRY_WINDOW", market.close_time - now > self.config.entry_cutoff),
            ("KILL_SWITCH", not self.risk.halted),
            ("METADATA_QUARANTINED", market.ticker not in self.quarantines),
        ):
            if not okay:
                reasons.append(dict(code=code))
        if reasons:
            self.cancel(market.ticker, now, "ioc_execution_blocked", details=dict(reasons=reasons))
            return
        for price, quantity in book.asks(order.side):
            if D(price) > D(order.limit):
                break
            if not market.valid_tick(price):
                self.cancel(market.ticker, now, "invalid_book_tick", details=dict(price=price))
                return
            quantity = float(D(quantity).quantize(D(".01"), rounding=ROUND_FLOOR))
            if quantity <= 0:
                continue
            if self.fill(order, quantity, price, now, False, book=book) is False:
                return
            if not order.active:
                break
        if order.active:
            self.cancel(
                market.ticker,
                now,
                "ioc_remainder",
                details=dict(
                    limit=order.limit,
                    best_ask=book.ask(order.side),
                    reasons=[
                        dict(
                            code="IOC_UNFILLED",
                            message="Available quantity within the price cap was insufficient",
                        )
                    ],
                ),
            )

    def monitor(self, market, book, probability, now, event_id):
        pos = self.positions.get(market.ticker)
        if (
            not pos
            or market.ticker in self.quarantines
            or market.ticker in self.venue_pauses
            or not book.valid
            or not market.tradable(now)
        ):
            return
        bid = book.bid(pos.side)
        if bid is None or not 0 <= now - book.received <= self.config.book_max_age:
            return
        entry = pos.cost / pos.bought
        mark = bid - entry
        c = self.config
        # The engine may authorize price-only risk reduction while its model is
        # warming up or unavailable. Missing/invalid probability is not a zero.
        conservative = probability.get("conservative_" + pos.side) if probability else None
        model_available = (
            type(conservative) in (int, float) and math.isfinite(conservative) and 0 <= conservative <= 1
        )
        target_warning = False
        try:
            target = market.snap(c.take_profit, up=True) if c.take_profit is not None else None
        except ValueError:
            target = None
            target_warning = True
        reason = (
            "HARD_STOP"
            if bid <= entry * c.stop_multiplier
            else "TAKE_PROFIT"
            if target is not None and bid >= target
            else "INVALIDATION"
            if model_available
            and (
                conservative < c.exit_probability
                or (
                    c.hold_value_exit_enabled
                    and conservative - (bid - fee_bound(bid, c) - c.slippage) < c.min_hold_ev
                )
            )
            else ""
        )
        clear_pending = model_available or pos.exit_reason != "INVALIDATION"
        # Most quote updates change neither the exit decision nor saved extrema.
        # Keep these read-only checks outside the rollback snapshot/SQL transaction.
        if (
            not target_warning
            and not reason
            and (not pos.exit_reason or not clear_pending)
            and pos.max_adverse <= mark <= pos.max_favorable
        ):
            return
        audit = None
        if reason and reason != pos.exit_reason:
            hold_ev = conservative - (bid - fee_bound(bid, c) - c.slippage) if model_available else None
            audit = dict(
                trigger=(
                    "PROBABILITY_BELOW_EXIT_THRESHOLD"
                    if conservative < c.exit_probability
                    else "HOLD_VALUE_BELOW_THRESHOLD"
                )
                if reason == "INVALIDATION"
                else reason,
                conservative_probability=conservative if model_available else None,
                model_available=model_available,
                exit_probability=c.exit_probability,
                hold_ev=hold_ev,
                min_hold_ev=c.min_hold_ev,
                hold_value_exit_enabled=c.hold_value_exit_enabled,
                bid=bid,
                average_entry=entry,
                hard_stop_price=entry * c.stop_multiplier,
                take_profit_price=target,
                fee_estimate=fee_bound(bid, c),
                slippage=c.slippage,
                snapshot_id=event_id,
                observation=self.execution_observation(book, now),
            )
        self._apply_monitor(
            market,
            book,
            mark,
            reason,
            target,
            target_warning,
            now,
            event_id,
            audit,
            clear_pending=clear_pending,
        )

    @atomic
    def _apply_monitor(
        self,
        market,
        book,
        mark,
        reason,
        target,
        target_warning,
        now,
        event_id,
        audit=None,
        *,
        clear_pending=True,
    ):
        pos = self.positions[market.ticker]
        pos.max_favorable = max(pos.max_favorable, mark)
        pos.max_adverse = min(pos.max_adverse, mark)
        c = self.config
        if target_warning:
            self.record(
                "exit_warning",
                {"reason": "UNSUPPORTED_TAKE_PROFIT_TICK"},
                now,
                market.ticker,
                pos.opportunity_id,
            )
        # Missing probability is not evidence that a probability trigger recovered.
        # A new trigger must never inherit a previous conditional intent's latency.
        if pos.exit_reason and reason != pos.exit_reason and (reason or clear_pending):
            self.record(
                "exit_cancelled",
                dict(
                    reason="TRIGGER_CHANGED" if reason else "TRIGGER_CLEARED",
                    previous_reason=pos.exit_reason,
                    remaining_quantity=pos.quantity,
                    eligible=getattr(self, "_exit_eligible", {}).get(market.ticker),
                    snapshot_id=event_id,
                ),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            pos.exit_reason = ""
            getattr(self, "_exit_eligible", {}).pop(market.ticker, None)
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
                dict(reason=reason, eligible=now + c.latency_seconds, decision=audit),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            self._exit_eligible = getattr(self, "_exit_eligible", {})
            self._exit_eligible[market.ticker] = now + c.latency_seconds
            return
        eligible = self._exit_eligible[market.ticker]
        if now < eligible or book.received < eligible:
            return
        self.last_exit_event[market.ticker] = event_id
        self.cancel(market.ticker, now, "exit_requested")
        self.state(market.ticker, "EXITING", now, pos.opportunity_id)
        levels = book.yes if pos.side == "yes" else book.no
        # Each eligible matching event is an IOC child; its partial fills share fees.
        fees = FeeAccumulator(c.fee_balance_precision)
        stress_fees = FeeAccumulator(c.fee_balance_precision)
        execution_id = f"{market.ticker}:{event_id}"
        for price, quantity in sorted(levels.items(), reverse=True):
            key = (market.ticker, price)
            consumed = D(self.exit_consumed.get(key, 0))
            available = max(D(0), quantity - consumed)
            position_quantity = D(pos.quantity)
            q = min(position_quantity, available).quantize(D(".01"), rounding=ROUND_FLOOR)
            if q <= 0 or not market.valid_tick(float(price)):
                continue
            fill_price = float(price)
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
                    exit_eligible_at=self._exit_eligible.get(market.ticker),
                    exit_execution_id=execution_id,
                    execution_model="displayed-depth-v2",
                    book_received_at=book.received,
                    book_source_at=book.source_time,
                    observation=self.execution_observation(book, now),
                ),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            # Same-opportunity stress diagnostic only: never changes inventory/P&L.
            try:
                stress_price = market.snap(D(price) - D(c.slippage))
            except ValueError:
                stress_price = None
            stress_fillable = stress_price is not None and (reason != "TAKE_PROFIT" or stress_price >= target)
            stress_fee = (
                stress_fees.charge(stress_price, q_float, c.taker_fee_rate, "sell")
                if stress_fillable
                else None
            )
            self.record(
                "exit_stress",
                dict(
                    exit_execution_id=execution_id,
                    scenario="bid_haircut_same_opportunity",
                    side=pos.side,
                    quantity=q_float,
                    displayed_price=fill_price,
                    primary_fee=fee,
                    haircut=c.slippage,
                    stressed_price=stress_price,
                    fillable=stress_fillable,
                    status="FILLABLE"
                    if stress_fillable
                    else "NOT_FILLABLE_AT_LIMIT"
                    if stress_price is not None
                    else "NO_VALID_TICK",
                    sell_limit=target if reason == "TAKE_PROFIT" else None,
                    stressed_fee=stress_fee,
                    stressed_proceeds=q_float * stress_price if stress_fillable else None,
                    primary_proceeds=q_float * fill_price,
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
        order = self.orders.get(market)
        if order and order.opportunity_id == pos.opportunity_id:
            order.completed_at = now
        self.state(market, "CLOSED", now, pos.opportunity_id)

    @atomic
    def quarantine(self, market, now, reason, observed=None):
        """Block trading without discarding the identity or accounting of inventory."""
        ticker = market.ticker
        state = self.store.state(self.run_id, ticker)
        if state == "ERROR":
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
        for trade in self.store.list(kind="trade_result", run_id=self.run_id, market=ticker, limit=None):
            b = trade["body"]
            if b.get("settlement_result") is not None:
                continue
            buy_fees = sum(
                r["body"]["fee"]
                for r in self.store.list(
                    kind="fill",
                    run_id=self.run_id,
                    market=ticker,
                    opportunity_id=trade["opportunity_id"],
                    limit=None,
                )
                if r["body"].get("action") == "buy"
            )
            hypothetical = b["bought"] * int(b["side"] == result) - b["cost"] - buy_fees
            self.record(
                "hold_to_settlement_comparison",
                dict(
                    hypothetical=True,
                    result=result,
                    actual_net_pnl=b["net_pnl"],
                    hypothetical_net_pnl=hypothetical,
                    actual_minus_hold=b["net_pnl"] - hypothetical,
                    assumption="Same filled purchases held to payout; entry fees only; no settlement fee modeled",
                ),
                now,
                ticker,
                trade["opportunity_id"],
            )
        self.quarantines.pop(ticker, None)
        self.venue_pauses.pop(ticker, None)
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
