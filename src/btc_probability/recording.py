"""Append-only SQLite capture with hash lineage and exclusive writer ownership."""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schema import digest, encode


class Recording:
    def __init__(self, path, mode, config):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + ".lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Recording already has a writer") from None
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, received_time REAL NOT NULL,
                source_time REAL, processing_time REAL NOT NULL, data TEXT NOT NULL,
                previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS forecasts (
                tick_seq INTEGER NOT NULL REFERENCES events(seq), market TEXT NOT NULL,
                provider TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(tick_seq,market,provider));
        """)
        meta = dict(self.db.execute("SELECT key,value FROM metadata"))
        expected = dict(mode=mode, config=encode(config), schema="probability-recording-v1")
        if meta and any(meta.get(k) != v for k, v in expected.items()):
            self.close()
            raise ValueError("Recording mode/config mismatch; use a separate recording")
        for key, value in expected.items():
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
        self.db.commit()
        last = self.db.execute(
            "SELECT seq,received_time,hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self.seq, self.last_received, self.previous_hash = last or (0, float("-inf"), "")

    def append(self, kind, data, received, processed, source=None):
        received, processed = float(received), float(processed)
        source = None if source is None else float(source)
        if received < self.last_received:
            raise ValueError("RECEIPT_CLOCK_REGRESSION")
        event = dict(
            seq=self.seq + 1,
            kind=kind,
            received_time=received,
            source_time=source,
            processing_time=processed,
            data=data,
            previous_hash=self.previous_hash,
        )
        event["hash"] = digest(event)
        with self.db:
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                (
                    event["seq"],
                    kind,
                    received,
                    source,
                    processed,
                    encode(data),
                    self.previous_hash,
                    event["hash"],
                ),
            )
        self.seq, self.last_received, self.previous_hash = event["seq"], received, event["hash"]
        return event

    def forecasts(self, tick, forecasts):
        with self.db:
            for forecast in forecasts:
                self.db.execute(
                    "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?)",
                    (tick, forecast.market_ticker, forecast.provider, encode(forecast)),
                )

    def restore(self, engine):
        # Recompute interrupted forecast ticks with their original information and clocks.
        for event in read_events(self.path):
            self.forecasts(event["seq"], engine.apply(event))

    def close(self):
        self.db.close()
        self.lock.close()


@contextmanager
def read_connection(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield db
    finally:
        db.close()


def metadata(path):
    with read_connection(path) as db:
        return dict(db.execute("SELECT key,value FROM metadata"))


def read_events(path):
    previous, seq, received = "", 0, float("-inf")
    with read_connection(path) as db:
        for row in db.execute("SELECT * FROM events ORDER BY seq"):
            event = dict(
                zip(
                    (
                        "seq",
                        "kind",
                        "received_time",
                        "source_time",
                        "processing_time",
                        "data",
                        "previous_hash",
                        "hash",
                    ),
                    row,
                )
            )
            event["data"] = json.loads(event["data"])
            recorded_hash = event.pop("hash")
            if (
                event["seq"] != seq + 1
                or event["previous_hash"] != previous
                or digest(event) != recorded_hash
            ):
                raise ValueError("RECORDING_INTEGRITY_FAILURE")
            if event["received_time"] < received:
                raise ValueError("RECEIPT_CLOCK_REGRESSION")
            event["hash"] = recorded_hash
            previous, seq, received = recorded_hash, event["seq"], event["received_time"]
            yield event


def replay(path, config=None):
    from .engine import Engine
    from .schema import Config

    meta = metadata(path)
    engine = Engine(config or Config(**json.loads(meta["config"])), meta["mode"], baselines=config is None)
    for event in read_events(path):
        yield from engine.apply(event)
