"""Append-only research memory and durable raw event recording."""

import gzip
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import (
    JSON,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    bindparam,
    cast,
    create_engine,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from .domain import dumps
from .lease_identity import WriterOwnedError, current_process, owner_is_dead

metadata = MetaData()
records = Table(
    "records",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("mode", String, index=True),
    Column("kind", String, index=True),
    Column("market", String, index=True),
    Column("opportunity_id", String, index=True),
    Column("timestamp", Float, index=True),
    Column("body", Text, nullable=False),
)
history_indexes = (
    Index(
        "ix_records_kind_mode_run_time",
        records.c.kind,
        records.c.mode,
        records.c.run_id,
        records.c.timestamp,
        records.c.id,
    ),
    Index("ix_records_kind_mode_time", records.c.kind, records.c.mode, records.c.timestamp, records.c.id),
)
states = Table(
    "states",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("market", String, primary_key=True),
    Column("state", String),
    Column("version", Integer),
)
claims = Table(
    "claims", metadata, Column("run_id", String), Column("key", String), UniqueConstraint("run_id", "key")
)
leases = Table("leases", metadata, Column("key", String, primary_key=True), Column("owner", String))
market_display = Table(
    "market_display", metadata, Column("key", String, primary_key=True), Column("body", Text, nullable=False)
)

TRANSITIONS = {
    "DISCOVER_MARKET": {"VALIDATE_MARKET"},
    "VALIDATE_MARKET": {"WARMUP"},
    "WARMUP": {"MONITORING", "ENTRY_WINDOW", "SETTLEMENT_PENDING"},
    "MONITORING": {"ENTRY_WINDOW", "SETTLEMENT_PENDING"},
    "ENTRY_WINDOW": {"EVALUATING", "SETTLEMENT_PENDING"},
    "EVALUATING": {"NO_TRADE", "TRADE_CANDIDATE"},
    "NO_TRADE": {"EVALUATING", "SETTLEMENT_PENDING"},
    "TRADE_CANDIDATE": {"ORDER_PENDING", "NO_TRADE"},
    "ORDER_PENDING": {"ORDER_PARTIALLY_FILLED", "POSITION_OPEN", "ORDER_CANCELLED"},
    "ORDER_PARTIALLY_FILLED": {"POSITION_OPEN", "ORDER_CANCELLED"},
    "ORDER_CANCELLED": {"EVALUATING", "POSITION_OPEN", "SETTLEMENT_PENDING"},
    "POSITION_OPEN": {"EXITING", "SETTLEMENT_PENDING"},
    "EXITING": {"POSITION_OPEN", "CLOSED", "SETTLEMENT_PENDING"},
    "SETTLEMENT_PENDING": {"CLOSED"},
    "CLOSED": {"MONITORING"},
    "ERROR": {"HALTED"},
    "HALTED": set(),
}


checkpoints = Table(
    "paper_checkpoints",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("body", Text, nullable=False),
)


class Store:
    def run_summaries(self, mode, limit=None):
        def field(*path):
            if self.engine.dialect.name == "sqlite":
                return func.json_extract(records.c.body, "$." + ".".join(path))
            return func.json_extract_path_text(cast(records.c.body, JSON), *path)

        query = select(
            records.c.id,
            records.c.run_id,
            records.c.mode,
            records.c.timestamp,
            field("model").label("model"),
            field("versions", "config").label("config"),
        )
        query = (
            query.where(records.c.kind == "run", records.c.mode == mode)
            .order_by(records.c.timestamp.desc(), records.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as c:
            return [
                dict(
                    id=r.id,
                    run_id=r.run_id,
                    kind="run",
                    mode=r.mode,
                    timestamp=r.timestamp,
                    body=dict(
                        model=json.loads(r.model) if isinstance(r.model, str) else r.model,
                        versions=dict(config=r.config),
                    ),
                )
                for r in c.execute(query)
            ]

    def latest_evaluations(self, run_ids, mode):
        if not run_ids:
            return {}
        result = {}
        with self.engine.connect() as c:
            for (body,) in c.execute(
                select(market_display.c.body).where(
                    market_display.c.key.in_([f"evaluation:{r}" for r in run_ids])
                )
            ):
                row = json.loads(body)
                if row["mode"] == mode:
                    result[row["run_id"]] = row
            missing = set(run_ids) - result.keys()
            if missing:
                ranked = (
                    select(
                        records.c.id,
                        func.row_number()
                        .over(
                            partition_by=records.c.run_id,
                            order_by=(records.c.timestamp.desc(), records.c.id.desc()),
                        )
                        .label("rank"),
                    )
                    .where(
                        records.c.kind == "opportunity", records.c.mode == mode, records.c.run_id.in_(missing)
                    )
                    .subquery()
                )
                query = select(records).join(ranked, records.c.id == ranked.c.id).where(ranked.c.rank == 1)
                for row in c.execute(query).mappings():
                    result[row["run_id"]] = {**row, "body": json.loads(row["body"])}
        return result

    def trade_revision(self, mode):
        with self.engine.connect() as c:
            return tuple(
                c.execute(
                    select(records.c.kind, func.count())
                    .where(records.c.mode == mode, records.c.kind.in_(("fill", "trade_result")))
                    .group_by(records.c.kind)
                    .order_by(records.c.kind)
                ).all()
            )

    def publish_record(self, kind, body, run_id, mode, now, market="", opportunity_id=""):
        row = dict(
            id=opportunity_id or f"{kind}:{run_id}",
            kind=kind,
            body=body,
            run_id=run_id,
            mode=mode,
            timestamp=now,
            market=market,
            opportunity_id=opportunity_id,
        )
        self.publish_market_display(row, f"{kind}:{run_id}")

    def latest_evaluation(self, run_id, mode):
        live = self.read_market_display(f"evaluation:{run_id}") if run_id else None
        if live and live["mode"] == mode:
            return [live]
        return self.list(kind="opportunity", mode=mode, run_id=run_id, limit=1, newest_first=True)

    def publish_market_display(self, body, key="current"):
        # One replaceable UI projection, separate from immutable research records.
        with self.transaction() as c:
            result = c.execute(
                update(market_display).where(market_display.c.key == key).values(body=dumps(body))
            )
            if not result.rowcount:
                c.execute(insert(market_display).values(key=key, body=dumps(body)))

    def read_market_display(self, key="current"):
        with self.transaction() as c:
            body = c.execute(select(market_display.c.body).where(market_display.c.key == key)).scalar()
            return json.loads(body) if body else None

    def __init__(self, url):
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)
        self._state_query = select(states.c.state).where(
            states.c.run_id == bindparam("run"), states.c.market == bindparam("ticker")
        )
        self._connection = ContextVar("btc15_transaction", default=None)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def setup(dbapi, _):
                dbapi.execute("PRAGMA journal_mode=WAL")
                dbapi.execute("PRAGMA busy_timeout=5000")

        metadata.create_all(self.engine)
        for index in history_indexes:
            index.create(self.engine, checkfirst=True)
        # Enforce immutable history even outside the application.
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "sqlite":
                for action in ("UPDATE", "DELETE"):
                    conn.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS records_no_{action.lower()} "
                        f"BEFORE {action} ON records BEGIN SELECT RAISE(ABORT, 'immutable history'); END"
                    )
            elif self.engine.dialect.name == "postgresql":
                conn.exec_driver_sql(
                    "CREATE OR REPLACE FUNCTION btc15_immutable() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN RAISE EXCEPTION 'immutable history'; END; $$"
                )
                conn.exec_driver_sql("DROP TRIGGER IF EXISTS records_immutable ON records")
                conn.exec_driver_sql(
                    "CREATE TRIGGER records_immutable BEFORE UPDATE OR DELETE ON records "
                    "FOR EACH ROW EXECUTE FUNCTION btc15_immutable()"
                )

    @contextmanager
    def transaction(self):
        existing = self._connection.get()
        if existing is not None:
            yield existing
            return
        with self.engine.begin() as connection:
            token = self._connection.set(connection)
            try:
                yield connection
            finally:
                self._connection.reset(token)

    def checkpoint(self, run_id, body):
        with self.transaction() as c:
            result = c.execute(
                update(checkpoints).where(checkpoints.c.run_id == run_id).values(body=dumps(body))
            )
            if not result.rowcount:
                c.execute(insert(checkpoints).values(run_id=run_id, body=dumps(body)))

    def load_checkpoint(self, run_id):
        with self.transaction() as c:
            body = c.execute(select(checkpoints.c.body).where(checkpoints.c.run_id == run_id)).scalar()
            return json.loads(body) if body else None

    def add(self, kind, body, run_id, mode, now, market="", opportunity_id="", record_id=None, conn=None):
        rid = record_id or str(uuid.uuid4())
        row = dict(
            id=rid,
            kind=kind,
            body=dumps(body),
            run_id=run_id,
            mode=mode,
            timestamp=now,
            market=market,
            opportunity_id=opportunity_id,
        )
        if conn is not None:
            conn.execute(insert(records).values(**row))
        else:
            with self.transaction() as c:
                c.execute(insert(records).values(**row))
        return rid

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
        q = select(records)
        for key, val in dict(
            kind=kind, run_id=run_id, mode=mode, market=market, opportunity_id=opportunity_id
        ).items():
            if val is not None:
                q = q.where(records.c[key] == val)
        with self.transaction() as c:
            order = (
                (records.c.timestamp.desc(), records.c.id.desc())
                if newest_first
                else (records.c.timestamp, records.c.id)
            )
            rows = c.execute(q.order_by(*order).limit(limit)).mappings()
            result = [{**r, "body": json.loads(r["body"])} for r in rows]
            if kind == "status":
                projected = c.execute(
                    select(market_display.c.body).where(market_display.c.key.like("status:%"))
                )
                for (body,) in projected:
                    row = json.loads(body)
                    if all(
                        val is None or row[key] == val
                        for key, val in dict(
                            mode=mode, run_id=run_id, market=market, opportunity_id=opportunity_id
                        ).items()
                    ):
                        result.append(row)
                result.sort(key=lambda r: (r["timestamp"], r["id"]), reverse=newest_first)
                result = result[:limit] if limit is not None else result
            return result

    def has_market_integrity_failure(self, run_id, market, since):
        """Check retained evidence without loading an unbounded diagnostic history."""
        code = (
            func.json_extract(records.c.body, "$.code")
            if self.engine.dialect.name == "sqlite"
            else func.json_extract_path_text(cast(records.c.body, JSON), "code")
        )
        query = (
            select(records.c.id)
            .where(
                records.c.run_id == run_id,
                records.c.market == market,
                records.c.timestamp >= since,
                (records.c.kind == "invalid_market")
                | ((records.c.kind == "health") & (code == "RULES_CHANGED")),
            )
            .limit(1)
        )
        with self.transaction() as c:
            return c.execute(query).first() is not None

    def state(self, run_id, market):
        existing = self._connection.get()
        if existing is not None:
            return existing.execute(self._state_query, dict(run=run_id, ticker=market)).scalar()
        # Reads need no commit, but must still see an enclosing transaction's writes.
        with self.engine.connect() as c:
            return c.execute(self._state_query, dict(run=run_id, ticker=market)).scalar()

    def transition(
        self,
        run_id,
        mode,
        market,
        target,
        now,
        opportunity_id="",
        *,
        record_history=True,
        settlement_recovery_id=None,
    ):
        with self.transaction() as c:
            row = (
                c.execute(select(states).where(states.c.run_id == run_id, states.c.market == market))
                .mappings()
                .first()
            )
            old = row["state"] if row else None
            if old == target:
                return
            recovering = False
            if settlement_recovery_id is not None:
                recovery = c.execute(
                    select(records.c.body).where(
                        records.c.id == settlement_recovery_id,
                        records.c.kind == "settlement_recovery",
                        records.c.run_id == run_id,
                        records.c.mode == mode,
                        records.c.market == market,
                    )
                ).scalar()
                if old != "HALTED" or target != "SETTLEMENT_PENDING" or not recovery:
                    raise ValueError("Invalid settlement recovery transition")
                body = json.loads(recovery)
                proof = c.execute(
                    select(records.c.body).where(
                        records.c.id == body["evidence_id"],
                        records.c.kind == "settlement_evidence",
                        records.c.run_id == run_id,
                        records.c.mode == mode,
                        records.c.market == market,
                    )
                ).scalar()
                if not proof or json.loads(proof)["result"] != body["result"]:
                    raise ValueError("Settlement recovery evidence mismatch")
                recovering = True
            if not recovering and target not in (
                {"DISCOVER_MARKET"} if old is None else TRANSITIONS[old] | {"ERROR", "HALTED"}
            ):
                raise ValueError(f"Invalid transition {old} -> {target}")
            if row:
                result = c.execute(
                    update(states)
                    .where(
                        states.c.run_id == run_id,
                        states.c.market == market,
                        states.c.version == row["version"],
                    )
                    .values(state=target, version=row["version"] + 1)
                )
                if result.rowcount != 1:
                    raise RuntimeError("Concurrent state transition")
            else:
                c.execute(insert(states).values(run_id=run_id, market=market, state=target, version=0))
            if record_history:
                self.add(
                    "transition",
                    dict(
                        previous=old,
                        state=target,
                        **({"settlement_recovery_id": settlement_recovery_id} if recovering else {}),
                    ),
                    run_id,
                    mode,
                    now,
                    market,
                    opportunity_id,
                    conn=c,
                )

    def claim(self, run_id, key):
        # Conflict-safe insertion participates in the outer transaction on both databases.
        # SQLite's legacy transaction mode can commit a standalone SAVEPOINT prematurely.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        constructor = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        with self.transaction() as c:
            result = c.execute(
                constructor(claims)
                .values(run_id=run_id, key=key)
                .on_conflict_do_nothing(index_elements=["run_id", "key"])
                .returning(claims.c.key)
            )
            return result.scalar_one_or_none() is not None

    def acquire(self, key, owner):
        identity = current_process()
        try:
            with self.transaction() as c:
                previous = c.execute(select(leases.c.owner).where(leases.c.key == key)).scalar()
                if previous is not None:
                    saved = self.read_market_display("lease:" + key) or {}
                    if saved.get("owner") != previous or not owner_is_dead(saved, identity):
                        raise WriterOwnedError()
                    # Compare-and-delete and replacement share one transaction.
                    # A competing acquirer cannot replace a newer/live owner.
                    deleted = c.execute(
                        leases.delete().where(leases.c.key == key, leases.c.owner == previous)
                    )
                    if deleted.rowcount != 1:
                        raise WriterOwnedError()
                    self.add(
                        "writer_recovered",
                        dict(key=key, previous_owner=previous, process=saved),
                        owner,
                        "PAPER",
                        time.time(),
                    )
                c.execute(insert(leases).values(key=key, owner=owner))
                self.publish_market_display(dict(owner=owner, **(identity or {})), "lease:" + key)
        except IntegrityError as e:
            raise WriterOwnedError() from e

    def writer_owner(self):
        with self.transaction() as c:
            return c.execute(select(leases.c.owner).where(leases.c.key == "collector")).scalar()

    def release(self, key, owner):
        with self.transaction() as c:
            deleted = c.execute(leases.delete().where(leases.c.key == key, leases.c.owner == owner))
            if deleted.rowcount:
                c.execute(market_display.delete().where(market_display.c.key == "lease:" + key))


RAW_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("received", pa.float64()),
        ("monotonic_ns", pa.int64()),
        ("connection_id", pa.string()),
        ("payload", pa.string()),
        ("analysis_suspended", pa.bool_()),
        ("collector_entries_blocked", pa.bool_()),
    ]
)


class RawRecorder:
    def __init__(self, directory, chunk_size=500):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = str(uuid.uuid4())
        self.journal = (self.directory / f"{self.session}.jsonl").open("a", buffering=1)
        self.buffer = []
        self.chunk_size = chunk_size
        self.chunk = 0

    def append(self, payload, received, monotonic_ns, connection_id):
        row = dict(
            id=str(uuid.uuid4()),
            received=received,
            monotonic_ns=monotonic_ns,
            connection_id=connection_id,
            payload=dumps(payload),
        )
        self.append_rows([row])
        return row

    def append_rows(self, rows):
        self.journal.writelines(dumps(row) + "\n" for row in rows)
        self.journal.flush()
        os.fsync(self.journal.fileno())
        self.buffer.extend(rows)
        if len(self.buffer) >= self.chunk_size:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        target = self.directory / f"{self.session}-{self.chunk:06d}.parquet"
        temporary = target.with_suffix(".tmp")
        pq.write_table(pa.Table.from_pylist(self.buffer, schema=RAW_SCHEMA), temporary, compression="zstd")
        with temporary.open("rb") as f:
            os.fsync(f.fileno())
        temporary.replace(target)
        self.buffer.clear()
        self.chunk += 1

    def close(self):
        try:
            self.flush()
        finally:
            self.journal.close()


class CompactRecorder:
    """One compressed input tape, without derived evaluations or a Parquet mirror.

    Each fsynced batch is a complete gzip member so cleanly written batches remain
    readable after a crash. A torn final batch is detected, not silently ignored.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = str(uuid.uuid4())
        self.path = self.directory / f"{self.session}.jsonl.gz"
        self.journal = self.path.open("xb")

    def append_rows(self, rows):
        data = b"".join(
            (
                json.dumps(
                    {
                        **row,
                        "payload": json.loads(row["payload"])
                        if isinstance(row["payload"], str)
                        else row["payload"],
                    },
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            for row in rows
        )
        if data:
            self.journal.write(gzip.compress(data, compresslevel=1, mtime=0))
            self.flush()

    def flush(self):
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def close(self):
        try:
            self.flush()
        finally:
            self.journal.close()


def read_events(path):
    """Stream rows; physical order is authoritative, never timestamp-sort a tape."""
    path = Path(path)

    def source():
        if path.suffix == ".parquet":
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=4096):
                yield from batch.to_pylist()
        else:
            with gzip.open(path, "rt") if path.suffix == ".gz" else path.open() as f:
                for i, line in enumerate(f, 1):
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"Corrupt journal line {i}; repair explicitly before replay") from e

    previous = None
    for row in source():
        for flag in ("analysis_suspended", "collector_entries_blocked"):
            if row.get(flag) is None:
                row.pop(flag, None)
        if not math.isfinite(row["received"]):
            raise ValueError("Invalid receive timestamp")
        if previous and row["received"] < previous["received"]:
            if row.get("monotonic_ns", 0) <= previous.get("monotonic_ns", 0):
                raise ValueError("Receive and monotonic order moved backwards")
            # A real host wall-clock adjustment is retained and audited by the engine.
        previous = row
        payload = row["payload"]
        yield {**row, "payload": json.loads(payload) if isinstance(payload, str) else payload}


def export_packet(store, opportunity_id, directory):
    rows = store.list(opportunity_id=opportunity_id)
    if not rows:
        raise ValueError("Unknown opportunity")
    op = next(r for r in rows if r["kind"] == "opportunity")
    stamp = datetime.fromtimestamp(op["timestamp"], timezone.utc)
    target = Path(directory) / stamp.strftime("%Y/%m") / opportunity_id
    target.mkdir(parents=True, exist_ok=False)
    body = op["body"]
    outcomes = store.list(kind="settlement", run_id=op["run_id"], market=op["market"], limit=None)
    rows += outcomes
    run = store.list(kind="run", run_id=op["run_id"], limit=None)[0]
    (target / "software_snapshot.json").write_text(dumps(run["body"].get("source_snapshot", {})))
    groups = dict(
        summary={
            "opportunity": op,
            "settlement": outcomes,
            "trade_result": [r for r in rows if r["kind"] == "trade_result"],
        },
        features=body.get("features", {}),
        probability=body.get("probability", {}),
        decision=body,
        config=body["config"],
        model_versions=body["versions"],
        orders=[r for r in rows if r["kind"] == "order"],
        fills=[r for r in rows if r["kind"] == "fill"],
    )
    for name, data in groups.items():
        (target / f"{name}.json").write_text(dumps(data) + "\n")
    paths = store.list(kind="opportunity", run_id=op["run_id"], market=op["market"])
    for name, field in [("market_path", "book"), ("probability_path", "probability")]:
        pq.write_table(
            pa.Table.from_pylist(
                [dict(timestamp=r["timestamp"], data=dumps(r["body"].get(field))) for r in paths]
            ),
            target / f"{name}.parquet",
            compression="zstd",
        )
    (target / "timeline.json").write_text(dumps(rows))
    (target / "analysis.md").write_text(
        f"# {opportunity_id}\n\nMode: {op['mode']}\n\n"
        f"Decision: {body['decision']}\n\nReasons: {dumps(body['reasons'])}\n\n"
        "Probabilities are model estimates, not proof of edge. Original records are immutable.\n"
    )
    return target
