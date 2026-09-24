"""Background recording coverage and durable research-only recovery obligations."""

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .domain import dumps, timestamp

INDEX_NAME = "coverage.sqlite"


def atomic_json(path, body):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(body) + "\n")
    temporary.replace(path)


class Coverage:
    def __init__(self, root, asset, session, started_at, capture_mode="full"):
        self.asset, self.session, self.started_at = asset, session, started_at
        self.capture_mode = capture_mode
        self.gaps = []
        self.gap_records = 0
        self.db = sqlite3.connect(Path(root) / INDEX_NAME, timeout=2)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS markets (asset TEXT, ticker TEXT, raw TEXT, "
            "open_time REAL, close_time REAL, settlement TEXT, PRIMARY KEY(asset,ticker))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS market_sessions (asset TEXT, ticker TEXT, session TEXT, "
            "PRIMARY KEY(asset,ticker,session))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS reference_samples (asset TEXT, source REAL, received REAL, "
            "price REAL, input_id TEXT, PRIMARY KEY(asset,source))"
        )
        self.db.commit()
        self.markets = {}
        self.sequences = {}
        self.connection = None
        self.books = set()

    def market(self, ticker):
        return self.markets.setdefault(
            ticker,
            dict(
                market=ticker,
                input_counts={},
                book_snapshots=0,
                sequence_gaps=0,
                book_continuity_breaks=0,
                decisions=0,
                model_calculations=0,
                reference_fallbacks=0,
            ),
        )

    def observe(self, record):
        kind, body = record["kind"], record["body"]
        if kind == "recording_gap":
            self.gap_records += body["dropped_records"]
            start = body.get("previous_capture_at")
            start = self.started_at if start is None else start
            end = body.get("next_capture_at")
            if end is not None and end < start:
                start, end = end, start
            self.gaps.append(
                dict(
                    start=start,
                    end=end,
                )
            )
            return
        if kind == "book" and body.get("capture_mode") == "sampled":
            market = self.market(body["market"])
            market["sampled_books"] = market.get("sampled_books", 0) + 1
            market["last_sampled_book_at"] = record["received_at"]
            return
        if kind == "reference_sample":
            old = self.db.execute(
                "SELECT price FROM reference_samples WHERE asset=? AND source=?", (self.asset, body["source"])
            ).fetchone()
            if old and old[0] != body["price"]:
                raise ValueError("Conflicting accepted reference samples")
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO reference_samples VALUES (?,?,?,?,?)",
                    (self.asset, body["source"], body["received"], body["price"], body["input_id"]),
                )
            return
        if kind == "settlement_observation":
            self.settlement(body["market"], body["result"], record)
            return
        if kind in ("model_calculation", "decision_check") and body.get("market"):
            market = self.market(body["market"])
            if kind == "decision_check":
                market["decisions"] += 1
            else:
                market["model_calculations"] += 1
                market["reference_fallbacks"] += bool(body.get("probability", {}).get("atr_fallback_reason"))
                market["last_reference_readiness"] = body.get("features", {}).get("reference_readiness")
            return
        if kind != "input" or "payload" not in body:
            return
        payload = json.loads(body["payload"]) if isinstance(body["payload"], str) else body["payload"]
        if not isinstance(payload, dict) or not isinstance(payload.get("msg", {}), dict):
            return
        event, msg = payload.get("type"), payload.get("msg", {})
        if event == "metadata":
            for raw in msg.get("markets", []):
                if not isinstance(raw, dict) or not raw.get("ticker", "").startswith(
                    "KX" + self.asset + "15M-"
                ):
                    continue
                ticker = raw["ticker"]
                market = self.market(ticker)
                try:
                    opened, closed = timestamp(raw["open_time"]), timestamp(raw["close_time"])
                except (KeyError, TypeError, ValueError):
                    opened = closed = None
                market.update(open_time=opened, close_time=closed)
                with self.db:
                    self.db.execute(
                        "INSERT INTO markets VALUES (?,?,?,?,?,NULL) ON CONFLICT(asset,ticker) "
                        "DO UPDATE SET raw=excluded.raw, open_time=excluded.open_time, "
                        "close_time=excluded.close_time",
                        (self.asset, ticker, dumps(raw), opened, closed),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO market_sessions VALUES (?,?,?)",
                        (self.asset, ticker, self.session),
                    )
        ticker = msg.get("market_ticker")
        if event in ("disconnect", "stale", "error") or body.get("connection_id") != self.connection:
            for known in self.books:
                self.market(known)["book_continuity_breaks"] += 1
            self.books.clear()
            self.connection = body.get("connection_id")
            self.sequences.clear()
        sid, seq = payload.get("sid"), payload.get("seq")
        sequence_gap = False
        if sid is not None and seq is not None:
            previous = self.sequences.get(sid)
            sequence_gap = previous is not None and seq != previous + 1
            if self.capture_mode == "sampled" and event == "orderbook_snapshot":
                sequence_gap = False  # Deltas on this subscription are intentionally omitted.
            self.sequences[sid] = seq
        if ticker and ticker.startswith("KX" + self.asset + "15M-"):
            market = self.market(ticker)
            market["input_counts"][event] = market["input_counts"].get(event, 0) + 1
            market.setdefault("first_received_at", record["received_at"])
            market["last_received_at"] = record["received_at"]
            if event == "orderbook_snapshot":
                market["book_snapshots"] += 1
                self.books.add(ticker)
            elif event == "orderbook_delta" and ticker not in self.books:
                market["book_continuity_breaks"] += 1
            if sequence_gap:
                market["sequence_gaps"] += 1
            if event == "market_lifecycle_v2" and msg.get("event_type") in ("closed", "settled"):
                market["closure_received_at"] = record["received_at"]
            if (
                event == "settlement"
                or (event == "market_lifecycle_v2" and msg.get("event_type") == "settled")
            ) and msg.get("result") in ("yes", "no"):
                self.settlement(ticker, msg["result"], record)

    def settlement(self, ticker, result, record):
        if result not in ("yes", "no"):
            raise ValueError("Invalid research settlement outcome")
        observed = dict(
            event_id=record.get("caused_by"),
            session=self.session,
            received_at=record.get("received_at"),
            source_time=record.get("source_time"),
            capture_seq=record["capture_seq"],
            result=result,
        )
        old = self.db.execute(
            "SELECT settlement FROM markets WHERE asset=? AND ticker=?", (self.asset, ticker)
        ).fetchone()
        if old and old[0]:
            first = json.loads(old[0])
            if first["result"] != result:
                self.market(ticker)["settlement_conflict"] = True
            observed = first
        else:
            with self.db:
                self.db.execute(
                    "UPDATE markets SET settlement=? WHERE asset=? AND ticker=?",
                    (dumps(observed), self.asset, ticker),
                )
        market = self.market(ticker)
        market["settlement"] = observed
        market["origin_sessions"] = [
            r[0]
            for r in self.db.execute(
                "SELECT session FROM market_sessions WHERE asset=? AND ticker=?", (self.asset, ticker)
            )
        ]

    def summary(self, status, ended_at=None):
        now = ended_at if ended_at is not None else time.time()
        result = []
        for ticker, value in self.markets.items():
            row = self.db.execute(
                "SELECT open_time,close_time,settlement FROM markets WHERE asset=? AND ticker=?",
                (self.asset, ticker),
            ).fetchone()
            if row:
                value.update(open_time=row[0], close_time=row[1])
                if row[2]:
                    value["settlement"] = json.loads(row[2])
            opened, closed = value.get("open_time"), value.get("close_time")
            reasons = []
            if opened is None or closed is None:
                reasons.append("UNKNOWN_MARKET_WINDOW")
            elif self.started_at > opened or now < closed:
                reasons.append("PARTIAL_MARKET_WINDOW")
            if not value.get("settlement"):
                reasons.append("SETTLEMENT_NOT_OBSERVED")
            if not value["book_snapshots"]:
                reasons.append("NO_BOOK_SNAPSHOT")
            if value["book_continuity_breaks"]:
                reasons.append("BOOK_CONTINUITY_BREAK")
            if value["sequence_gaps"]:
                reasons.append("EXCHANGE_SEQUENCE_GAP")
            # Dependencies may extend into the preceding hour of reference history.
            # A late unrelated loss does not retroactively invalidate earlier markets.
            affected = [
                index
                for index, g in enumerate(self.gaps)
                if opened is None
                or closed is None
                or (g["start"] <= closed and (g["end"] is None or g["end"] >= opened - 3600))
            ]
            if affected:
                reasons.append("RECORDING_GAP_IN_DEPENDENCIES")
            if status.get("dropped", 0) > self.gap_records or (
                not status.get("capture_complete") and not self.gaps
            ):
                reasons.append("RECORDING_INCOMPLETE")
            if "coverage_rebuild_required" in status.get("diagnostics", {}):
                reasons.append("COVERAGE_REBUILD_REQUIRED")
            if value.get("settlement_conflict"):
                reasons.append("CONFLICTING_SETTLEMENT")
            result.append(
                dict(
                    value,
                    incomplete_reasons=reasons,
                    capture_mode=self.capture_mode,
                    recording_gap_indexes=affected,
                )
            )
        return dict(
            schema_version=2,
            session=self.session,
            asset=self.asset,
            observed_at=now,
            final=ended_at is not None,
            capture_complete=status.get("capture_complete", False),
            capture_mode=self.capture_mode,
            exact_replay=self.capture_mode == "full",
            recording_gap_intervals=list(self.gaps),
            markets=result,
            note="Coverage evidence, not proof of fill or execution-state completeness",
        )

    def prune_reference(self, now):
        with self.db:
            self.db.execute(
                "DELETE FROM reference_samples WHERE asset=? AND source<?", (self.asset, now - 3600)
            )

    def close(self):
        self.db.close()


def pending_markets(root, asset, now, limit=100, after=""):
    path = Path(root) / INDEX_NAME
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.1) as db:
        try:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT raw FROM markets WHERE asset=? AND settlement IS NULL AND close_time<=? "
                    "AND ticker>? ORDER BY ticker LIMIT ?",
                    (asset, now, after, limit),
                )
            ]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise


async def recover_settlements(log, client, stop):
    """Observe late outcomes for prior sessions; never feed retrospective facts into the bot."""
    after = ""
    while not stop.is_set():
        try:
            pending = await asyncio.to_thread(pending_markets, log.root, log.asset, time.time(), 100, after)
            after = pending[-1]["ticker"] if pending else ""
            for raw in pending:
                if stop.is_set():
                    return
                try:
                    requested = time.time()
                    params = {"exchange_index": raw["exchange_index"]} if "exchange_index" in raw else None
                    final = (await client.get("markets/" + raw["ticker"], params))["market"]
                    received = time.time()
                    if final.get("ticker") != raw["ticker"] or timestamp(final["close_time"]) != timestamp(
                        raw["close_time"]
                    ):
                        raise ValueError("Research settlement market identity changed")
                    if final.get("status") == "finalized" and final.get("result") in ("yes", "no"):
                        log.emit(
                            "settlement_observation",
                            dict(
                                market=raw["ticker"],
                                result=final["result"],
                                evidence=final,
                                request_started_at=requested,
                                origin="research_recovery",
                            ),
                            received,
                            source_time=timestamp(final["settlement_ts"])
                            if final.get("settlement_ts")
                            else None,
                            caused_by=str(uuid.uuid4()),
                        )
                except Exception as exc:
                    log.emit(
                        "settlement_poll_error",
                        dict(market=raw["ticker"], error=type(exc).__name__),
                        processed_at=time.time(),
                    )
        except Exception as exc:
            log.fail(exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass
