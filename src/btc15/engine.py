"""One causal event consumer shared by collector, paper mode and backtests."""

import hashlib
import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .domain import Book, dumps, parse_market, timestamp
from .execution import PaperExecutor
from .model import Tick, features, probability, quality
from .strategy import evaluate

log = logging.getLogger("btc15")
VERSIONS = dict(
    strategy="btc15-v1", probability="settlement-mc-logwalk-v1", volatility="rv-ewma-v1", software="0.1.0"
)


class Engine:
    def __init__(self, store, config, mode="PAPER", run_id=None, execute=True, clock=None):
        if mode not in ("PAPER", "BACKTEST"):
            raise ValueError("Research modes only")
        self.store, self.config, self.mode = store, config, mode
        self.run_id = run_id or str(uuid.uuid4())
        self.execute, self.clock = execute, clock
        self.markets, self.books, self.last_evaluation, self.latest = {}, {}, {}, {}
        self.ticks = []
        self.sequences = {}
        self.connection = None
        self.healthy = False
        self.clock_ok = False
        self.exchange_open = False
        self.fees_ok = False
        self.fee_changes = {}
        self.last_received = -float("inf")
        self.last_mono = None
        self.last_wall = None
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
        self.store.add(
            "run",
            dict(
                config=asdict(config),
                versions=self.versions,
                execute=execute,
                source_snapshot=source_snapshot,
            ),
            self.run_id,
            mode,
            time.time(),
        )

    def state(self, market, target, now, op=""):
        self.store.transition(self.run_id, self.mode, market, target, now, op)

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
            raise ValueError("Receive order moved backwards")
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
                self.fees_ok = series.get("fee_type") == "quadratic" and series.get("fee_multiplier") == 1
                self.fee_changes = msg.get("fee_changes", {})
                self.clock_ok = abs(msg.get("clock_skew", float("inf"))) <= self.config.max_clock_skew
                self.exchange_open = msg.get("exchange_status", {}).get("trading_active") is True
                for raw in msg["markets"]:
                    try:
                        market = parse_market(raw, series)
                    except (ValueError, KeyError, TypeError) as exc:
                        ticker = raw.get("ticker", "")
                        if raw.get("floor_strike") is None and raw.get("status") != "active":
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
        if not self.store.claim(self.run_id, "settlement:" + ticker):
            return
        self.store.add("settlement", dict(result=result), self.run_id, self.mode, now, ticker)
        self.executor.settle(market, result, now)

    def process(self, now, event_id, kind, msg):
        c = self.config
        for ticker, market in list(self.markets.items()):
            state = self.store.state(self.run_id, ticker)
            if state in ("CLOSED", "ERROR", "HALTED"):
                continue
            if now >= market.close_time:
                self.executor.cancel(ticker, now, "entry_window_closed")
                if self.store.state(self.run_id, ticker) != "SETTLEMENT_PENDING":
                    self.state(ticker, "SETTLEMENT_PENDING", now)
                continue
            book = self.books[ticker]
            resting = self.executor.orders.get(ticker)
            position = self.executor.positions.get(ticker)
            in_window = 0 < market.close_time - now <= c.entry_window_start
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
            if not in_window or not (due or ((resting and resting.active) or position) and material):
                continue
            self.last_evaluation[ticker] = now
            extras = []
            if not self.healthy:
                extras.append("FEED_UNHEALTHY")
            if not self.clock_ok:
                extras.append("CLOCK_SKEW")
            if not self.exchange_open:
                extras.append("EXCHANGE_PAUSED")
            changes = self.fee_changes.get(market.event_ticker)
            verified = self.fees_ok and changes is not None
            if changes:
                effective = sorted(
                    (r for r in changes if timestamp(r["scheduled_ts"]) <= now),
                    key=lambda r: r["scheduled_ts"],
                )
                if effective:
                    change = effective[-1]
                    verified = (
                        verified
                        and change.get("fee_type_override") in (None, "quadratic")
                        and change.get("fee_multiplier_override") in (None, 1)
                    )
            if not verified:
                extras.append("UNVERIFIED_FEES")
            if self.executor.risk.halted:
                extras.append("KILL_SWITCH")
            if resting or position:
                extras.append("EXISTING_ENTRY")
            try:
                f = features(self.ticks, now, c)
                q = quality(f, self.ticks, book, now, c)
                p = probability(market.spec, self.ticks, now, f["sigma"], c)
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
            op = str(uuid.uuid4())
            body = {
                **decision,
                "features": f,
                "probability": p,
                "quality": q,
                "book": book.summary(),
                "config": asdict(c),
                "versions": self.versions,
                "settlement_spec": asdict(market.spec),
                "ticker": ticker,
                "mode": self.mode,
                "timestamp": now,
            }
            self.store.add("opportunity", body, self.run_id, self.mode, now, ticker, op, record_id=op)
            self.latest[ticker] = body
            if state in ("WARMUP", "MONITORING"):
                self.state(ticker, "ENTRY_WINDOW", now, op)
                state = "ENTRY_WINDOW"
            if state in ("ENTRY_WINDOW", "NO_TRADE"):
                self.state(ticker, "EVALUATING", now, op)
                self.state(ticker, decision["decision"], now, op)
            healthy = not extras or extras == ["EXISTING_ENTRY"]
            healthy = healthy and not q["reasons"]
            if resting and resting.active:
                revalidated = {
                    **decision,
                    "reasons": [r for r in decision["reasons"] if r["code"] != "EXISTING_ENTRY"],
                }
                revalidated["decision"] = "NO_TRADE" if revalidated["reasons"] else "TRADE_CANDIDATE"
                if resting.side != decision["side"]:
                    revalidated["decision"] = "NO_TRADE"
                self.executor.revalidate(market, revalidated, now, healthy)
                if kind == "trade" and msg.get("market_ticker") == ticker and healthy:
                    self.executor.trade(market, msg, now)
                if (
                    kind in ("orderbook_snapshot", "orderbook_delta")
                    and msg.get("market_ticker") == ticker
                    and healthy
                ):
                    self.executor.aggressive(market, book, now)
            if (
                position
                and p
                and healthy
                and kind in ("orderbook_snapshot", "orderbook_delta")
                and msg.get("market_ticker") == ticker
            ):
                self.executor.monitor(market, book, p, now, event_id)
            if decision["decision"] == "TRADE_CANDIDATE":
                submit_now = self.clock() if self.clock else now
                fresh = (
                    self.ticks
                    and 0 <= submit_now - self.ticks[-1].received <= c.reference_max_age
                    and 0 <= submit_now - book.received <= c.book_max_age
                )
                order = (
                    self.executor.submit(market, book, decision, op, submit_now, fresh and healthy)
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
