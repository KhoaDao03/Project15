"""Opt-in, bounded research capture. Never supplies inputs to trading decisions."""

import fcntl
import gzip
import hashlib
import json
import logging
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .domain import dumps
from .strategies.settlement_edge.bleep import SIGMA_MULTIPLIERS, capped_confidence

LOG = logging.getLogger(__name__)
REFERENCE = {"reference_history", "cfbenchmarks_value", "cfbenchmarks_value_5hz", "pyth_value"}
EVENTS = {
    "metadata",
    "market_lifecycle_v2",
    "connected",
    "settlement",
    "disconnect",
    "stale",
    "error",
    "bleep_seed",
}


class ResearchLog:
    """One producer, one daemon writer; at most 8 MiB queued per collector."""

    def __init__(
        self,
        root,
        asset,
        run_id,
        config,
        *,
        max_queue_bytes=8 * 1024**2,
        max_disk_bytes=20 * 1024**3,
        min_free_bytes=30 * 1024**3,
        retention_seconds=7 * 86400,
        rotation_seconds=600,
        settlement_db=None,
        order_db=None,
    ):
        self.settlement_db, self.order_db = settlement_db, order_db
        self.settlement_cursor = 0
        self.order_cursor = 0.0
        self.root = Path(root)
        self.asset, self.run_id = asset, run_id
        self.session = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex}"
        self.directory = self.root / asset / self.session
        self.max_queue_bytes = max_queue_bytes
        self.max_disk_bytes, self.min_free_bytes = max_disk_bytes, min_free_bytes
        self.retention_seconds, self.rotation_seconds = retention_seconds, rotation_seconds
        self.queue = queue.Queue(maxsize=8192)
        self.lock = threading.Lock()
        self.queued_bytes = self.dropped = self.written = 0
        self.pending_drops = 0
        self.error = None
        self.stopping = threading.Event()
        self.last_sample, self.last_eval, self.thresholds = {}, {}, {}
        self.metadata = dict(
            schema_version=1,
            asset=asset,
            run_id=run_id,
            session=self.session,
            started_at=time.time(),
            config=asdict(config),
            sigma_multipliers=SIGMA_MULTIPLIERS,
            book_sampling_seconds=1,
            book_depth_levels=5,
            execution_accuracy="sampled books; fills are estimates, not guaranteed",
            max_queue_bytes=max_queue_bytes,
        )
        self.thread = threading.Thread(target=self._writer, name=f"research-log-{asset}", daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            return dict(
                enabled=True,
                session=self.session,
                queued_bytes=self.queued_bytes,
                dropped=self.dropped,
                written=self.written,
                error=self.error,
            )

    def emit(self, kind, body, timestamp=None):
        try:
            data = (
                dumps(dict(schema_version=1, kind=kind, timestamp=timestamp or time.time(), body=body)) + "\n"
            ).encode()
            with self.lock:
                if self.stopping.is_set() or self.queued_bytes + len(data) > self.max_queue_bytes:
                    self.dropped += 1
                    self.pending_drops += 1
                    return False
                try:
                    self.queue.put_nowait(data)
                except queue.Full:
                    self.dropped += 1
                    self.pending_drops += 1
                    return False
                self.queued_bytes += len(data)
            return True
        except Exception as exc:
            self.fail(exc)
            return False

    def fail(self, exc):
        with self.lock:
            self.dropped += 1
            self.pending_drops += 1
            self.error = type(exc).__name__
        LOG.warning("Research recording failure (%s); trading continues", type(exc).__name__)

    def capture(self, engine, row, payload):
        """Called after ingestion. Copy only sampled diagnostics, never retain engine objects."""
        try:
            now = row["received"]
            kind = payload.get("type")
            if kind in REFERENCE | EVENTS:
                self.emit("input", row, now)
            # Inspect only current markets; stale historical evaluations must not be resampled.
            for ticker, market in engine.markets.items():
                if not market.open_time <= now <= market.close_time:
                    continue
                book = engine.books.get(ticker)
                if book is None:
                    continue
                bids = {side: book.bid(side) for side in ("yes", "no")}
                fresh = (
                    book.valid
                    and 0 <= now - book.received <= engine.config.book_max_age
                    and -engine.config.max_clock_skew <= now - book.source_time <= engine.config.book_max_age
                )
                bands = tuple(
                    (bids[s] <= 0.55, bids[s] >= 0.99) if fresh and bids[s] is not None else None
                    for s in ("yes", "no")
                )
                key = (ticker, "book")
                crossing = self.thresholds.get(key) != bands
                if crossing or now - self.last_sample.get(key, 0) >= 1:
                    self.emit(
                        "book",
                        dict(
                            market=ticker,
                            source=book.source_time,
                            received=book.received,
                            valid=book.valid,
                            fresh=fresh,
                            threshold_change=crossing,
                            **book.summary(),
                        ),
                        now,
                    )
                    self.thresholds[key] = bands
                    self.last_sample[key] = now
                evaluation = engine.latest.get(ticker)
                if not evaluation or self.last_eval.get(ticker) == evaluation["timestamp"]:
                    continue
                self.last_eval[ticker] = evaluation["timestamp"]
                confidence = []
                for side in ("yes", "no"):
                    p = evaluation.get("probability", {}).get("p_" + side)
                    b = evaluation.get("book", {})
                    value = capped_confidence(p, b.get(side + "_bid"), b.get(side + "_ask"), self.asset)
                    confidence.append(value is not None and value >= 0.83)
                key = (ticker, "evaluation")
                crossing = self.thresholds.get(key) != tuple(confidence)
                if crossing or now - self.last_sample.get(key, 0) >= 1:
                    self.emit(
                        "evaluation",
                        dict(evaluation, confidence_83=confidence, threshold_change=crossing),
                        now,
                    )
                    self.thresholds[key] = tuple(confidence)
                    self.last_sample[key] = now
            active = {t for t, m in engine.markets.items() if m.close_time >= now}
            self.last_eval = {k: v for k, v in self.last_eval.items() if k in active}
            self.last_sample = {k: v for k, v in self.last_sample.items() if k[0] in active}
            self.thresholds = {k: v for k, v in self.thresholds.items() if k[0] in active}
        except Exception as exc:
            self.fail(exc)

    def _audit_records(self):
        """Read local journals off the trading thread; no credentials or network requests."""
        if self.settlement_db and Path(self.settlement_db).exists():
            with sqlite3.connect(f"file:{self.settlement_db}?mode=ro", uri=True, timeout=0.1) as db:
                for rowid, market, timestamp, kind, body in db.execute(
                    "select rowid,market,timestamp,kind,body from records where rowid>? "
                    "and kind in ('settlement','settlement_evidence') order by rowid limit 200",
                    (self.settlement_cursor,),
                ):
                    self.emit(
                        "settlement_journal", dict(market=market, kind=kind, body=json.loads(body)), timestamp
                    )
                    self.settlement_cursor = rowid
        if self.order_db and Path(self.order_db).exists():
            with sqlite3.connect(f"file:{self.order_db}?mode=ro", uri=True, timeout=0.1) as db:
                cutoff = time.time()
                for (raw,) in db.execute(
                    "select body from manual_orders where json_extract(body,'$.updated_at')>=? "
                    "and json_extract(body,'$.updated_at')<? "
                    "and json_extract(body,'$.request.ticker') like ?",
                    (self.order_cursor, cutoff, "KX" + self.asset + "15M-%"),
                ):
                    body = json.loads(raw)
                    # Exclude account identifiers and any future arbitrary journal fields.
                    event = {
                        k: body[k]
                        for k in (
                            "id",
                            "request",
                            "state",
                            "created_at",
                            "updated_at",
                            "origin",
                            "automation_reason",
                            "timing",
                            "order_id",
                            "effective_limit_cents",
                        )
                        if k in body
                    }
                    event["exchange_order"] = {
                        k: v for k, v in (body.get("exchange_order") or {}).items() if k != "user_id"
                    }
                    self.emit("order_journal", event)
                self.order_cursor = cutoff

    def _retention(self):
        # Shared cap across assets; lock only the background writers, never trading.
        with (self.root / ".retention.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            files = sorted(self.root.glob("*/*/events-*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
            extras = list(self.root.glob("*/*/source.json.gz")) + list(self.root.glob("*/*/manifest.json"))
            total = sum(p.stat().st_size for p in files + extras)
            cutoff = time.time() - self.retention_seconds
            for p in files:
                size = p.stat().st_size
                if p.stat().st_mtime < cutoff or total > self.max_disk_bytes:
                    p.unlink()
                    total -= size
            for manifest in self.root.glob("*/*/manifest.json"):
                directory = manifest.parent
                if (
                    directory != self.directory
                    and manifest.stat().st_mtime < cutoff
                    and not list(directory.glob("events-*"))
                ):
                    for name in ("manifest.json", "source.json.gz", "status.json"):
                        (directory / name).unlink(missing_ok=True)
                    # Only remove our own empty session directories.
                    if not any(directory.iterdir()):
                        directory.rmdir()
            # Open segments may temporarily exceed the cap until rotation; never delete live files.
        return shutil.disk_usage(self.root).free >= self.min_free_bytes

    def _writer(self):
        stream = None
        part = None
        try:
            self.directory.mkdir(parents=True, mode=0o700)
            source = {
                str(p.relative_to(Path(__file__).parent)): p.read_text()
                for p in Path(__file__).parent.rglob("*.py")
            }
            project = Path(__file__).resolve().parents[2]
            for name in ("pyproject.toml", "uv.lock"):
                if (project / name).is_file():
                    source[name] = (project / name).read_text()
            self.metadata["python_version"] = sys.version
            raw = dumps(source).encode()
            self.metadata["source_sha256"] = hashlib.sha256(raw).hexdigest()
            with gzip.open(self.directory / "source.json.gz", "wb", compresslevel=1) as f:
                f.write(raw)
            (self.directory / "manifest.json").write_text(dumps(self.metadata) + "\n")
            maintenance = opened = flushed = audit_at = 0
            allowed = False
            while not self.stopping.is_set() or not self.queue.empty():
                now = time.time()
                if not self.stopping.is_set() and now - audit_at >= 30:
                    try:
                        self._audit_records()
                    except Exception as exc:
                        self.fail(exc)
                    audit_at = now
                if now - maintenance >= 5:
                    allowed = self._retention()
                    maintenance = now
                    self.error = None if allowed else "LOW_DISK"
                    (self.directory / "status.json").write_text(dumps(self.status()) + "\n")
                if stream and now - opened >= self.rotation_seconds:
                    stream.close()
                    part.rename(part.with_suffix(""))
                    stream = None
                try:
                    data = self.queue.get(timeout=0.25)
                except queue.Empty:
                    if stream and now - flushed >= 1:
                        stream.flush()
                        flushed = now
                    continue
                with self.lock:
                    self.queued_bytes -= len(data)
                if not allowed:
                    with self.lock:
                        self.dropped += 1
                        self.pending_drops += 1
                    continue
                if stream is None:
                    part = self.directory / f"events-{time.time_ns()}.jsonl.gz.part"
                    stream = gzip.open(part, "wb", compresslevel=1)
                    opened = now
                with self.lock:
                    lost = self.pending_drops
                    self.pending_drops = 0
                if lost:
                    stream.write(
                        (
                            dumps(
                                dict(
                                    schema_version=1,
                                    kind="recording_gap",
                                    timestamp=now,
                                    body=dict(dropped_records=lost),
                                )
                            )
                            + "\n"
                        ).encode()
                    )
                stream.write(data)
                self.written += 1
                if now - flushed >= 1:
                    stream.flush()
                    flushed = now
        except Exception as exc:
            self.error = type(exc).__name__
            LOG.warning("Research writer stopped (%s); trading continues", type(exc).__name__)
        finally:
            with self.lock:
                self.stopping.set()
                lost = self.queue.qsize()
                self.dropped += lost
                self.pending_drops += lost
            try:
                if stream:
                    stream.close()
                    part.rename(part.with_suffix(""))
                (self.directory / "status.json").write_text(
                    dumps(
                        dict(
                            self.status(),
                            closed_at=time.time(),
                            clean_shutdown=self.stopping.is_set() and self.queue.empty(),
                        )
                    )
                    + "\n"
                )
            except Exception:
                LOG.warning("Unable to finalize research recording status")
            self.stopping.set()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=2)


def start_research_log(config, run_id, *, settlement_db=None):
    """Disabled unless explicitly enabled at deployment; no active environment changes here."""
    if os.environ.get("BTC15_RESEARCH_LOG_ENABLED") != "1":
        return None
    root = os.environ.get("BTC15_RESEARCH_LOG_DIR", "research-logs")
    try:
        return ResearchLog(
            root,
            config.asset,
            run_id,
            config,
            settlement_db=settlement_db,
            order_db=Path(settlement_db).parent.parent / "manual-orders.sqlite" if settlement_db else None,
        )
    except Exception:
        LOG.exception("Research recorder unavailable; trading continues")
        return None
