"""Dashboard-only fallback from durable live fills when paper has no purchase."""

import json
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

from .domain import timestamp


class LiveFallbackStore:
    def __init__(self, store, journal, run_id, asset):
        self.store, self.journal, self.run_id, self.asset = store, Path(journal), run_id, asset
        self._history = ContextVar("live_fallback_snapshot", default=None)
        self._journal_reader = None
        self._revision_lock = threading.Lock()
        self._cached_revision = None
        self._cached_fallback = None

    def history_revision(self, mode, run_id=None):
        revision = self.store.history_revision(mode, run_id)
        if mode != "PAPER" or not self.journal.exists():
            return revision, None
        with self._revision_lock:
            if self._journal_reader is None:
                self._journal_reader = sqlite3.connect(
                    self.journal.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
                )
            # data_version is comparable only on the same connection. It detects
            # committed updates even when an existing order ID is reconciled.
            version = self._journal_reader.execute("PRAGMA data_version").fetchone()[0]
        return revision, version

    def close_history(self):
        with self._revision_lock:
            if self._journal_reader is not None:
                self._journal_reader.close()
                self._journal_reader = None

    def __getattr__(self, name):
        return getattr(self.store, name)

    @contextmanager
    def history_snapshot(self):
        with self.store.transaction() as connection:
            # sqlite3 legacy transaction mode does not BEGIN for SELECT alone.
            if (
                connection.dialect.name == "sqlite"
                and not connection.connection.driver_connection.in_transaction
            ):
                connection.exec_driver_sql("BEGIN")
            token = self._history.set(self.fallback())
            try:
                yield
            finally:
                self._history.reset(token)

    def fallback(self):
        frozen = self._history.get()
        if frozen is not None:
            return frozen
        revision = self.history_revision("PAPER", self.run_id)
        with self._revision_lock:
            if self._cached_revision != revision or self._cached_fallback is None:
                self._cached_fallback = self._fallback()
                self._cached_revision = revision
            return self._cached_fallback

    def _fallback(self):
        if not self.journal.exists():
            return []
        paper = self.store.list("fill", self.run_id, "PAPER", limit=None)
        occupied = {r["market"] for r in paper if r["body"].get("action") == "buy"}
        occupied.update(
            r["market"] for r in self.store.list("trade_result", self.run_id, "PAPER", limit=None)
        )
        with sqlite3.connect(self.journal.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = [
                json.loads(b)
                for (b,) in db.execute(
                    "SELECT body FROM manual_orders WHERE json_extract(body,'$.request.ticker') LIKE ?",
                    ("KX" + self.asset + "15M-%",),
                )
            ]
        groups = defaultdict(list)
        for row in rows:
            ticker = row["request"]["ticker"]
            if ticker not in occupied:
                groups[ticker].append(row)
        result = []
        settlements = {
            r["market"]: r for r in self.store.list("settlement", self.run_id, "PAPER", limit=None)
        }
        for market, orders in groups.items():
            # Mixed manual purchases cannot be allocated reliably to the bot.
            if any(
                r.get("origin") != "bot"
                and r["request"]["action"] == "buy"
                and Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0
                for r in orders
            ):
                continue
            buys = [
                r
                for r in orders
                if r.get("origin") == "bot"
                and r["request"]["action"] == "buy"
                and Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0
            ]
            if not buys or len({r["request"]["side"] for r in buys}) != 1:
                continue
            side = buys[0]["request"]["side"]
            opened = min(
                (
                    timestamp(r["exchange_order"]["created_time"])
                    if r["exchange_order"].get("created_time")
                    else r["created_at"]
                )
                for r in buys
            )
            trade_id = "live-fallback:" + market
            records = []
            bought = sold = cost = proceeds = fees = Decimal(0)
            last = opened
            reason = ""
            valid = True

            def record(kind, suffix, at, body):
                return dict(
                    id=trade_id + ":" + suffix,
                    opportunity_id=trade_id,
                    run_id=self.run_id,
                    mode="PAPER",
                    market=market,
                    timestamp=at,
                    kind=kind,
                    body=dict(body, source="LIVE_FALLBACK", trade_id=trade_id),
                )

            for row in sorted(orders, key=lambda r: r["created_at"]):
                request, order = row["request"], row.get("exchange_order") or {}
                quantity = Decimal(order.get("fill_count_fp", "0"))
                if (
                    quantity <= 0
                    or request["side"] != side
                    or row["created_at"] < min(r["created_at"] for r in buys)
                ):
                    continue
                try:
                    paid = sum(
                        (Decimal(order[k]) for k in ("maker_fill_cost_dollars", "taker_fill_cost_dollars")),
                        Decimal(0),
                    )
                    fee = sum(
                        (Decimal(order[k]) for k in ("maker_fees_dollars", "taker_fees_dollars")), Decimal(0)
                    )
                    action = request["action"]
                    # Kalshi sells represent acquisition of the opposite outcome.
                    expected_side = side if action == "buy" else ("no" if side == "yes" else "yes")
                    if order["outcome_side"] != expected_side:
                        raise ValueError("Unexpected outcome accounting")
                    value = paid if action == "buy" else quantity - paid
                    if (
                        not all(x.is_finite() for x in (quantity, value, fee))
                        or not 0 <= value <= quantity
                        or fee < 0
                    ):
                        raise ValueError("Invalid fill economics")
                    date = order.get("last_update_time") or order.get("created_time")
                    at = timestamp(date) if date else row["created_at"]
                except (KeyError, ValueError, ArithmeticError):
                    valid = False
                    break
                if action == "buy":
                    bought += quantity
                    cost += value
                else:
                    sold += quantity
                    proceeds += value
                    reason = row.get("automation_reason") or "MANUAL_SELL"
                fees += fee
                last = max(last, at)
                records.append(
                    record(
                        "fill",
                        row["id"],
                        at,
                        dict(
                            action=action,
                            side=side,
                            quantity=float(quantity),
                            price=float(value / quantity),
                            fee=float(fee),
                            live_order_id=row["id"],
                            reason=row.get("automation_reason"),
                        ),
                    )
                )
            if not valid or sold > bought or bought <= 0:
                continue
            remaining = bought - sold
            settlement = settlements.get(market)
            settled = (
                settlement
                and settlement["body"].get("result") in ("yes", "no")
                and settlement["timestamp"] >= opened
            )
            if remaining and settled:
                proceeds += remaining if settlement["body"]["result"] == side else 0
                remaining = Decimal(0)
                last = max(last, settlement["timestamp"])
                reason = "SETTLEMENT"
            # A display reset must preserve orders needed by the daily loss guard.
            # Never hide inventory that still needs management.
            if not remaining and all(r.get("dashboard_history_cleared") is True for r in buys):
                continue
            result.extend(records)
            if not remaining:
                net = proceeds - cost - fees
                body = dict(
                    side=side,
                    bought=float(bought),
                    quantity=0,
                    cost=float(cost),
                    proceeds=float(proceeds),
                    fees=float(fees),
                    gross_pnl=float(proceeds - cost),
                    net_pnl=float(net),
                    opened=opened,
                    holding_seconds=last - opened,
                    reason=reason,
                    exit_reason=reason,
                    win=net > 0,
                    return_on_capital=float(net / (cost + fees)) if cost + fees else 0,
                )
                if reason == "SETTLEMENT":
                    body.update(
                        settlement_result=settlement["body"]["result"],
                        settlement_timestamp=settlement["timestamp"],
                    )
                result.append(record("trade_result", "result", last, body))
        return result

    def list(
        self,
        kind=None,
        run_id=None,
        mode=None,
        market=None,
        opportunity_id=None,
        limit=10000,
        newest_first=False,
    ):
        filters = dict(kind=kind, run_id=run_id, mode=mode, market=market, opportunity_id=opportunity_id)
        if (
            kind not in (None, "fill", "trade_result")
            or mode not in (None, "PAPER")
            or run_id not in (None, self.run_id)
        ):
            return self.store.list(**filters, limit=limit, newest_first=newest_first)
        rows = self.store.list(kind, run_id, mode, market, opportunity_id, limit=None)
        rows += [r for r in self.fallback() if all(v is None or r[k] == v for k, v in filters.items())]
        rows.sort(key=lambda r: (r["timestamp"], r["id"]), reverse=newest_first)
        return rows if limit is None else rows[:limit]

    def trade_revision(self, mode):
        return self.history_revision(mode)
