"""Isolated fixed-purchase experiment; never submits primary orders."""

import copy
import math
import time
from dataclasses import asdict, replace

from .domain import D
from .engine import freshness_rechecks
from .execution import PaperExecutor, Position, atomic
from .recovery import contract_hash, restore_market
from .storage import Store
from .strategies.settlement_edge.model import features, probability, quality

POLICY = dict(name="stop-confirmation-v1", confirmations=2, max_wait_seconds=2.0, emergency_multiplier=0.5)


class ConfirmationExecutor(PaperExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.confirmations = {}
        self.mirrored_fills = set()
        self.excluded_trades = set()
        self.gapped_markets = set()

    def snapshot(self):
        return dict(
            super().snapshot(),
            confirmations=copy.deepcopy(self.confirmations),
            mirrored_fills=sorted(self.mirrored_fills),
            excluded_trades=sorted(self.excluded_trades),
            gapped_markets=sorted(self.gapped_markets),
        )

    def restore(self, data):
        super().restore(data)
        self.confirmations = copy.deepcopy(data.get("confirmations", {}))
        self.mirrored_fills = set(data.get("mirrored_fills", []))
        self.excluded_trades = set(data.get("excluded_trades", []))
        self.gapped_markets = set(data.get("gapped_markets", []))

    def note_gap(self, ticker, now):
        if ticker not in self.gapped_markets:
            self._note_gap(ticker, now)

    @atomic
    def _note_gap(self, ticker, now):
        self.gapped_markets.add(ticker)
        self.record(
            "input_gap", dict(confirmation_fallback=True), now, ticker, self.positions[ticker].opportunity_id
        )

    @atomic
    def mirror_buy(self, row, market):
        if row["id"] in self.mirrored_fills:
            return
        b = row["body"]
        ticker = market.ticker
        op = row["opportunity_id"]
        held = self.positions.get(ticker)
        if op in self.excluded_trades or (held and held.opportunity_id != op):
            self.excluded_trades.add(op)
            self.mirrored_fills.add(row["id"])
            self.record(
                "shadow_trade_excluded",
                dict(reason="PRIOR_SHADOW_POSITION_OPEN"),
                row["timestamp"],
                ticker,
                op,
            )
            return
        if ticker not in self.positions:
            self.confirmations.pop(ticker, None)
            if self.store.state(self.run_id, ticker) == "CLOSED":
                self.state(ticker, "MONITORING", row["timestamp"], op)
                initial = ()
            else:
                initial = ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP")
            for state in initial + (
                "ENTRY_WINDOW",
                "EVALUATING",
                "TRADE_CANDIDATE",
                "ORDER_PENDING",
                "POSITION_OPEN",
            ):
                self.state(ticker, state, row["timestamp"], row["opportunity_id"])
            self.positions[ticker] = Position(
                row["opportunity_id"], ticker, b["side"], opened=row["timestamp"]
            )
            self.contracts[ticker] = asdict(market)
        pos = self.positions[ticker]
        pos.quantity = float(D(pos.quantity) + D(b["quantity"]))
        pos.bought = float(D(pos.bought) + D(b["quantity"]))
        pos.cost += b["quantity"] * b["price"]
        pos.fees += b["fee"]
        self.mirrored_fills.add(row["id"])
        self.record(
            "fill", {**b, "mirrored_primary_fill": row["id"]}, row["timestamp"], ticker, pos.opportunity_id
        )

    def confirmation_monitor(self, market, book, p, now, event_id, reference_source, *, available=True):
        pos = self.positions.get(market.ticker)
        if pos and market.ticker not in self.confirmations:
            bid = book.bid(pos.side)
            if bid is not None and bid > pos.cost / pos.bought * self.config.stop_multiplier:
                return super().monitor(
                    market, book, p, now, event_id, reference_source=reference_source if available else None
                )
        return self._confirmation_monitor(
            market, book, p, now, event_id, reference_source, available=available
        )

    @atomic
    def _confirmation_monitor(self, market, book, p, now, event_id, reference_source, *, available=True):
        pos = self.positions.get(market.ticker)
        if not pos:
            return
        bid = book.bid(pos.side)
        if bid is None:
            return
        entry = pos.cost / pos.bought
        threshold = entry * self.config.stop_multiplier
        adjusted = p.get("conservative_" + pos.side)
        model_ok = type(adjusted) in (float, int) and math.isfinite(adjusted) and 0 <= adjusted <= 1
        state = self.confirmations.get(market.ticker)
        # A dust bid above the stop does not reset the clock for the whole position.
        levels = book.yes if pos.side == "yes" else book.no
        recovered = sum(
            max(D(0), q - D(self.exit_consumed.get((market.ticker, price), 0)))
            for price, q in levels.items()
            if float(price) > threshold
        ) >= D(pos.quantity)
        if recovered:
            self.confirmations.pop(market.ticker, None)
            state = None
        bypass = (
            not available
            or not model_ok
            or adjusted < self.config.exit_probability
            or bid <= entry * POLICY["emergency_multiplier"]
        )
        hold = False
        if bid <= threshold and state is None:
            state = dict(started=now, last_source=reference_source, count=1)
            self.confirmations[market.ticker] = state
        if bid <= threshold and not bypass and not state.get("released"):
            if reference_source != state["last_source"]:
                # A missing reference second is not a second confirmation.
                if not 0 < reference_source - state["last_source"] <= 1.5:
                    bypass = True
                else:
                    state["count"] += 1
                state["last_source"] = reference_source
            hold = (
                not bypass
                and state["count"] < POLICY["confirmations"]
                and now - state["started"] < POLICY["max_wait_seconds"]
            )
        if bid <= threshold and not hold:
            state["released"] = True
        decision = (
            "WAIT"
            if hold
            else "FALLBACK"
            if bypass and bid <= threshold
            else "CONFIRMED"
            if bid <= threshold
            else "RECOVERED"
        )
        prior = state.get("decision") if state else None
        if state is not None:
            state["decision"] = decision
        if decision != prior and (state is not None or bid <= threshold):
            self.record(
                "stop_confirmation",
                dict(
                    policy=POLICY,
                    decision=decision,
                    reference_source=reference_source,
                    bid=bid,
                    threshold=threshold,
                    probability=adjusted,
                    state=copy.deepcopy(state),
                ),
                now,
                market.ticker,
                pos.opportunity_id,
            )
        original = self.config
        try:
            # Suppress only the ordinary price stop. Probability and TP stay active.
            if hold:
                self.config = replace(original, stop_multiplier=0)
            super().monitor(
                market, book, p, now, event_id, reference_source=reference_source if available else None
            )
        finally:
            self.config = original


class StopShadow:
    """Reads committed primary fills, shares received inputs, owns an isolated ledger."""

    def __init__(self, primary, path):
        self.primary = primary
        self.store = Store("sqlite:///" + str(path))
        self.run_id = primary.run_id + ":" + POLICY["name"]
        self.executor = ConfirmationExecutor(self.store, self.run_id, "BACKTEST", primary.config)
        checkpoint = self.store.load_checkpoint(self.run_id)
        if checkpoint:
            self.executor.restore(checkpoint)
        elif not self.store.list(kind="run", run_id=self.run_id, limit=1):
            self.store.add(
                "run", dict(policy=POLICY, parent_run=primary.run_id), self.run_id, "BACKTEST", time.time()
            )
        self.pending = []
        self.cache = {}
        self.gaps = set(self.executor.positions)  # Restart cannot count as continuous confirmation.
        self.excluded = {
            p.opportunity_id
            for t, p in primary.executor.positions.items()
            if t not in self.executor.positions
        }
        for ticker in self.gaps:
            self.executor.note_gap(ticker, time.time())
        self.failed = False
        self.connection = None
        self.published = {
            r["opportunity_id"]
            for r in primary.store.list(kind="stop_shadow_comparison", run_id=primary.run_id, limit=None)
        }
        self.done = set(self.executor.contracts)
        for ticker, pos in list(self.executor.positions.items()):
            for fill in primary.store.list(
                kind="fill", run_id=primary.run_id, opportunity_id=pos.opportunity_id, limit=None
            ):
                if fill["body"].get("action") == "buy":
                    self.executor.mirror_buy(fill, restore_market(self.executor.contracts[ticker]))
        original_record = primary.executor.record

        def record(kind, body, now, market, op):
            result = original_record(kind, body, now, market, op)
            if not self.failed and kind == "fill" and body.get("action") == "buy":
                self.pending.append((market, op))
            if not self.failed and kind == "trade_result":
                self.done.add(market)
            return result

        self.store.checkpoint(self.run_id, self.executor.snapshot())
        primary.store.add(
            "stop_shadow_status",
            dict(
                policy=POLICY,
                status="STARTED",
                excluded_markets=sorted(self.excluded),
                resumed_positions=sorted(self.executor.positions),
            ),
            primary.run_id,
            primary.mode,
            time.time(),
        )

        primary.executor.record = record

    def process(self, row, payload):
        if self.failed:
            return
        try:
            self._process(row, payload)
        except Exception as exc:
            # Fail the experiment closed, without changing primary positions or risk.
            self.failed = True
            self.primary.store.add(
                "stop_shadow_status",
                dict(status="FAILED", error=str(exc), policy=POLICY),
                self.primary.run_id,
                self.primary.mode,
                row["received"],
            )

    def _process(self, row, payload):
        e, ex = self.primary, self.executor
        now = e.clock() if e.clock else row["received"]
        for ticker, op in self.pending:
            if op in self.excluded:
                continue
            # Read committed rows: a rolled-back primary transaction cannot seed a trade.
            fills = e.store.list(kind="fill", run_id=e.run_id, opportunity_id=op, limit=None)
            for fill in sorted(fills, key=lambda r: r["timestamp"]):
                if fill["body"]["action"] == "buy":
                    ex.mirror_buy(fill, e.markets[ticker])
        self.pending.clear()
        kind, msg = payload.get("type"), payload.get("msg", {})
        connection = row.get("connection_id")
        if connection != self.connection:
            self.gaps.update(ex.positions)
            for held_ticker in ex.positions:
                ex.note_gap(held_ticker, now)
            self.connection = connection
        ticker = msg.get("market_ticker")
        if kind == "settlement" and ticker in ex.positions:
            ex.settle(restore_market(ex.contracts[ticker]), msg["result"], now, evidence=msg.get("evidence"))
            self.done.add(ticker)
        if kind in ("disconnect", "stale", "error") or row.get("analysis_suspended"):
            self.gaps.update(ex.positions)
            for held_ticker in ex.positions:
                ex.note_gap(held_ticker, now)
        if (
            not row.get("analysis_suspended")
            and kind in ("orderbook_snapshot", "orderbook_delta")
            and ticker in ex.positions
        ):
            market, book = e.markets[ticker], e.books[ticker]
            healthy = e.healthy and e.clock_ok and e.exchange_open and market.tradable(now)
            healthy = (
                healthy and ticker not in e.executor.quarantines and ticker not in e.executor.venue_pauses
            )
            healthy = healthy and contract_hash(market) == contract_hash(restore_market(ex.contracts[ticker]))
            healthy = healthy and not freshness_rechecks(book, e.ticks, row["received"], now, e.config)
            if not healthy:
                self.gaps.add(ticker)
                ex.note_gap(ticker, now)
            else:
                source = e.ticks[-1].source
                cached = self.cache.get(ticker)
                if (
                    cached is None
                    or cached[0] != e.ticks[-1]
                    or now - cached[1] >= e.config.evaluation_interval
                ):
                    primary_model = getattr(e, "_model_cache", {}).get(ticker)
                    if (
                        primary_model
                        and primary_model[1] == market.spec
                        and primary_model[5] == e.ticks[-1]
                        and 0 <= row["received"] - primary_model[0] < e.config.evaluation_interval
                    ):
                        f, p = (
                            (primary_model[2], primary_model[3]) if primary_model[4] is None else (None, {})
                        )
                    else:
                        try:
                            f = features(e.ticks, row["received"], e.config)
                            p = probability(market.spec, e.ticks, row["received"], f["sigma"], e.config)
                        except ValueError:
                            f, p = None, {}
                    cached = (e.ticks[-1], now, f, p)
                    self.cache[ticker] = cached
                f, p = cached[2:]
                checked = e.clock() if e.clock else now
                if freshness_rechecks(book, e.ticks, row["received"], checked, e.config):
                    self.gaps.add(ticker)
                    ex.note_gap(ticker, checked)
                    return
                now = checked
                if f is None or quality(f, e.ticks, book, now, e.config)["reasons"]:
                    p = {}
                ex.observe_exit_liquidity(
                    market, book, now, continuous=ticker not in self.gaps, source_time=book.source_time
                )
                ex.confirmation_monitor(
                    market, book, p, now, row["id"], source, available=ticker not in self.gaps
                )
                self.gaps.discard(ticker)
                if ticker not in ex.positions:
                    self.done.add(ticker)
        for ticker in list(self.done):
            if ticker not in ex.contracts:
                self.done.discard(ticker)
                continue
            if ticker in ex.positions or ticker in e.executor.positions:
                continue
            shadows = self.store.list(kind="trade_result", run_id=self.run_id, market=ticker, limit=None)
            for shadow_row in shadows:
                op = shadow_row["opportunity_id"]
                if op in self.published:
                    continue
                actual = e.store.list(kind="trade_result", run_id=e.run_id, opportunity_id=op, limit=1)
                if not actual:
                    continue
                a, b = actual[0]["body"], shadow_row["body"]
                if (
                    op != shadow_row["opportunity_id"]
                    or a["side"] != b["side"]
                    or abs(a["bought"] - b["bought"]) > 1e-8
                    or abs(a["cost"] - b["cost"]) > 1e-8
                ):
                    raise ValueError("Shadow purchases do not match primary result")
                if op not in self.published:
                    e.store.add(
                        "stop_shadow_comparison",
                        dict(
                            policy=POLICY,
                            primary_net_pnl=a["net_pnl"],
                            shadow_net_pnl=b["net_pnl"],
                            difference=b["net_pnl"] - a["net_pnl"],
                            primary_reason=a["reason"],
                            shadow_reason=b["reason"],
                            shadow_holding_seconds=b["holding_seconds"],
                            primary_holding_seconds=a["holding_seconds"],
                            input_interruption=ticker in ex.gapped_markets,
                            model=ex.model_identity,
                            same_purchases=True,
                        ),
                        e.run_id,
                        e.mode,
                        now,
                        ticker,
                        op,
                    )
                    self.published.add(op)
                self.done.discard(ticker)
