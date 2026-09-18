import asyncio
import copy
from dataclasses import replace

import httpx
import pytest
from test_sustained_lead import reference, setup_engine

from btc15.bleep_seed import fetch_seed, seeded_inputs, validate_seed
from btc15.strategies.settlement_edge.bleep import probability, safety_clamp
from btc15.strategies.settlement_edge.model import Tick, features


def seed(now):
    minute = int(now // 60)
    return dict(
        version=1,
        provider="coinbase",
        candles=[[m, 60000, 60010, 59990, 60000 + m % 3] for m in range(minute - 100, minute)],
    )


@pytest.mark.parametrize(
    "p,safety,expected",
    [
        (0.95, 0.49, 0.75),
        (0.05, 0.49, 0.25),
        (0.95, 0.5, 0.95),
        (0.05, 0.5, 0.05),
        (0.6, 0.1, 0.6),
        (0.4, 0.1, 0.4),
    ],
)
def test_clamp_both_sides_and_threshold(p, safety, expected):
    assert safety_clamp(p, safety) == expected


@pytest.mark.parametrize("operator", [">=", "<="])
def test_clamp_before_complement(monkeypatch, market, config, now, operator):
    # Force a high favored probability near strike to exercise the normally redundant cap.
    monkeypatch.setattr("btc15.strategies.settlement_edge.bleep.safety_clamp", lambda p, s, limit=0.75: 0.75)
    spec = replace(market.spec, strike=60000, comparison_operator=operator)
    ticks = [Tick(now - i, now - i, 60001) for i in range(400, -1, -1)]
    f = features(ticks, now, config)
    f["bleep"] = seeded_inputs(validate_seed(seed(now), now), int(now // 60) - 1, ticks, now)
    b = probability(spec, ticks, now, f, config)
    expected = 0.75 if operator == ">=" else 0.25
    assert b["p_yes"] == expected
    assert b["safety_clamp_enabled"] and b["safety_clamp_applied"]



@pytest.mark.parametrize("damage", ["future", "gap", "nan", "ohlc", "stale", "duplicate"])
def test_invalid_seed_rejected(now, damage):
    body = seed(now)
    if damage == "future":
        body["candles"][-1][0] += 1
    elif damage == "gap":
        del body["candles"][10]
    elif damage == "nan":
        body["candles"][0][1] = float("nan")
    elif damage == "ohlc":
        body["candles"][0][2] = 1
    elif damage == "stale":
        for c in body["candles"]:
            c[0] -= 10
    else:
        body["candles"][1] = body["candles"][0]
    with pytest.raises(ValueError):
        validate_seed(body, now)


def test_seed_rolls_out_without_affecting_reference(now):
    body = seed(now)
    candles = validate_seed(body, now)
    last = max(candles)
    ticks = [Tick(now, now, 60100)]
    assert seeded_inputs(candles, last, ticks, now)["candles"] == 101
    assert len(ticks) == 1
    for n in range(1, 121):
        t = now + n * 60
        seeded_inputs(candles, last, [Tick(t, t, 60100 + n)], t)
    assert len(candles) == 120
    assert all(m > last for m in candles)


def test_seed_event_is_replayable_and_does_not_create_reference_health_or_orders(store, market, config):
    c = replace(
        config,
        bleep_exchange_seed_enabled=True,
        bleep_safety_clamp_enabled=True,
    )
    now = market.close_time - 300
    e, price = setup_engine(store, market, c, now)
    before = copy.deepcopy(e.ticks)
    healthy = e.healthy
    body = seed(now)
    assert e.ingest(dict(received=now, payload=dict(type="bleep_seed", msg=body)))
    assert e.ticks == before and e.healthy == healthy
    assert not e.executor.positions
    reference(e, market, now + 1, price)
    latest = e.latest[market.ticker]
    assert latest["features"]["bleep"]["candles"] >= 100
    assert latest["features"]["bleep_seed"]["provider"] == "coinbase"
    assert latest["probability"]["safety_clamp_enabled"]
    assert latest["probability"]["p_yes"] == latest["probability"]["conservative_yes"]



@pytest.mark.parametrize("provider", ["coinbase", "kraken", "binance", "none"])
def test_download_fallback_and_closed_candles(now, provider):
    body = seed(now)
    candles = body["candles"] + [[int(now // 60), 60000, 60010, 59990, 60000]]
    visited = []

    def respond(request):
        host = request.url.host
        visited.append(host)
        if provider not in host:
            return httpx.Response(503)
        if provider == "coinbase":
            rows = [[m * 60, low, high, op, close, 1] for m, op, high, low, close in reversed(candles)]
        elif provider == "kraken":
            rows = {
                "result": {
                    "XXBTZUSD": [
                        [m * 60, str(op), str(high), str(low), str(close)]
                        for m, op, high, low, close in candles
                    ],
                    "last": 1,
                }
            }
        else:
            rows = [
                [m * 60000, str(op), str(high), str(low), str(close)] for m, op, high, low, close in candles
            ]
        return httpx.Response(200, json=rows)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await fetch_seed(client, now)

    if provider == "none":
        with pytest.raises(ValueError):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result["provider"] == provider
        assert result["candles"] == [tuple(c) for c in body["candles"]]
    assert len(visited) == {"coinbase": 1, "kraken": 2, "binance": 3, "none": 3}[provider]


def test_collector_records_seed_for_offline_replay(store, config, tmp_path, raw, series, monkeypatch):
    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings
    from btc15.engine import Engine
    from btc15.storage import read_events

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])

    async def download(client, now, asset="BTC"):
        assert asset == "BTC"
        return seed(now)

    monkeypatch.setattr("btc15.bleep_seed.fetch_seed", download)
    c = replace(
        config,
        bleep_exchange_seed_enabled=True,
        bleep_safety_clamp_enabled=True,
    )
    run = asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), c, store, paper=True, duration=0.2))
    path = store.list(kind="raw_source", run_id=run)[0]["body"]["journal"]
    rows = list(read_events(path))
    seeds = [r for r in rows if r["payload"]["type"] == "bleep_seed"]
    assert len(seeds) == 1
    e = Engine(store, c, mode="BACKTEST")
    for row in rows:
        assert e.ingest(row)
    assert e.bleep_seed["provider"] == "coinbase"
    assert len(e.bleep_candles) == 100
    assert not e.ticks and not e.executor.positions
    assert store.list(kind="bleep_seed_status", run_id=run)[0]["body"]["status"] == "LOADED"
