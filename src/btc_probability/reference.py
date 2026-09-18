"""Causal, exact-slot settlement accumulation; no gap filling or tick duplication."""

from dataclasses import asdict
from decimal import Decimal

from .schema import decimal


class ReferenceHistory:
    def __init__(self):
        self.rows = []

    def add(self, reference):
        self.rows.append(reference)

    def causal(self, now, index="BRTI"):
        rows = [r for r in self.rows if r.received_time <= now and r.source_time <= now and r.index == index]
        # Keep first receipt for identical source timestamps. Conflicts are separately rejected.
        unique = {}
        for row in rows:
            unique.setdefault(row.source_time, row)
        return sorted(unique.values(), key=lambda r: r.source_time)

    def snapshot(self, spec, now):
        rows = [
            r
            for r in self.rows
            if r.received_time <= now and r.source_time <= now and r.index == spec.reference_index
        ]
        by_time, conflicts = {}, set()
        for row in rows:
            if row.source_time in by_time and decimal(by_time[row.source_time].value) != decimal(row.value):
                conflicts.add(row.source_time)
            by_time.setdefault(row.source_time, row)
        elapsed = [t for t in spec.sample_times if t <= now]
        known = {t: by_time[t].value for t in elapsed if t in by_time and t not in conflicts}
        latest = max(by_time.values(), key=lambda r: r.source_time, default=None)
        if latest and latest.source_time in conflicts:
            latest = None
        flags = ["CONFLICTING_REFERENCE"] if conflicts.intersection(elapsed) else []
        if len(known) < len(elapsed):
            flags.append("MISSING_PAST_SAMPLES")
        return latest, known, len(elapsed) - len(known), flags

    def crosscheck(
        self,
        spec,
        aggregate,
        now,
        *,
        field="last_60s_windowed_average_15min",
        source_time=None,
        received_time=None,
    ):
        """Crosscheck only a matching full trailing window; helper semantics differ."""
        if field != "avg_60s_data":
            return "DIFFERENT_WINDOW_SEMANTICS"
        if source_time is None or received_time is None or max(source_time, received_time) > now:
            return "NOT_YET_AVAILABLE"
        if (
            source_time != spec.observation_end_time
            or tuple(spec.sample_times) != tuple(source_time - 60 + i for i in range(60))
            or aggregate.get("window_start_ts_ms") != (source_time - 60) * 1000
            or aggregate.get("window_end_ts_exclusive") != source_time * 1000
        ):
            return "AGGREGATE_BOUNDARY_MISMATCH"
        _, known, missing, reasons = self.snapshot(spec, now)
        if missing or reasons or len(known) != 60:
            return "INCOMPLETE"
        if aggregate.get("window_size") != 60:
            return "AGGREGATE_COUNT_MISMATCH"
        average = sum(map(decimal, known.values()), Decimal(0)) / 60
        return (
            "MATCH"
            if abs(average - decimal(aggregate["value"])) <= Decimal("0.00000001")
            else "AGGREGATE_VALUE_MISMATCH"
        )

    def as_rows(self):
        return [asdict(r) for r in self.rows]
