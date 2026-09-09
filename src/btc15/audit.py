"""Dataset integrity evidence, separate from economic results."""

import hashlib
import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .domain import timestamp
from .storage import read_events


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def unique_events(paths, stats=None):
    """Deduplicate journal/Parquet mirrors without keeping all IDs in memory."""
    with tempfile.TemporaryDirectory(prefix="btc15-events-") as directory:
        with sqlite3.connect(str(Path(directory) / "ids.db")) as db:
            db.execute("CREATE TABLE ids (id TEXT PRIMARY KEY, hash TEXT)")
            for path in paths:
                for row in read_events(path):
                    digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                    old = db.execute("SELECT hash FROM ids WHERE id=?", (row["id"],)).fetchone()
                    if old:
                        if old[0] != digest:
                            raise ValueError("Conflicting duplicate event ID")
                        if stats is not None:
                            stats["duplicates"] += 1
                        continue
                    db.execute("INSERT INTO ids VALUES (?,?)", (row["id"], digest))
                    yield row


def audit_files(paths):
    counts = Counter()
    sequences = {}
    gaps = []
    clocks = []
    previous = None
    starts = []
    ends = []
    markets = set()
    closes = {}
    settled = set()
    observations = defaultdict(list)
    connection_ids = set()
    synthetic = False
    provenances = set()
    source_ages = []
    stats = Counter()
    aggregate_checks = []
    reference_gaps = []
    previous_source = None
    for row in unique_events(paths, stats):
        now = row["received"]
        payload = row["payload"]
        kind = payload.get("type")
        msg = payload.get("msg", {})
        if not starts:
            starts.append(now)
        starts[0] = min(starts[0], now)
        ends[:] = [max(ends[0], now) if ends else now]
        if previous and now < previous["received"]:
            clocks.append({"id": row["id"], "seconds": now - previous["received"]})
        if (
            previous
            and now < previous["received"]
            and (
                row.get("connection_id") != previous.get("connection_id")
                or row.get("monotonic_ns", 0) <= previous.get("monotonic_ns", 0)
            )
        ):
            raise ValueError("Input recordings overlap or go backwards")
        previous = row
        counts[kind] += 1
        connection = row.get("connection_id", "")
        connection_ids.add(connection)
        sid, seq = payload.get("sid"), payload.get("seq")
        if sid is not None and seq is not None:
            key = (connection, sid)
            if key in sequences and seq != sequences[key] + 1:
                gaps.append(dict(id=row["id"], previous=sequences[key], current=seq))
            sequences[key] = seq
        if kind == "metadata":
            synthetic |= bool(msg.get("synthetic"))
            provenances.add("SYNTHETIC" if msg.get("synthetic") else "RECORDED")
            for m in msg["markets"]:
                if m.get("status") == "active":
                    markets.add(m["ticker"])
                    closes[m["ticker"]] = timestamp(m["close_time"])
        elif kind == "settlement":
            settled.add(msg["market_ticker"])
        elif kind == "market_lifecycle_v2" and msg.get("event_type") == "settled":
            settled.add(msg["market_ticker"])
        elif kind == "cfbenchmarks_value":
            tick = json.loads(msg["data"])
            t = float(tick["time"]) / 1000
            source_ages.append(now - t)
            if previous_source is not None and t - previous_source > 1.001:
                reference_gaps.append(dict(previous=previous_source, current=t, seconds=t - previous_source))
            previous_source = t
            close = (int(t) // 900 + 1) * 900 if int(t) % 900 else int(t)
            if close - 60 < t <= close:
                observations[close].append((t, float(tick["value"])))
                aggregate = msg.get("last_60s_windowed_average_15min")
                if aggregate and aggregate.get("window_size") == 60:
                    samples = dict(observations[close])
                    if len(samples) == 60:
                        mean = sum(samples.values()) / 60
                        aggregate_checks.append(
                            dict(
                                close=close,
                                observed=mean,
                                official=float(aggregate["value"]),
                                matches=abs(mean - float(aggregate["value"])) < 0.000001,
                            )
                        )
    import numpy as np

    settled &= markets  # The lifecycle channel includes unrelated exchange markets.
    complete_markets = [
        m for m in sorted(settled) if len({int(t) for t, _ in observations.get(closes[m], [])}) == 60
    ]
    complete = sum(len({int(t) for t, p in rows}) == 60 for rows in observations.values())
    return dict(
        events=sum(counts.values()),
        counts=dict(counts),
        duplicate_events=stats["duplicates"],
        first_received=min(starts) if starts else None,
        last_received=max(ends) if ends else None,
        sequence_gaps=gaps,
        clock_adjustments=clocks,
        connections=len(connection_ids),
        active_markets=sorted(markets),
        settled_markets=sorted(settled),
        complete_settlement_windows=complete,
        complete_settled_markets=complete_markets,
        synthetic=synthetic,
        provenance=next(iter(provenances)) if len(provenances) == 1 else "MIXED_OR_UNKNOWN",
        reference_age_p50=float(np.median(source_ages)) if source_ages else None,
        reference_age_p99=float(np.quantile(source_ages, 0.99)) if source_ages else None,
        research_valid=bool(
            counts["cfbenchmarks_value"]
            and counts["orderbook_snapshot"]
            and not gaps
            and not clocks
            and not counts["error"]
            and not reference_gaps
            and len(provenances) == 1
            and complete > 0
            and bool(complete_markets)
            and all(a["matches"] for a in aggregate_checks)
        ),
        settlement_aggregate_checks=aggregate_checks,
        reference_gaps=reference_gaps,
        economic_validation=False,
    )
