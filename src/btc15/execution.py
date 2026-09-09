"""Conservative paper matching; no network writes."""

import copy
import uuid
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, InvalidOperation
from functools import wraps

from .domain import D, order_direction
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

    def snapshot(self):
        return dict(
            maker_rates=self.maker_rates,
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
            exit_consumed=[[k[0], str(k[1]), v] for k, v in self.exit_consumed.items()],
            exit_eligible=getattr(self, "_exit_eligible", {}),
        )

    def restore(self, data):
        data = copy.deepcopy(data)
        if data["config_version"] != self.config.version or data["mode"] != self.mode:
            raise ValueError("Checkpoint configuration/mode mismatch")
        self.maker_rates = data.get("maker_rates", {})
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
        self.exit_consumed = {(m, D(p)): v for m, p, v in data["exit_consumed"]}
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
        if self.model_identity["model_id"] == "settlement-edge":
            return model is None or model["model_id"] == "settlement-edge"
        return model == self.model_identity

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

    @atomic
    def submit(self, market, book, decision, opportunity_id, now, freshness_ok, *, entry_evidence=None):
        if not self.config.enabled:
            return None
        c = self.config
        if (
            decision["decision"] != "TRADE_CANDIDATE"
            or not freshness_ok
            or not market.tradable(now)
            or not (
                c.no_new_entry <= market.close_time - now <= c.entry_window_start
                if hasattr(c, "model_id")
                else c.no_new_entry < market.close_time - now <= c.entry_window_start
            )
        ):
            return None
        side = decision["side"]
        if not book.valid or now - book.received > c.book_max_age:
            return None
        ask = book.ask(side)
        if ask is None or decision["conservative_probability"] - ask - fee_bound(ask, c) - c.slippage < max(
            c.min_ev, c.min_edge
        ):
            return None
        price = (
            passive_price(market, book, side, decision["conservative_probability"], c)
            if c.passive
            else market.snap(ask + c.slippage)
        )
        if not market.valid_tick(price):
            return None
        quantity = self.risk.size(max(price, ask), now)
        if not quantity or market.ticker in self.orders or market.ticker in self.positions:
            return None
        if hasattr(c, "model_id") and not self.store.claim(self.run_id, "event:" + market.event_ticker):
            self.record(
                "execution_rejection", dict(reason="DUPLICATE_EVENT"), now, market.ticker, opportunity_id
            )
            return None
        if not self.store.claim(self.run_id, "entry:" + market.ticker):
            return None
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
        if not order.active or now < order.eligible:
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
        pos.bought += quantity
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
        if not pos or not book.valid or not market.tradable(now):
            return
        bid = book.bid(pos.side)
        if bid is None or now - book.received > self.config.book_max_age:
            return
        entry = pos.cost / pos.bought
        mark = bid - entry
        pos.max_favorable = max(pos.max_favorable, mark)
        pos.max_adverse = min(pos.max_adverse, mark)
        c = self.config
        if getattr(c, "hold_to_settlement", False):
            return
        conservative = probability["conservative_" + pos.side]
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
            if conservative < c.exit_probability
            or conservative - (bid - fee_bound(bid, c) - c.slippage) < c.min_hold_ev
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
            available = max(D(0), quantity - D(self.exit_consumed.get(key, 0)))
            q = float(min(D(pos.quantity), available))
            if q <= 0 or float(price) <= c.slippage:
                continue
            fill_price = market.snap(float(price) - c.slippage)
            if reason == "TAKE_PROFIT" and fill_price < target:
                continue
            self.exit_consumed[key] = self.exit_consumed.get(key, 0) + q
            fee = fees.charge(fill_price, q, c.taker_fee_rate, "sell")
            pos.quantity = float(D(pos.quantity) - D(q))
            pos.proceeds += q * fill_price
            pos.fees += fee
            self.record(
                "fill",
                dict(action="sell", side=pos.side, quantity=q, price=fill_price, fee=fee, reason=reason),
                now,
                market.ticker,
                pos.opportunity_id,
            )
            if pos.quantity <= 0:
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
    def settle(self, market, result, now):
        if now < market.close_time or result not in ("yes", "no"):
            raise ValueError("Premature/invalid settlement")
        if not self.store.claim(self.run_id, "settlement:" + market.ticker):
            return
        self.record("settlement", dict(result=result), now, market.ticker, "")
        self.cancel(market.ticker, now, "settlement")
        pos = self.positions.get(market.ticker)
        if pos:
            self.state(market.ticker, "SETTLEMENT_PENDING", now, pos.opportunity_id)
            pos.proceeds += pos.quantity * (1 if pos.side == result else 0)
            pos.quantity = 0
            self.finish(market.ticker, now, "SETTLEMENT", result)
        elif self.store.state(self.run_id, market.ticker) not in ("CLOSED", "HALTED", "ERROR"):
            self.state(market.ticker, "SETTLEMENT_PENDING", now)
            self.state(market.ticker, "CLOSED", now)


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
