"""Opt-in, bounded research capture. Never supplies inputs to trading decisions."""

import fcntl
import gzip
import hashlib
import json
import logging
import math
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .domain import dumps, jsonable
from .research_control import read_control
from .research_coverage import Coverage, atomic_json
from .strategies.settlement_edge.bleep import SIGMA_MULTIPLIERS, capped_confidence

LOG = logging.getLogger(__name__)


def input_source_time(payload):
    """Normalize only known exchange clock fields; missing times remain unknown."""
    try:
        msg = payload.get("msg", {})
        if payload.get("type") == "cfbenchmarks_value":
            value = float(json.loads(msg["data"])["time"]) / 1000
            return value if math.isfinite(value) else None
        for key in ("source_ts_ms", "ts_ms"):
            if key in msg:
                value = float(msg[key]) / 1000
                return value if math.isfinite(value) else None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        pass
    return None


class ResearchLog:
    """Serialized capture from receipt/processing threads; one bounded daemon writer."""

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
        producer="collector",
        capture_mode="full",
        archive_dir=None,
    ):
        if capture_mode not in ("full", "sampled"):
            raise ValueError("Research capture_mode must be full or sampled")
        self.settlement_db, self.order_db = settlement_db, order_db
        self.settlement_cursor = 0
        self.order_cursor = 0.0
        self.execution_cursor = 0
        self.root = Path(root)
        self.archive_dir = (
            Path(archive_dir) if archive_dir else self.root.with_name(self.root.name + "-archive")
        )
        if (
            self.root.resolve() == self.archive_dir.resolve()
            or self.root.resolve() in self.archive_dir.resolve().parents
            or self.archive_dir.resolve() in self.root.resolve().parents
        ):
            raise ValueError("Research archive must be separate from the capture directory")
        self.capture_mode = capture_mode
        self.sampled_out = {}
        self.paused = False
        self.paused_intervals = 0
        self.suppressed_records = 0
        self.pause_started_at = None
        self.diagnostics = {}
        self.asset, self.run_id = asset, run_id
        self.session = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex}"
        self.directory = self.root / asset / self.session
        self.max_queue_bytes = max_queue_bytes
        self.max_disk_bytes, self.min_free_bytes = max_disk_bytes, min_free_bytes
        self.retention_seconds, self.rotation_seconds = retention_seconds, rotation_seconds
        self.queue = queue.Queue(maxsize=8192)
        self.lock = threading.Lock()
        self.queued_bytes = self.dropped = self.written = 0
        self.capture_seq = self.last_written_seq = 0
        self.last_capture_at = None
        self.drop_reasons = {}
        self.error = None
        self.stopping = threading.Event()
        self.close_deadline = None
        self.last_sample, self.last_eval, self.thresholds = {}, {}, {}
        self.metadata = dict(
            schema_version=3 if capture_mode == "sampled" else 2,
            capture_mode=capture_mode,
            exact_replay=capture_mode == "full",
            asset=asset,
            run_id=run_id,
            session=self.session,
            producer=producer,
            process_id=os.getpid(),
            started_at=time.time(),
            config=asdict(config),
            sigma_multipliers=SIGMA_MULTIPLIERS,
            book_sampling_seconds=1,
            book_depth_levels=None if capture_mode == "sampled" else 5,
            input_capture="all received inputs, before analysis"
            if producer == "collector"
            else "published collector snapshots and preflight reads",
            decision_capture="every model calculation and decision check"
            if producer == "collector"
            else "live entry, authorization and preflight checks",
            execution_accuracy="complete received inputs; hypothetical fills remain estimates",
            sequence_scope="session; gap records occupy the first missing sequence",
            max_queue_bytes=max_queue_bytes,
        )
        if capture_mode == "sampled":
            self.metadata.update(
                input_capture="received inputs except depth deltas; no per-input processing records",
                execution_accuracy="sampled observed books; intrasecond prices and fills are estimates",
            )
        self._poll("logging_control", self._sync_control)
        self.thread = threading.Thread(target=self._writer, name=f"research-log-{asset}", daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            return dict(
                enabled=True,
                paused=self.paused,
                paused_intervals=self.paused_intervals,
                suppressed_records=self.suppressed_records,
                pause_started_at=self.pause_started_at,
                session=self.session,
                queued_bytes=self.queued_bytes,
                dropped=self.dropped,
                written=self.written,
                error=self.error,
                last_capture_seq=self.capture_seq,
                last_capture_at=self.last_capture_at,
                last_written_seq=self.last_written_seq,
                drop_reasons=dict(self.drop_reasons),
                capture_complete=self.dropped == 0 and self.error is None and self.paused_intervals == 0,
                capture_mode=self.capture_mode,
                exact_replay=self.capture_mode == "full",
                sampled_out=dict(self.sampled_out),
                diagnostics={k: dict(v) for k, v in self.diagnostics.items()},
                journal_reads_healthy=not any(k.endswith("journal_read") for k in self.diagnostics),
                coverage_index_healthy=not any(k.startswith("coverage_") for k in self.diagnostics),
            )

    def _drop(self, reason, count=1):
        # Caller holds lock. Sequence holes, including a terminal hole in status,
        # identify lost records without an unbounded side queue of gap descriptors.
        self.dropped += count
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + count

    def emit(
        self,
        kind,
        body,
        received_at=None,
        *,
        source_time=None,
        processed_at=None,
        monotonic_ns=None,
        caused_by=None,
        coverage_payload=None,
    ):
        with self.lock:
            if self.paused:
                self.suppressed_records += 1
                return False
            if self.capture_mode == "sampled" and kind == "input_processed":
                self.sampled_out[kind] = self.sampled_out.get(kind, 0) + 1
                return True
            self.capture_seq += 1
            seq = self.capture_seq
            captured_at = self.last_capture_at = time.time()
            try:
                data = json.dumps(
                    dict(
                        schema_version=self.metadata["schema_version"],
                        session=self.session,
                        capture_seq=seq,
                        kind=kind,
                        source_time=source_time,
                        received_at=received_at,
                        processed_at=processed_at,
                        captured_at=captured_at,
                        capture_monotonic_ns=time.monotonic_ns(),
                        received_monotonic_ns=monotonic_ns,
                        caused_by=caused_by,
                        body=body,
                    ),
                    default=jsonable,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode()
                if self.stopping.is_set() or self.queued_bytes + len(data) > self.max_queue_bytes:
                    self._drop("stopped" if self.stopping.is_set() else "queue_bytes")
                    return False
                try:
                    # Coverage needs only a tiny subset of hot records. Snapshot
                    # immutable fields now rather than decode their full archived
                    # envelopes again on the writer (which shares the trading GIL).
                    coverage_record = None
                    if kind == "decision_check":
                        coverage_record = dict(kind=kind, body=dict(market=body.get("market")))
                    elif kind == "input" and isinstance(body.get("payload"), str):
                        coverage_record = dict(
                            kind=kind,
                            capture_seq=seq,
                            received_at=received_at,
                            source_time=source_time,
                            caused_by=caused_by,
                            body=dict(
                                payload=coverage_payload if coverage_payload is not None else body["payload"],
                                connection_id=body.get("connection_id"),
                            ),
                        )
                    self.queue.put_nowait((seq, captured_at, data, kind, coverage_record))
                except queue.Full:
                    self._drop("queue_records")
                    return False
                self.queued_bytes += len(data)
                return True
            except Exception as exc:
                self._drop("serialization")
                self.error = type(exc).__name__
                LOG.warning("Research serialization failed (%s); trading continues", self.error)
                return False

    def fail(self, exc):
        with self.lock:
            self.capture_seq += 1
            self.last_capture_at = time.time()
            self._drop("capture_error")
            self.error = type(exc).__name__
        LOG.warning("Research recording failure (%s); trading continues", type(exc).__name__)

    def diagnostic(self, operation, exc):
        # A retryable database read is not a lost feed input. Keep its cursor
        # unchanged and expose the operation and SQLite cause without a fake gap.
        with self.lock:
            old = self.diagnostics.get(operation, {})
            self.diagnostics[operation] = dict(
                count=old.get("count", 0) + 1,
                at=time.time(),
                error=type(exc).__name__,
                sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                message=str(exc)[:240],
            )
        LOG.warning("Research %s failed (%s): %s", operation, type(exc).__name__, str(exc)[:240])

    def _poll(self, operation, callback):
        try:
            callback()
        except Exception as exc:
            self.diagnostic(operation, exc)
        else:
            with self.lock:
                self.diagnostics.pop(operation, None)

    def _sync_control(self):
        paused = not read_control(self.root).get(self.asset, True)
        if paused == self.paused:
            return
        if paused:
            self.emit("recording_control", dict(action="stop", asset=self.asset), time.time())
            with self.lock:
                self.paused = True
                self.pause_started_at = time.time()
                self.paused_intervals += 1
                # Reserve a gap even if no inputs arrive while stopped. The next
                # admitted event bounds it; an unfinished pause is an unknown tail.
                self.capture_seq += 1
                self.last_capture_at = self.pause_started_at
        else:
            with self.lock:
                self.paused = False
                started = self.pause_started_at
                self.pause_started_at = None
            self.emit(
                "recording_control",
                dict(action="start", asset=self.asset, pause_started_at=started),
                time.time(),
            )

    def capture_input(self, row, payload):
        with self.lock:
            if self.paused:
                self.suppressed_records += 1
                return False
        if self.capture_mode == "sampled" and payload.get("type") == "orderbook_delta":
            with self.lock:
                self.sampled_out["orderbook_delta"] = self.sampled_out.get("orderbook_delta", 0) + 1
            return True
        # Coverage counts book/trade messages and checks sequence continuity; it
        # does not inspect prices, quantities or depth. Reuse parsed identity
        # fields instead of parsing the full message again on the writer.
        # The archived row still contains the complete original payload.
        coverage_payload = None
        if payload.get("type") in ("orderbook_snapshot", "orderbook_delta", "trade") and isinstance(
            payload.get("msg"), dict
        ):
            coverage_payload = dict(
                type=payload["type"],
                sid=payload.get("sid"),
                seq=payload.get("seq"),
                msg=dict(market_ticker=payload["msg"].get("market_ticker")),
            )
        return self.emit(
            "input",
            row,
            row["received"],
            source_time=input_source_time(payload),
            monotonic_ns=row.get("monotonic_ns"),
            caused_by=row["id"],
            coverage_payload=coverage_payload,
        )

    def capture_execution(self, event, *, delivery):
        return self.emit(
            "execution_event",
            dict(event, delivery=delivery),
            event.get("received_at"),
            source_time=event.get("source_time"),
            processed_at=event["observed_at"],
            caused_by=event.get("decision_id"),
        )

    def _execution_records(self):
        if self.order_db and Path(self.order_db).is_file():
            from .execution_journal import read_events

            with closing(sqlite3.connect(f"file:{self.order_db}?mode=ro", uri=True, timeout=0)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_events'").fetchone():
                    return
                events = list(read_events(db, self.execution_cursor, "KX" + self.asset + "15M-", limit=500))
            for event in events:
                if not self.capture_execution(event, delivery="journal_backfill"):
                    break
                self.execution_cursor = event["journal_seq"]

    def capture(self, engine, row, payload):
        """Optional sampled summaries. Authoritative inputs/checks are captured elsewhere."""
        if self.paused:
            return
        try:
            now = row["received"]
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
                            **(
                                dict(
                                    capture_mode="sampled",
                                    yes_levels=[[str(p), str(q)] for p, q in book.yes.items()],
                                    no_levels=[[str(p), str(q)] for p, q in book.no.items()],
                                )
                                if self.capture_mode == "sampled"
                                else {}
                            ),
                            **book.summary(),
                        ),
                        now,
                        source_time=book.source_time,
                        caused_by=row["id"],
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
                        caused_by=evaluation.get("decision_id", row["id"]),
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
        """Read legacy journals without holding SQLite locks during serialization."""
        if self.settlement_db and Path(self.settlement_db).exists():
            with closing(sqlite3.connect(f"file:{self.settlement_db}?mode=ro", uri=True, timeout=0)) as db:
                rows = db.execute(
                    "select rowid,market,timestamp,kind,body from records where rowid>? "
                    "and kind in ('settlement','settlement_evidence') order by rowid limit 200",
                    (self.settlement_cursor,),
                ).fetchall()
            for rowid, market, timestamp, kind, body in rows:
                if not self.emit(
                    "settlement_journal",
                    dict(
                        market=market,
                        kind=kind,
                        journal_rowid=rowid,
                        journal_timestamp=timestamp,
                        body=json.loads(body),
                    ),
                ):
                    return
                self.settlement_cursor = rowid
        if self.order_db and Path(self.order_db).exists():
            with closing(sqlite3.connect(f"file:{self.order_db}?mode=ro", uri=True, timeout=0)) as db:
                # The durable event stream/checkpoint already includes order state.
                # Do not scan/serialize all order snapshots as well every 30 seconds.
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_events'").fetchone():
                    return
                cutoff = time.time()
                rows = db.execute(
                    "select body from manual_orders where json_extract(body,'$.updated_at')>=? "
                    "and json_extract(body,'$.updated_at')<? "
                    "and json_extract(body,'$.request.ticker') like ?",
                    (self.order_cursor, cutoff, "KX" + self.asset + "15M-%"),
                ).fetchall()
            for (raw,) in rows:
                body = json.loads(raw)
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
                if not self.emit("order_journal", event):
                    return
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
                    # Preserve retired segments and their reconstruction context.
                    # A same-filesystem rename is cheap and cannot expose a partial
                    # segment. Failure leaves the original intact for the next pass.
                    self._archive_segment(p)
                    total -= size
            # Keep session context in the hot directory: a running session can
            # later update coverage or receive settlement evidence for old markets.
            # Open segments may temporarily exceed the cap until rotation; never delete live files.
        return shutil.disk_usage(self.root).free >= self.min_free_bytes

    def _archive_segment(self, path):
        from .research_archive import digest

        destination = self.archive_dir / path.parent.relative_to(self.root)
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        segment_target = destination / path.name
        if segment_target.exists() and digest(path) != digest(segment_target):
            raise ValueError("Archive segment conflict: " + path.name)
        # Validate immutable context before changing any archived file.
        for name in ("manifest.json", "source.json.gz"):
            source, target = path.parent / name, destination / name
            if not source.is_file():
                raise ValueError("Missing archive context: " + name)
            if target.exists() and digest(source) != digest(target):
                raise ValueError("Archive context conflict: " + name)
        for name in ("manifest.json", "source.json.gz", "coverage.json", "status.json"):
            source, target = path.parent / name, destination / name
            if name in ("manifest.json", "source.json.gz") and target.exists():
                continue
            if source.is_file():
                if name in ("coverage.json", "status.json"):
                    try:
                        body = json.loads(source.read_text())
                    except ValueError:
                        continue  # Older writers may be updating advisory summaries.
                    atomic_json(target, body)
                    continue
                temporary = target.with_suffix(target.suffix + ".tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
        if segment_target.exists():
            path.unlink()  # A verified identical archived copy already exists.
        else:
            path.rename(segment_target)

    def _writer(self):
        stream = part = inflight = None
        coverage = None
        allowed = failed = drained = False
        last_capture_at = None
        opened = 0

        def write(data):
            nonlocal stream, part, opened
            if stream is None:
                part = self.directory / f"events-{time.time_ns()}.jsonl.gz.part"
                stream = gzip.open(part, "wb", compresslevel=1)
                opened = time.monotonic()
            # No parse/re-encode of queued payloads on the writer.
            stream.write(data[:-1] + b',"recorded_at":' + str(time.time()).encode() + b"}\n")

        def gap(last, next_capture_at):
            if last <= self.last_written_seq:
                return
            first = self.last_written_seq + 1
            event = dict(
                schema_version=self.metadata["schema_version"],
                session=self.session,
                capture_seq=first,
                kind="recording_gap",
                source_time=None,
                received_at=None,
                processed_at=None,
                captured_at=time.time(),
                capture_monotonic_ns=time.monotonic_ns(),
                received_monotonic_ns=None,
                caused_by=None,
                body=dict(
                    first_missing_seq=first,
                    last_missing_seq=last,
                    dropped_records=last - first + 1,
                    previous_capture_at=last_capture_at,
                    next_capture_at=next_capture_at,
                    affected_scope="capture_interval",
                    reason="capture_loss",
                ),
            )
            write(dumps(event).encode())
            if coverage is not None:
                coverage.observe(event)
            self.last_written_seq = last

        try:
            self.directory.mkdir(parents=True, mode=0o700)
            coverage = Coverage(
                self.root,
                self.asset,
                self.session,
                self.metadata["started_at"],
                capture_mode=self.capture_mode,
            )
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
            maintenance = flushed = audit_at = control_at = 0
            while not self.stopping.is_set() or not self.queue.empty():
                now = time.monotonic()
                if self.close_deadline is not None and now >= self.close_deadline:
                    self.error = "SHUTDOWN_TIMEOUT"
                    break
                if not self.stopping.is_set() and now - control_at >= 1:
                    self._poll("logging_control", self._sync_control)
                    control_at = now
                if not self.stopping.is_set() and not self.paused and now - audit_at >= 30:
                    self._poll("legacy_journal_read", self._audit_records)
                    audit_at = now
                if now - maintenance >= 5:
                    if not self.stopping.is_set() and not self.paused:
                        self._poll("execution_journal_read", self._execution_records)
                    try:
                        allowed = self._retention()
                    except Exception as exc:
                        self.diagnostic("retention_archive", exc)
                        allowed = shutil.disk_usage(self.root).free >= self.min_free_bytes
                    else:
                        with self.lock:
                            self.diagnostics.pop("retention_archive", None)
                    maintenance = now
                    if not allowed:
                        self.error = "LOW_DISK"
                    elif self.error == "LOW_DISK":
                        self.error = None
                    atomic_json(self.directory / "status.json", self.status())
                    self._poll("coverage_maintenance", lambda: coverage.prune_reference(time.time()))
                    self._poll(
                        "coverage_summary",
                        lambda: atomic_json(
                            self.directory / "coverage.json", coverage.summary(self.status())
                        ),
                    )
                if stream and (now - opened >= self.rotation_seconds or (self.paused and self.queue.empty())):
                    stream.close()
                    part.rename(part.with_suffix(""))
                    stream = None
                try:
                    inflight = self.queue.get(timeout=0.25)
                except queue.Empty:
                    if stream and now - flushed >= 1:
                        stream.flush()
                        flushed = now
                    continue
                seq, captured_at, data, kind, coverage_record = inflight
                with self.lock:
                    self.queued_bytes -= len(data)
                if not allowed:
                    with self.lock:
                        self._drop("low_disk")
                    inflight = None
                    continue
                gap(seq - 1, captured_at)
                write(data)
                try:
                    # Most records (notably input_processed and execution audits)
                    # have no coverage effect. Keep them verbatim in the archive
                    # without decoding them a second time on this CPU-bound path.
                    if coverage_record is not None:
                        coverage.observe(coverage_record)
                    elif kind in (
                        "input",
                        "book",
                        "reference_sample",
                        "settlement_observation",
                        "model_calculation",
                        "decision_check",
                    ):
                        coverage.observe(json.loads(data))
                except Exception as exc:
                    # The raw record was already written successfully. A coverage
                    # index failure requires a rebuild, not an invented feed gap.
                    self.diagnostic("coverage_rebuild_required", exc)
                self.last_written_seq = seq
                last_capture_at = captured_at
                self.written += 1
                inflight = None
                if now - flushed >= 1:
                    stream.flush()
                    flushed = now
            drained = self.queue.empty()
        except Exception as exc:
            failed = True
            self.error = type(exc).__name__
            LOG.warning("Research writer stopped (%s); trading continues", type(exc).__name__)
        finally:
            with self.lock:
                self.stopping.set()
                if inflight is not None:
                    self._drop("writer_failure")
                while not self.queue.empty():
                    _, _, data, _, _ = self.queue.get_nowait()
                    self.queued_bytes -= len(data)
                    self._drop("writer_failure" if failed else "shutdown_timeout")
                last_seq = self.capture_seq
            try:
                # Tail drops must be visible even without a following input.
                if allowed and not failed:
                    gap(last_seq, None)
                if stream:
                    stream.close()
                    if not failed and drained:
                        part.rename(part.with_suffix(""))
                (self.directory / "status.json").write_text(
                    dumps(
                        dict(
                            self.status(),
                            closed_at=time.time(),
                            clean_shutdown=not failed and drained,
                            capture_complete=not failed
                            and drained
                            and self.dropped == 0
                            and self.paused_intervals == 0
                            and self.error is None,
                        )
                    )
                    + "\n"
                )
                if coverage is not None:
                    final_status = dict(
                        self.status(),
                        capture_complete=not failed
                        and drained
                        and self.dropped == 0
                        and self.error is None
                        and self.paused_intervals == 0,
                    )
                    atomic_json(self.directory / "coverage.json", coverage.summary(final_status, time.time()))
            except Exception as exc:
                self.error = type(exc).__name__
                LOG.warning("Unable to finalize research recording status")
            finally:
                if coverage is not None:
                    coverage.close()

    def close(self):
        self.close_deadline = time.monotonic() + 2
        self.stopping.set()
        self.thread.join(timeout=2)


def start_research_log(config, run_id, *, settlement_db=None, producer="collector", order_db=None):
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
            producer=producer,
            capture_mode=os.environ.get("BTC15_RESEARCH_LOG_MODE", "full"),
            archive_dir=os.environ.get("BTC15_RESEARCH_ARCHIVE_DIR"),
            settlement_db=settlement_db,
            order_db=order_db
            or (Path(settlement_db).parent.parent / "manual-orders.sqlite" if settlement_db else None),
        )
    except Exception:
        LOG.exception("Research recorder unavailable; trading continues")
        return None
