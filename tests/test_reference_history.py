import asyncio
import json
from dataclasses import asdict, replace

import pytest
from test_collection import fake_client, fake_socket
from test_sustained_lead import reference, setup_engine

from btc15 import runner
from btc15.config import Settings
from btc15.engine import Engine
from btc15.reference_history import CACHE_NAME, history_body, load_history, save_history, validate_history
from btc15.storage import CompactRecorder, read_events
from btc15.strategies.settlement_edge.model import Tick, features


def ticks_at(now, price=60000, count=2400):
    return [Tick(t, t, price) for t in range(int(now) - count, int(now))]


def test_cache_roundtrip_original_receipts_and_rolling_hour(tmp_path, config):
    now = 1000000
    ticks = ticks_at(now, count=4000)
    save_history(tmp_path, ticks, now, config)
    body, report = load_history(tmp_path, [], now + 1, config)
    restored = validate_history(body, now + 1, config)
    assert report["source"] == "cache" and report["status"] == "LOADED"
    assert restored == [t for t in ticks if t.source >= now + 1 - 3600]
    assert restored[-1].received == now - 1
    assert validate_history(body, now + 3601, config) == []


@pytest.mark.parametrize(
    "mutation",
    ["future_source", "future_receipt", "nan", "stale_at_receipt", "duplicate", "reverse", "wrong_index"],
)
def test_invalid_cache_does_not_seed(tmp_path, config, mutation):
    now = 1000000
    body = history_body(ticks_at(now, count=3), now, config)
    if mutation == "future_source":
        body["samples"][-1]["source"] = now + 1
    elif mutation == "future_receipt":
        body["samples"][-1]["received"] = now + 1
    elif mutation == "nan":
        body["samples"][-1]["price"] = float("nan")
    elif mutation == "stale_at_receipt":
        body["samples"][-1]["source"] -= 30
    elif mutation == "duplicate":
        body["samples"].append(body["samples"][-1])
    elif mutation == "reverse":
        body["samples"].reverse()
    else:
        body["index"] = "ETHUSD_RTI"
    (tmp_path / CACHE_NAME).write_text(json.dumps(body))
    restored, report = load_history(tmp_path, [], now, config)
    assert not restored["samples"] and report["status"] == "COLD_START"
    assert report["errors"]


@pytest.mark.parametrize("bad", ["[]", "null", "{bad-json"])
def test_malformed_cache_falls_back_to_recordings(tmp_path, config, bad):
    now = 1000000
    source = tape(tmp_path, [recorded(Tick(now - 1, now - 1, 60000))])
    (tmp_path / CACHE_NAME).write_text(bad)
    body, report = load_history(tmp_path, [source], now, config)
    assert len(body["samples"]) == 1 and report["errors"]
    assert report["source"] == "recordings"


def recorded(tick, kind="cfbenchmarks_value"):
    return dict(
        id=str(tick.source),
        received=tick.received,
        monotonic_ns=int(tick.received * 1e9),
        payload=dict(
            type=kind,
            msg=dict(
                index_id="BRTI",
                data=json.dumps(dict(type="value", id="BRTI", time=tick.source * 1000, value=tick.price)),
            ),
        ),
    )


def tape(tmp_path, rows):
    recorder = CompactRecorder(tmp_path / "raw")
    recorder.append_rows(rows)
    recorder.close()
    return {"body": {"journal": str(recorder.path)}}


def test_recording_fallback_excludes_display_and_deduplicates(tmp_path, config):
    now = 1000000
    ticks = ticks_at(now, count=3)
    first = tape(tmp_path, [recorded(t) for t in ticks])
    second = tape(tmp_path, [recorded(ticks[-1]), recorded(Tick(now, now, 1e9), "cfbenchmarks_value_5hz")])
    body, report = load_history(tmp_path, [first, second], now, config)
    assert validate_history(body, now, config) == ticks
    assert report["source"] == "recordings"


def test_corrupt_recording_prefix_is_not_used(tmp_path, config):
    now = 1000000
    source = tape(tmp_path, [recorded(t) for t in ticks_at(now, count=3)])
    from pathlib import Path

    path = Path(source["body"]["journal"])
    path.write_bytes(path.read_bytes()[:-5])
    body, report = load_history(tmp_path, [source], now, config)
    assert not body["samples"] and report["errors"]


def test_conflicting_tapes_fail_closed(tmp_path, config):
    now = 1000000
    a = tape(tmp_path, [recorded(Tick(now - 1, now - 1, 60000))])
    b = tape(tmp_path, [recorded(Tick(now - 1, now - 1, 61000))])
    body, report = load_history(tmp_path, [a, b], now, config)
    assert not body["samples"] and report["status"] == "COLD_START"


def test_gaps_are_preserved_not_filled(config):
    now = 1000020
    c = replace(config)
    ticks = ticks_at(now)
    ticks = [t for t in ticks if not now - 100 <= t.source < now - 70]
    body = history_body(ticks, now, c)
    restored = validate_history(body, now, c)
    assert restored == ticks
    f = features(restored, now, c)
    assert f["max_gap"] == 31 and f["bleep"] is None


def test_preload_is_history_only_and_requires_fresh_confirmations(store, market, config, now):
    c = config
    e, _ = setup_engine(store, market, c, now)
    old = ticks_at(now, market.spec.strike + 300)
    e.ticks = []
    e.healthy = False
    row = dict(id="seed", received=now, payload=dict(type="reference_history", msg=history_body(old, now, c)))
    assert e.ingest(row)
    assert e.ticks == old and not e.healthy and not e._lead_history
    assert not e.executor.orders and e.executor.latest_reference_receipt is None
    assert not e.latest
    # Startup metadata/heartbeats may evaluate history but cannot confirm its lead.
    e.process(now, "startup-heartbeat", "heartbeat", {})
    assert not e._lead_history[market.ticker]
    for i in range(1, 5):
        reference(e, market, now + i, old[-1].price)
        assert not e.executor.orders
    reference(e, market, now + 5, old[-1].price)
    assert e.latest[market.ticker]["probability"]["model"] == "bleep-reference-atr-finish-v5"
    assert market.ticker in e.executor.orders
    with pytest.raises(ValueError, match="first event"):
        e.ingest(row)


def test_preload_event_replays_without_cache(store, tmp_path, config):
    now = 1000000
    c = replace(config)
    ticks = ticks_at(now)
    row = dict(
        id="seed", received=now, payload=dict(type="reference_history", msg=history_body(ticks, now, c))
    )
    source = tape(tmp_path, [row])
    engine = Engine(store, c, "BACKTEST")
    for event in read_events(source["body"]["journal"]):
        assert engine.ingest(event)
    assert engine.ticks == ticks
    assert features(engine.ticks, now, c)["bleep"] is not None
    assert not engine.executor.orders


def test_live_ticks_phase_out_preload(store, config):
    now = 1000000
    engine = Engine(store, config, "BACKTEST", execute=False)
    engine.ingest(
        dict(
            id="seed",
            received=now,
            payload=dict(type="reference_history", msg=history_body(ticks_at(now), now, config)),
        )
    )
    live = Tick(now + 3601, now + 3601, 61000)
    engine.ingest(recorded(live))
    assert engine.ticks == [live]


def test_collector_records_preload_and_saves_cache(store, config, tmp_path, raw, series, monkeypatch):
    import time

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    now = int(time.time())
    c = replace(config)
    ticks = ticks_at(now, raw["floor_strike"])
    save_history(tmp_path, ticks, now, c)
    run = asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), c, store, paper=True, duration=0.15))
    path = store.list(kind="raw_source", run_id=run)[0]["body"]["journal"]
    rows = list(read_events(path))
    assert rows[0]["payload"]["type"] == "reference_history"
    assert rows[0]["payload"]["msg"]["samples"] == [asdict(t) for t in ticks]
    assert store.list(kind="reference_preload", run_id=run)[0]["body"]["source"] == "cache"
    assert not store.list(kind="fill", run_id=run)
    assert (tmp_path / CACHE_NAME).is_file()
