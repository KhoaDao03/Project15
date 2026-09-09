"""One causal event consumer shared by collector, paper mode and backtests."""

import hashlib
import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .config import Strategy
from .domain import Book, dumps, parse_market, timestamp
from .execution import PaperExecutor
from .models import require_single_run
from .strategies.settlement_edge.model import Tick, features, probability, quality
from .strategies.settlement_edge.rules import evaluate

log = logging.getLogger("btc15")
VERSIONS = dict(
    strategy="btc15-v1", probability="settlement-mc-logwalk-v1", volatility="rv-ewma-v1", software="0.1.0"
)


class Engine:
    def __init__(
        self,
        store,
        config,
        mode="PAPER",
        run_id=None,
        execute=True,
        clock=None,
        resume=False,
        record_evaluations=True,
    ):
        if mode not in ("PAPER", "BACKTEST"):
            raise ValueError("Research modes only")
        if type(config) is not Strategy:
            raise ValueError("Only BTC15 Settlement Edge is executable")
        if resume:
            require_single_run(store, run_id)
        self.store, self.config, self.mode = store, config, mode
        self.run_id = run_id or str(uuid.uuid4())
        self.execute, self.clock = execute, clock
        self.record_evaluations = record_evaluations
        self.raw_archive = record_evaluations
        self.markets, self.books, self.last_evaluation, self.latest = {}, {}, {}, {}
        self.ticks = []
        self.sequences = {}
        self.connection = None
        self.healthy = False
        self.clock_ok = False
        self.exchange_open = False
        self.fee_changes = {}
        self.series_fees = {}
        self.series_fee_changes = None
        self.last_received = -float("inf")
        self._model_cache = {}
        self._pending_settlement = set()
        self._decision_keys = {}
        self._last_op = {}
        self.last_mono = None
        self.last_wall = None
        self.entries_active = True
        self.executor = PaperExecutor(store, self.run_id, mode, config)
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True))
        except (subprocess.CalledProcessError, FileNotFoundError):
            commit, dirty = "unavailable", True
        package = Path(__file__).parent
        source_snapshot = {
            str(p.relative_to(package)): p.read_text()
            for p in sorted(package.rglob("*"))
            if p.suffix in (".py", ".js", ".css", ".html")
        }
        lock = package.parent.parent / "uv.lock"
        if lock.exists():
            source_snapshot["uv.lock"] = lock.read_text()
        source_hash = hashlib.sha256(dumps(source_snapshot).encode()).hexdigest()
        self.versions = {
            **VERSIONS,
            "config": config.version,
            "git_commit": commit,
            "working_tree_dirty": dirty,
            "source_hash": source_hash,
        }
        if resume:
            checkpoint = store.load_checkpoint(self.run_id)
            if not checkpoint:
                raise ValueError(
                    "No atomic checkpoint for this run; legacy open positions require forensic recovery"
                )
            self.executor.restore(checkpoint)
            for record in store.list(kind="market", run_id=self.run_id, limit=None):
                m = parse_market(record["body"]["raw"], record["body"]["series"])
                self.markets[m.ticker] = m
                self.books[m.ticker] = Book()
            for ticker in list(self.executor.orders):
                self.executor.cancel(ticker, time.time(), "operator_resume_no_downtime_fills")
            store.add(
                "resume",
                {
                    "source_hash": source_hash,
                    "config_version": config.version,
                    "recording": "full" if record_evaluations else "trades_only",
                },
                self.run_id,
                mode,
                time.time(),
            )
        else:
            self.store.add(
                "run",
                dict(
                    config=asdict(config),
                    model=self.executor.model_identity,
                    versions=self.versions,
                    execute=execute,
                    recording="full" if record_evaluations else "trades_only",
                    source_snapshot=source_snapshot,
                ),
                self.run_id,
                mode,
                time.time(),
            )

    def state(self, market, target, now, op=""):
        self.store.transition(
            self.run_id,
            self.mode,
            market,
            target,
            now,
            op,
            record_history=self.record_evaluations or market in self.executor.orders,
        )

    def error(self, code, now, market="", detail=""):
        self.store.add("health", dict(code=code, detail=detail), self.run_id, self.mode, now, market)
        log.warning(dumps(dict(code=code, market_id=market, run_id=self.run_id, detail=detail)))

    def invalidate(self, now, reason):
        self.healthy = False
        for book in self.books.values():
            book.valid = False
        for ticker in list(self.executor.orders):
            self.executor.cancel(ticker, now, reason)

    def ingest(self, row):
        now = row["received"]
        if now < self.last_received:
            if self.last_mono is not None and row.get("monotonic_ns", 0) / 1e9 <= self.last_mono:
                raise ValueError("Receive order moved backwards")
            self.clock_ok = False
            self.healthy = False
            for ticker in list(self.executor.orders):
                self.executor.cancel(ticker, now, "wall_clock_reversal")
            self.error("CLOCK_REVERSAL", now, detail=str(now - self.last_received))
            # Monotonic receipt still establishes causal order. Apply the frame so the
            # book stays consistent; reconnecting would create an avoidable data gap.
        self.last_received = now
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        kind = payload.get("type")
        msg = payload.get("msg", {})
        connection = row.get("connection_id", "")
        if connection != self.connection:
            self.invalidate(now, "connection_changed")
            self.connection = connection
            self.sequences = {}
            self.last_mono = self.last_wall = None
        mono = row.get("monotonic_ns", 0) / 1e9
        if (
            self.last_mono is not None
            and abs((now - self.last_wall) - (mono - self.last_mono)) > self.config.max_clock_skew
        ):
            self.clock_ok = False
            self.invalidate(now, "local_clock_jump")
        self.last_mono, self.last_wall = mono, now
        sid, seq = payload.get("sid"), payload.get("seq")
        if sid is not None and seq is not None:
            previous = self.sequences.get(sid)
            if previous is not None and seq != previous + 1:
                self.invalidate(now, "sequence_gap")
                self.error("SEQUENCE_GAP", now, detail=f"{previous} -> {seq}")
                self.sequences[sid] = seq
                return False
            self.sequences[sid] = seq
        try:
            if kind == "metadata":
                series = msg["series"]
                self.series_fees = series
                self.series_fee_changes = msg.get("series_fee_changes")
                self.fee_changes = msg.get("fee_changes", {})
                self.clock_ok = abs(msg.get("clock_skew", float("inf"))) <= self.config.max_clock_skew
                self.exchange_open = msg.get("exchange_status", {}).get("trading_active") is True
                for raw in msg["markets"]:
                    try:
                        market = parse_market(raw, series)
                    except (ValueError, KeyError, TypeError) as exc:
                        ticker = raw.get("ticker", "")
                        if raw.get("floor_strike") is None:
                            # Keep the subscribed book warm while strike publication is pending.
                            # Activation must not require reconnecting the reference feeds.
                            self.books.setdefault(ticker, Book())
                            self.store.add("pending_market", raw, self.run_id, self.mode, now, ticker)
                            continue
                        self.error("INVALID_MARKET", now, ticker, str(exc))
                        if ticker in self.markets:
                            self.executor.cancel(ticker, now, "metadata_invalid")
                            self.markets.pop(ticker)
                        self.store.add("invalid_market", raw, self.run_id, self.mode, now, ticker)
                        continue
                    old = self.markets.get(market.ticker)
                    if old and old.spec != market.spec:
                        self.executor.cancel(market.ticker, now, "settlement_rules_changed")
                        self.state(market.ticker, "HALTED", now)
                        self.error("RULES_CHANGED", now, market.ticker)
                    self.markets[market.ticker] = market
                    self.books.setdefault(market.ticker, Book())
                    self.store.add(
                        "market",
                        dict(raw=raw, series=series, spec=asdict(market.spec)),
                        self.run_id,
                        self.mode,
                        now,
                        market.ticker,
                    )
                    if self.store.state(self.run_id, market.ticker) is None:
                        for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
                            self.state(market.ticker, state, now)
            elif kind in ("disconnect", "stale", "error"):
                self.invalidate(now, kind)
                self.error(kind.upper(), now, detail=str(msg))
            elif kind == "cfbenchmarks_value":
                raw = json.loads(msg["data"])
                if msg["index_id"] != "BRTI" or raw.get("id") != "BRTI" or raw.get("type") != "value":
                    raise ValueError("Not an official BRTI tick")
                tick = Tick(float(raw["time"]) / 1000, now, float(raw["value"]))
                if tick.source > now + self.config.max_clock_skew:
                    raise ValueError("Future reference tick")
                if self.ticks and tick.source == self.ticks[-1].source and tick.price == self.ticks[-1].price:
                    return True
                if self.ticks and tick.source <= self.ticks[-1].source:
                    raise ValueError("Reference duplicate or reversal")
                self.ticks.append(tick)
                self.ticks = [t for t in self.ticks if t.source >= now - 3600]
                self.healthy = True
            elif kind in ("orderbook_snapshot", "orderbook_delta"):
                ticker = msg.get("market_ticker")
                if ticker in self.books:
                    if kind == "orderbook_snapshot":
                        self.books[ticker].snapshot(msg, now)
                    else:
                        self.books[ticker].delta(msg, now)
            elif kind == "market_lifecycle_v2":
                ticker = msg.get("market_ticker")
                if ticker in self.markets:
                    event = msg.get("event_type")
                    if event == "settled" and msg.get("result") in ("yes", "no"):
                        self.settle(ticker, msg["result"], now)
                    elif event in ("closed", "deactivated", "price_level_structure_updated", "determined"):
                        self.books[ticker].valid = False
                        self.executor.cancel(ticker, now, event)
            elif kind == "settlement":
                self.settle(msg["market_ticker"], msg["result"], now)
            # 5 Hz/ticker frames remain in raw storage; never counted as 1 Hz settlement samples.
            if kind not in ("cfbenchmarks_value_5hz", "ticker", "subscribed", "ok"):
                self.process(now, row["id"], kind, msg)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            self.invalidate(now, "invalid_data")
            self.error("INVALID_DATA", now, detail=str(exc))
            return False
        return True

    def settle(self, ticker, result, now):
        market = self.markets.get(ticker)
        if not market:
            return
        if now < market.close_time or result not in ("yes", "no"):
            raise ValueError("Premature settlement")
        self.executor.settle(market, result, now)

    def process(self, now, event_id, kind, msg):
        c = self.config
        for ticker, market in list(self.markets.items()):
            if ticker in self._pending_settlement:
                continue
            state = self.store.state(self.run_id, ticker)
            if state in ("CLOSED", "ERROR", "HALTED"):
                continue
            if now >= market.close_time:
                order = self.executor.orders.get(ticker)
                if order and order.active:
                    self.executor.cancel(ticker, now, "entry_window_closed")
                if state != "SETTLEMENT_PENDING":
                    self.state(ticker, "SETTLEMENT_PENDING", now)
                self._pending_settlement.add(ticker)
                continue
            book = self.books[ticker]
            resting = self.executor.orders.get(ticker)
            position = self.executor.positions.get(ticker)
            due = now - self.last_evaluation.get(ticker, 0) >= c.evaluation_interval
            # Resting orders are revalidated on every material reference/book/trade event.
            material = kind in (
                "cfbenchmarks_value",
                "orderbook_snapshot",
                "orderbook_delta",
                "trade",
                "heartbeat",
                "metadata",
                "market_lifecycle_v2",
                "stale",
                "disconnect",
            )
            # Show research evaluations throughout an active market. The strategy
            # still rejects entries outside its configured trading window.
            quote_update = (
                kind in ("orderbook_snapshot", "orderbook_delta") and msg.get("market_ticker") == ticker
            )
            if not market.tradable(now) or not (
                due or quote_update or ((resting and resting.active) or position) and material
            ):
                continue

            extras = []
            if self.clock and not 0 <= self.clock() - now <= min(c.reference_max_age, c.book_max_age):
                extras.append("PROCESSING_LAG")
            if not self.entries_active:
                extras.append("MODEL_INACTIVE")
            if not self.healthy:
                extras.append("FEED_UNHEALTHY")
            if not self.clock_ok:
                extras.append("CLOCK_SKEW")
            if not self.exchange_open:
                extras.append("EXCHANGE_PAUSED")
            changes = self.fee_changes.get(market.event_ticker)
            schedule = dict(self.series_fees)
            verified = changes is not None and self.series_fee_changes is not None
            for change in sorted(self.series_fee_changes or [], key=lambda r: timestamp(r["scheduled_ts"])):
                if timestamp(change["scheduled_ts"]) <= now:
                    schedule.update(change)
            for change in sorted(changes or [], key=lambda r: timestamp(r["scheduled_ts"])):
                if timestamp(change["scheduled_ts"]) <= now:
                    for name in ("fee_type", "fee_multiplier"):
                        if change.get(name + "_override") is not None:
                            schedule[name] = change[name + "_override"]
            verified = (
                verified
                and schedule.get("fee_type") in ("quadratic", "quadratic_with_maker_fees")
                and schedule.get("fee_multiplier") == 1
            )
            if verified:
                self.executor.maker_rates[ticker] = (
                    0 if schedule["fee_type"] == "quadratic" else c.maker_fee_rate
                )
            if not verified:
                extras.append("UNVERIFIED_FEES")
            if self.executor.risk.halted:
                extras.append("KILL_SWITCH")
            if resting or position:
                extras.append("EXISTING_ENTRY")
            cached = self._model_cache.get(ticker)
            model_recomputed = (
                cached is None
                or now < cached[0]
                or now - cached[0] >= c.evaluation_interval
                or cached[1] != market.spec
            )
            if model_recomputed:
                try:
                    f = features(self.ticks, now, c)
                    p = probability(market.spec, self.ticks, now, f["sigma"], c)
                    cached = (now, market.spec, f, p, None)
                except ValueError as exc:
                    cached = (now, market.spec, {}, {}, str(exc))
                self._model_cache[ticker] = cached
            model_time, _, f, p, model_error = cached
            try:
                if model_error is not None:
                    raise ValueError(model_error)
                q = quality(f, self.ticks, book, now, c)
                decision = evaluate(market, book, self.ticks[-1], f, p, q, now, c, extras)
            except ValueError as exc:
                f, p, q = {}, {}, dict(score=0, reasons=["MODEL_UNAVAILABLE"])
                decision = dict(
                    decision="NO_TRADE",
                    side=None,
                    reasons=[dict(code="MODEL_UNAVAILABLE", actual=str(exc))],
                    seconds_remaining=market.close_time - now,
                    approval_status="RESEARCH_ONLY",
                )
            if (
                decision["decision"] == "TRADE_CANDIDATE"
                and self.executor.risk.size(decision["expected_fill_price"], now) == 0
            ):
                decision["decision"] = "NO_TRADE"
                decision["reasons"].append(dict(code="RISK_LIMIT"))
            signature = (
                decision["decision"],
                decision.get("side"),
                decision.get("expected_fill_price"),
                tuple(r["code"] for r in decision["reasons"]),
            )
            record_decision = (
                model_recomputed
                or due
                or kind == "cfbenchmarks_value"
                or self._decision_keys.get(ticker) != signature
            )
            self._decision_keys[ticker] = signature
            if record_decision:
                self.last_evaluation[ticker] = now
                op = str(uuid.uuid4())
                body = {
                    **decision,
                    "features": f,
                    "model": self.executor.model_identity,
                    "snapshot_id": event_id,
                    "raw_archive": self.raw_archive,
                    "snapshot_timestamp": now,
                    "event_ticker": market.event_ticker,
                    "market_open_timestamp": market.open_time,
                    "market_close_timestamp": market.close_time,
                    "book_timestamp": book.source_time,
                    "book_received": book.received,
                    "probability": p,
                    "quality": q,
                    "book": book.summary(),
                    "config": asdict(c),
                    "versions": self.versions,
                    "settlement_spec": asdict(market.spec),
                    "fee_schedule": schedule,
                    "fees_verified": verified,
                    "ticker": ticker,
                    "mode": self.mode,
                    "timestamp": now,
                    "model_evaluated_at": model_time,
                    "model_age_seconds": now - model_time,
                }
                if self.record_evaluations:
                    self.store.add("opportunity", body, self.run_id, self.mode, now, ticker, op, record_id=op)
                self.latest[ticker] = body
                self._last_op[ticker] = op
            else:
                op = self._last_op[ticker]
            transition_decision = record_decision and (
                self.record_evaluations or decision["decision"] == "TRADE_CANDIDATE"
            )
            if transition_decision and state in ("WARMUP", "MONITORING"):
                self.state(ticker, "ENTRY_WINDOW", now, op)
                state = "ENTRY_WINDOW"
            if transition_decision and state in ("ENTRY_WINDOW", "NO_TRADE"):
                self.state(ticker, "EVALUATING", now, op)
                self.state(ticker, decision["decision"], now, op)
            # Recheck after model computation: receipt-time freshness alone cannot
            # authorize fills or exits while a live collector is draining a backlog.
            execution_now = self.clock() if self.clock else now
            inputs_fresh = bool(
                book.valid
                and self.ticks
                and 0 <= execution_now - now <= min(c.reference_max_age, c.book_max_age)
                and 0 <= execution_now - self.ticks[-1].received <= c.reference_max_age
                and 0 <= execution_now - book.received <= c.book_max_age
                and -c.max_clock_skew <= execution_now - self.ticks[-1].source <= c.reference_max_age
                and -c.max_clock_skew <= execution_now - book.source_time <= c.book_max_age
            )
            # Entry policy and model quality must not disable safe risk reduction.
            # Unknown health reasons still fail closed; only these known policy/model
            # conditions are allowed through the position-management gate.
            entry_healthy = bool(
                self.execute
                and c.enabled
                and (not extras or extras == ["EXISTING_ENTRY"])
                and not q["reasons"]
                and inputs_fresh
            )
            management_healthy = bool(
                inputs_fresh
                and market.tradable(execution_now)
                and all(r in ("MODEL_INACTIVE", "KILL_SWITCH", "EXISTING_ENTRY") for r in extras)
                and all(r in ("WARMUP", "SHOCK", "REFERENCE_GAP", "MODEL_UNAVAILABLE") for r in q["reasons"])
            )
            if resting and resting.active:
                revalidated = {
                    **decision,
                    "reasons": [r for r in decision["reasons"] if r["code"] != "EXISTING_ENTRY"],
                }
                revalidated["decision"] = "NO_TRADE" if revalidated["reasons"] else "TRADE_CANDIDATE"
                if resting.side != decision["side"]:
                    revalidated["decision"] = "NO_TRADE"
                self.executor.revalidate(market, revalidated, now, entry_healthy)
                if kind == "trade" and msg.get("market_ticker") == ticker and entry_healthy:
                    self.executor.trade(market, msg, now)
                if (
                    kind in ("orderbook_snapshot", "orderbook_delta")
                    and msg.get("market_ticker") == ticker
                    and entry_healthy
                ):
                    self.executor.aggressive(market, book, now)
            if (
                position
                and management_healthy
                and kind in ("orderbook_snapshot", "orderbook_delta")
                and msg.get("market_ticker") == ticker
            ):
                # Price-based exits do not need a probability. Never use a missing
                # or quality-blocked model to invent a probability-based exit.
                exit_probability = p if not q["reasons"] else {}
                self.executor.monitor(market, book, exit_probability, execution_now, event_id)
            if decision["decision"] == "TRADE_CANDIDATE" and record_decision:
                submit_now = self.clock() if self.clock else now
                fresh = (
                    self.ticks
                    and 0 <= submit_now - self.ticks[-1].received <= c.reference_max_age
                    and 0 <= submit_now - book.received <= c.book_max_age
                )
                order = (
                    self.executor.submit(
                        market,
                        book,
                        decision,
                        op,
                        submit_now,
                        fresh and entry_healthy,
                        **({"entry_evidence": body} if not self.record_evaluations else {}),
                    )
                    if self.execute
                    else None
                )
                if order is None:
                    self.state(ticker, "NO_TRADE", submit_now, op)
                    self.store.add(
                        "execution_rejection",
                        dict(reason="disabled_or_submission_recheck"),
                        self.run_id,
                        self.mode,
                        submit_now,
                        ticker,
                        op,
                    )
