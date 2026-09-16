"""Collector feed/backlog gate; portfolio and order-book state remain engine-owned."""

import logging
import threading
import time

from .engine import freshness_rechecks

log = logging.getLogger(__name__)


class CollectorRecovery:
    WARNING_LAG = 0.5
    RESUME_QUEUE_FRACTION = 0.2
    STALLED_STREAM_SECONDS = 10.0

    def __init__(self, config, now):
        self.config = config
        self._gate_lock = threading.Lock()
        self.paused = threading.Event()
        self.paused.set()
        self.drain = threading.Event()
        self.phase = "RECOVERING"
        self.reason = "STARTUP"
        self.since = now
        self.connection = None
        self.connection_started = None
        self.reference = False
        self.snapshots = set()
        self.metadata_at = None
        self.reasons = ["WAITING_FOR_CONNECTION"]
        self.warning = False
        self.lag = 0.0
        self.depth = 0
        self._stalled_since = {}

    def request(self, reason, now):
        with self._gate_lock:
            self.paused.set()
            if self.drain.is_set():
                return
            self.reason = reason
            self.since = now
            self.drain.set()
        log.warning("Collector recovery requested: %s; entries blocked", reason)

    def pressure(self, lag, depth, capacity, now):
        self.lag, self.depth = lag, depth
        warning = lag >= self.WARNING_LAG or depth >= capacity * 0.5
        if warning and not self.warning:
            log.warning("Collector backlog rising: %.3fs, %s/%s events", lag, depth, capacity)
        self.warning = warning
        if (
            lag >= min(2.0, self.config.reference_max_age, self.config.book_max_age)
            or depth >= capacity * 0.8
        ):
            self.request("PROCESSING_OVERLOAD", now)

    def reconnect(self):
        """Called on the worker only after the old socket and its queue are drained."""
        self.phase = "RECOVERING"
        self.connection = None
        self.connection_started = None
        self.reference = False
        self.snapshots.clear()
        self.metadata_at = None
        self.reasons = ["WAITING_FOR_CONNECTION"]
        self._stalled_since.clear()
        self.drain.clear()

    def observe(self, engine, row, payload, valid):
        if self.drain.is_set():
            return
        kind, msg = payload.get("type"), payload.get("msg", {})
        if kind == "connected":
            self.connection = row["connection_id"]
            self.connection_started = row["received"]
            self.reference = False
            self.snapshots.clear()
            self._stalled_since.clear()
        if kind == "metadata" and valid and msg.get("request_started_at", -1) >= self.since:
            self.metadata_at = row["received"]
        if row["connection_id"] != self.connection or not valid:
            return
        if kind == "cfbenchmarks_value" and engine.ticks:
            self.reference = (
                self.connection_started is not None
                and engine.ticks[-1].received == row["received"]
                and engine.ticks[-1].received >= max(self.since, self.connection_started)
            )
        if kind == "orderbook_snapshot":
            # A fresh-looking snapshot without sequence identity cannot establish
            # continuity with subsequent deltas on this subscription.
            if type(payload.get("sid")) is not int or type(payload.get("seq")) is not int:
                self.request("UNSEQUENCED_SNAPSHOT", row["received"])
            elif engine.books.get(msg.get("market_ticker")) is not None:
                self.snapshots.add(msg["market_ticker"])

    def check(self, engine, now, lag, depth, capacity, connected, stopping=False):
        if (
            not stopping
            and self.connection is not None
            and self.connection == engine.connection
            and engine._collector_integrity_failed
        ):
            self.request("DATA_INTEGRITY_FAILURE", now)
        if self.drain.is_set():
            self.phase = "DRAINING"
            self.reasons = [self.reason]
            return
        reasons = []
        if stopping:
            reasons.append("STOPPING")
        if not connected or self.connection != engine.connection:
            reasons.append("WAITING_FOR_CONNECTION")
        if self.metadata_at is None or not 0 <= now - self.metadata_at <= 30:
            reasons.append("WAITING_FOR_FRESH_METADATA")
        if not engine.clock_ok:
            reasons.append("CLOCK_SKEW")
        if not engine.exchange_open:
            reasons.append("EXCHANGE_CLOSED")
        if engine.executor.risk.halted:
            reasons.append("HALTED")
        reference_fresh = (
            self.reference
            and engine.healthy
            and engine.ticks
            and 0 <= now - engine.ticks[-1].received <= self.config.reference_max_age
            and -self.config.max_clock_skew <= now - engine.ticks[-1].source <= self.config.reference_max_age
        )
        stalled = []
        if not reference_fresh:
            reasons.append("WAITING_FOR_FRESH_REFERENCE")
            stalled.append("REFERENCE_STREAM_STALLED")
        if lag > self.WARNING_LAG or depth > capacity * self.RESUME_QUEUE_FRACTION:
            reasons.append("BACKLOG_NOT_DRAINED")
        markets = [m for m in engine.markets.values() if m.tradable(now)]
        if not markets:
            reasons.append("NO_ACTIVE_MARKET")
        for market in markets:
            ticker = market.ticker
            market_blocked = ticker in engine.executor.quarantines or ticker in engine.executor.venue_pauses
            if market_blocked:
                reasons.append("MARKET_INTEGRITY:" + ticker)
            book_stale = ticker not in self.snapshots
            if ticker not in self.snapshots:
                reasons.append("WAITING_FOR_SEQUENCED_SNAPSHOT:" + ticker)
            else:
                for failure in freshness_rechecks(engine.books[ticker], engine.ticks, now, now, self.config):
                    reasons.append(failure["code"] + ":" + ticker)
                    book_stale |= failure["code"].startswith("BOOK_")
            if book_stale and not market_blocked:
                stalled.append("BOOK_STREAM_STALLED:" + ticker)
        if (
            stopping
            or not connected
            or self.connection is None
            or self.connection != engine.connection
            or not engine.clock_ok
            or not engine.exchange_open
        ):
            stalled = []
        # A live socket (or the 5 Hz display feed) does not prove that required
        # streams are progressing. Block immediately; retry only after a sustained
        # gap, using the same ordered drain/new-snapshot path as transport failure.
        monotonic = time.monotonic()
        self._stalled_since = {reason: self._stalled_since.get(reason, monotonic) for reason in stalled}
        for reason, since in self._stalled_since.items():
            if monotonic - since >= self.STALLED_STREAM_SECONDS:
                self.request(reason, now)
                break
        # Receipt can request a new drain while the worker is checking freshness.
        # Never clear its entry gate using the result of an earlier check.
        with self._gate_lock:
            if self.drain.is_set():
                self.phase, self.reasons = "DRAINING", [self.reason]
                self.paused.set()
            else:
                self.reasons = sorted(set(reasons))
                self.phase = "RECOVERING" if reasons else "READY"
                if reasons:
                    self.paused.set()
                else:
                    self.paused.clear()

    def status(self):
        return dict(
            state="DRAINING" if self.drain.is_set() else self.phase,
            reason=self.reason,
            reasons=[self.reason] if self.drain.is_set() else self.reasons,
            entries_blocked=self.paused.is_set(),
            since=self.since,
            warning=self.warning,
            oldest_event_lag=self.lag,
            outstanding_events=self.depth,
        )
