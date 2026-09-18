"""Numeric audit of recorded observations; never a probability or settlement authority.

Run: python -m btc_probability.reconciliation CAPTURE.json [--as-of EPOCH]
Both source and receipt timestamps constrain replay. Late results do not leak backwards.
"""

import argparse
import json
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from .schema import decimal


def reconstruct(events, start, end, as_of):
    """Exactly [start,end) at 1 Hz. Duplicate timestamps cannot increase weight."""
    rows = {}
    conflicts = set()
    for event in events:
        r = event["data"]["normalized"]
        t = r["source_time"]
        if (
            event["received_time"] > as_of
            or r["received_time"] > as_of
            or t > as_of
            or r["index"] != "BRTI"
            or r["source"] != "kalshi-cfbenchmarks-1hz"
        ):
            continue
        if t in rows and rows[t] != decimal(r["value"]):
            conflicts.add(t)
        rows.setdefault(t, decimal(r["value"]))
    slots = tuple(range(int(start), int(end)))
    values = [rows[t] for t in slots if t in rows and t not in conflicts]
    missing = [t for t in slots if t not in rows or t in conflicts]
    mean = sum(values, Decimal(0)) / len(values) if values else None
    result = dict(
        start=start,
        end_exclusive=end,
        count=len(values),
        missing=missing,
        conflicts=sorted(conflicts.intersection(slots)),
        partial_mean=str(mean) if mean is not None else None,
        mean=None,
        rounded=None,
        status="INCOMPLETE",
    )
    if missing:
        return result
    result.update(mean=str(mean), status="COMPLETE")
    lower = mean.quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    distance = mean - lower
    if distance == Decimal(".005"):
        result.update(
            status="ROUNDING_TIE_UNSPECIFIED", adjacent_cents=[str(lower), str(lower + Decimal(".01"))]
        )
    else:
        result["rounded"] = str(lower if distance < Decimal(".005") else lower + Decimal(".01"))
    return result


def reconcile(capture, as_of):
    end = capture["observation_end"]
    events = capture["references"]
    correct = reconstruct(events, end - 60, end, as_of)
    helper = reconstruct(events, end - 59, end + 1, as_of)
    result = dict(
        market_ticker=capture["market_ticker"],
        as_of=as_of,
        settlement_window=correct,
        close_inclusive_helper=helper,
        provider={},
        published={"status": "NOT_YET_AVAILABLE"},
    )
    for event in events:
        r = event["data"]["normalized"]
        if r["source_time"] != end or max(event["received_time"], r["received_time"], end) > as_of:
            continue
        msg = (event["data"].get("raw") or {}).get("msg", {})
        for field, reconstructed in [("avg_60s_data", correct), ("last_60s_windowed_average_15min", helper)]:
            aggregate = msg.get(field)
            if not aggregate:
                continue
            valid = (
                aggregate.get("window_start_ts_ms") == (end - 60) * 1000
                and aggregate.get("window_end_ts_exclusive") == end * 1000
                and aggregate.get("window_size") == 60
            )
            mean = reconstructed["mean"]
            result["provider"][field] = dict(
                value=aggregate.get("value"),
                boundaries_valid=valid,
                matches_reconstruction=valid
                and mean is not None
                and abs(decimal(aggregate["value"]) - decimal(mean)) <= Decimal(".00000001"),
                event_seq=event["seq"],
            )
    official = capture.get("official_result_event")
    if official and official["received_time"] <= as_of:
        raw = official["data"]["raw"]
        value = raw.get("expiration_value")
        result["published"] = dict(
            value=value,
            result=raw.get("result"),
            lifecycle=raw.get("status"),
            finalized=raw.get("status") in ("finalized", "settled"),
            event_seq=official["seq"],
            received_time=official["received_time"],
            status="AVAILABLE",
            matches_settlement_value=value is not None
            and correct["rounded"] is not None
            and decimal(value) == decimal(correct["rounded"]),
            matches_close_inclusive_value=value is not None
            and helper["rounded"] is not None
            and decimal(value) == decimal(helper["rounded"]),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--as-of", type=float)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())
    cutoff = (
        args.as_of
        if args.as_of is not None
        else max(
            capture["observation_end"] + 1,
            *(e["received_time"] for e in capture["references"]),
            capture.get("official_result_event", {}).get("received_time", 0),
        )
    )
    print(json.dumps(reconcile(capture, cutoff), indent=2))


if __name__ == "__main__":
    main()
