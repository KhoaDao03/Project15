"""Latched fleet cutoff using the same realized history as the dashboard."""

import asyncio
import json
import math
import time

from fastapi import HTTPException

from .live_fallback import LiveFallbackStore


class GlobalLossGuard:
    limit = -50.0
    interval = 15 * 60
    max_age = interval + 30

    def __init__(self, manual, members, stores):
        self.manual = manual
        self.members = members
        self.histories = {
            a: LiveFallbackStore(stores[a], manual.path, m["run_id"], a) for a, m in members.items()
        }
        self.cache = {}
        self.stop = asyncio.Event()
        self.updated_at = None
        self.pnl = None
        self.error = None
        with manual.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS global_loss_guard (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)"
            )
            row = db.execute("SELECT body FROM global_loss_guard WHERE id=1").fetchone()
        self.latch = json.loads(row[0]) if row else None

    def calculate(self):
        totals = []
        for asset, history in self.histories.items():
            run = self.members[asset]["run_id"]
            revision = history.history_revision("PAPER", run)
            cached = self.cache.get(asset)
            if cached is None or cached[0] != revision:
                with history.history_snapshot():
                    rows = history.list("trade_result", run_id=run, mode="PAPER", limit=None)
                values = [r["body"]["net_pnl"] for r in rows]
                if any(not math.isfinite(v) for v in values):
                    raise ValueError("Invalid realized P&L")
                cached = revision, math.fsum(values)
                self.cache[asset] = cached
            totals.append(cached[1])
        return round(math.fsum(totals), 4)

    def apply(self, pnl, now):
        if not math.isfinite(pnl):
            raise ValueError("Invalid global P&L")
        self.pnl, self.updated_at, self.error = pnl, now, None
        if self.latch or pnl <= self.limit:
            latch = self.latch or dict(
                scope="global",
                pnl=pnl,
                triggered_at=now,
                reason="Combined dashboard realized P&L reached -$50; all new live buys disabled",
            )
            # Latch and all permissions commit together; open-position exit controls stay intact.
            with self.manual.db() as db:
                db.execute("INSERT OR REPLACE INTO global_loss_guard VALUES (1, ?)", (json.dumps(latch),))
                for table, key in [("live_assets", "asset"), ("live_controls", "ticker")]:
                    for identity, raw in db.execute(f"SELECT {key},body FROM {table}").fetchall():
                        body = json.loads(raw)
                        if body.get("enabled") or body.get("loss_guard") != latch:
                            body.update(enabled=False, revision=body["revision"] + 1, loss_guard=latch)
                            db.execute(
                                f"UPDATE {table} SET body=? WHERE {key}=?", (json.dumps(body), identity)
                            )
            self.latch = latch

    def check(self, now):
        if self.latch:
            raise HTTPException(409, self.latch["reason"])
        if self.error or self.updated_at is None or not 0 <= now - self.updated_at <= self.max_age:
            raise HTTPException(409, "Global realized P&L check unavailable; new buys paused, exits continue")

    def state(self):
        return dict(
            limit=self.limit,
            check_interval_seconds=self.interval,
            basis="all_time_dashboard_realized",
            pnl=self.pnl,
            updated_at=self.updated_at,
            triggered=self.latch is not None,
            error=self.error,
        )

    async def run(self):
        try:
            while not self.stop.is_set():
                try:
                    pnl = await asyncio.to_thread(self.calculate)
                    self.apply(pnl, time.time())
                except Exception as exc:
                    self.error = type(exc).__name__
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.interval)
                except TimeoutError:
                    pass
        finally:
            for history in self.histories.values():
                history.close_history()
