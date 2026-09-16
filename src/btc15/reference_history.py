"""Bounded, causal official-reference preload. Never restores execution state."""

import json
import math
import os
from dataclasses import asdict
from pathlib import Path

from .storage import read_events
from .strategies.settlement_edge.model import Tick

HISTORY_SECONDS = 3600
MAX_SAMPLES = 7200
CACHE_NAME = "reference-history.json"


def validate_history(body, now, config):
    if (
        not math.isfinite(now)
        or not isinstance(body, dict)
        or body.get("version") != 1
        or body.get("index") != config.asset_spec.index
    ):
        raise ValueError("Unsupported reference history")
    samples = body.get("samples")
    if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
        raise ValueError("Invalid reference history size")
    ticks = []
    previous = None
    for sample in samples:
        tick = Tick(**sample)
        if tick.source > now or tick.received > now:
            raise ValueError("Future reference history")
        if not -config.max_clock_skew <= tick.received - tick.source <= config.reference_max_age:
            raise ValueError("Reference was stale or ahead of clock at receipt")
        if previous is not None and tick.source <= previous:
            raise ValueError("Duplicate or reversed reference history")
        previous = tick.source
        if min(tick.source, tick.received) >= now - HISTORY_SECONDS:
            ticks.append(tick)
    return ticks


def history_body(ticks, now, config):
    # Preserve original timestamps. Only causal, timely samples can seed history.
    samples = [
        asdict(t)
        for t in ticks
        if now - HISTORY_SECONDS <= t.source <= now
        and t.received <= now
        and -config.max_clock_skew <= t.received - t.source <= config.reference_max_age
    ]
    body = dict(version=1, index=config.asset_spec.index, samples=samples)
    body["samples"] = [asdict(t) for t in validate_history(body, now, config)]
    return body


def save_history(data_dir, ticks, now, config):
    path = Path(data_dir) / CACHE_NAME
    body = history_body(ticks, now, config)
    if not body["samples"]:
        return
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(body, stream, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def load_history(data_dir, recordings, now, config):
    """Cache first; indexed recent tapes only when no usable cache is available.

    Gaps remain gaps and are checked by the existing model. Corrupt files are
    rejected as a whole, rather than silently accepting a truncated prefix.
    """
    root = Path(data_dir)
    errors = []
    cache = root / CACHE_NAME
    try:
        if cache.stat().st_size > 1024 * 1024:
            raise ValueError("Reference cache exceeds 1 MiB")
        ticks = validate_history(json.loads(cache.read_text()), now, config)
        if ticks:
            return history_body(ticks, now, config), _report(ticks, now, "cache", [str(cache)], errors)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(dict(path=str(cache), reason=str(exc)))
    merged = {}
    paths = []
    raw_root = (root / "raw").resolve()
    for recording in recordings:
        path = Path(recording["body"]["journal"])
        try:
            if not path.resolve().is_relative_to(raw_root):
                raise ValueError("Recording outside this data directory")
            if path.stat().st_mtime < now - HISTORY_SECONDS:
                continue
            file_ticks = {}
            for row in read_events(path):
                payload = row["payload"]
                msg = payload.get("msg", {})
                if payload.get("type") == "metadata" and msg.get("synthetic"):
                    raise ValueError("Synthetic recording cannot seed official history")
                if payload.get("type") == "reference_history":
                    candidates = validate_history(msg, now, config)
                elif payload.get("type") == "cfbenchmarks_value" and msg.get("index_id") == config.asset_spec.index:
                    value = json.loads(msg["data"])
                    if value.get("id") != config.asset_spec.index or value.get("type") != "value":
                        raise ValueError("Not the configured official reference observation")
                    tick = Tick(float(value["time"]) / 1000, row["received"], float(value["value"]))
                    if tick.source < now - HISTORY_SECONDS:
                        continue
                    candidates = validate_history(
                        dict(version=1, index=config.asset_spec.index, samples=[asdict(tick)]), now, config
                    )
                else:
                    continue
                for tick in candidates:
                    existing = file_ticks.get(tick.source)
                    if existing is not None and existing.price != tick.price:
                        raise ValueError("Conflicting recorded reference samples")
                    file_ticks.setdefault(tick.source, tick)
            for source, tick in file_ticks.items():
                if source in merged and merged[source].price != tick.price:
                    # No trustworthy way to choose between conflicting tapes.
                    return history_body([], now, config), dict(
                        status="COLD_START",
                        samples=0,
                        errors=errors + [dict(path=str(path), reason="Conflicting tapes")],
                    )
            merged.update(file_ticks)
            paths.append(str(path))
        except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(dict(path=str(path), reason=str(exc)))
    ticks = [merged[source] for source in sorted(merged)]
    try:
        body = history_body(ticks, now, config)
    except ValueError as exc:
        errors.append(dict(reason=str(exc)))
        ticks = []
        body = history_body([], now, config)
    return body, _report(ticks, now, "recordings", paths, errors)


def _report(ticks, now, source, paths, errors):
    return dict(
        status="LOADED" if ticks else "COLD_START",
        source=source,
        paths=paths,
        samples=len(ticks),
        oldest_source=ticks[0].source if ticks else None,
        newest_source=ticks[-1].source if ticks else None,
        newest_age=now - ticks[-1].source if ticks else None,
        max_gap=max((b.source - a.source for a, b in zip(ticks, ticks[1:])), default=0),
        errors=errors,
    )
