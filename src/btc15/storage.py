"""Append-only research memory and durable raw event recording."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from .domain import dumps

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
    "ORDER_CANCELLED": {"POSITION_OPEN", "SETTLEMENT_PENDING"},
    "POSITION_OPEN": {"EXITING", "SETTLEMENT_PENDING"},
    "EXITING": {"POSITION_OPEN", "CLOSED", "SETTLEMENT_PENDING"},
    "SETTLEMENT_PENDING": {"CLOSED"},
    "CLOSED": set(),
    "ERROR": {"HALTED"},
    "HALTED": set(),
}


class Store:
    def __init__(self, url):
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def setup(dbapi, _):
                dbapi.execute("PRAGMA journal_mode=WAL")
                dbapi.execute("PRAGMA busy_timeout=5000")

        metadata.create_all(self.engine)
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
            with self.engine.begin() as c:
                c.execute(insert(records).values(**row))
        return rid

    def list(self, kind=None, run_id=None, mode=None, market=None, opportunity_id=None, limit=10000):
        q = select(records)
        for key, val in dict(
            kind=kind, run_id=run_id, mode=mode, market=market, opportunity_id=opportunity_id
        ).items():
            if val is not None:
                q = q.where(records.c[key] == val)
        with self.engine.connect() as c:
            rows = c.execute(q.order_by(records.c.timestamp, records.c.id).limit(limit)).mappings()
            return [{**r, "body": json.loads(r["body"])} for r in rows]

    def state(self, run_id, market):
        with self.engine.connect() as c:
            return c.execute(
                select(states.c.state).where(states.c.run_id == run_id, states.c.market == market)
            ).scalar()

    def transition(self, run_id, mode, market, target, now, opportunity_id=""):
        with self.engine.begin() as c:
            row = (
                c.execute(select(states).where(states.c.run_id == run_id, states.c.market == market))
                .mappings()
                .first()
            )
            old = row["state"] if row else None
            if old == target:
                return
            if target not in ({"DISCOVER_MARKET"} if old is None else TRANSITIONS[old] | {"ERROR", "HALTED"}):
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
            self.add(
                "transition",
                dict(previous=old, state=target),
                run_id,
                mode,
                now,
                market,
                opportunity_id,
                conn=c,
            )

    def claim(self, run_id, key):
        try:
            with self.engine.begin() as c:
                c.execute(insert(claims).values(run_id=run_id, key=key))
            return True
        except IntegrityError:
            return False

    def acquire(self, key, owner):
        try:
            with self.engine.begin() as c:
                c.execute(insert(leases).values(key=key, owner=owner))
        except IntegrityError as e:
            raise RuntimeError(
                "A writer owns this database. After a crash inspect the lease before manual recovery."
            ) from e

    def release(self, key, owner):
        with self.engine.begin() as c:
            c.execute(leases.delete().where(leases.c.key == key, leases.c.owner == owner))


RAW_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("received", pa.float64()),
        ("monotonic_ns", pa.int64()),
        ("connection_id", pa.string()),
        ("payload", pa.string()),
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
        self.journal.write(dumps(row) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())
        self.buffer.append(row)
        if len(self.buffer) >= self.chunk_size:
            self.flush()
        return row

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
        self.flush()
        self.journal.close()


def read_events(path):
    path = Path(path)
    # Choose journal OR parquet. Never silently double-count both copies.
    if path.suffix == ".parquet":
        rows = pq.read_table(path).to_pylist()
    else:
        rows = []
        with path.open() as f:
            for i, line in enumerate(f, 1):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"Corrupt journal line {i}; repair explicitly before replay") from e
    seen = set()
    previous = -float("inf")
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        if row["received"] < previous:
            raise ValueError("Nonmonotonic receive timestamps; replay cannot reorder history")
        previous = row["received"]
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
