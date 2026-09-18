import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest
from test_commodities import commodity  # noqa: F401

from btc15.assets import asset_spec
from btc15.bleep_seed import fetch_pyth_seed, validate_seed
from btc15.config import Settings, Strategy
from btc15.engine import Engine


def history(now):
    minutes = list(range(int(now // 60) - 100, int(now // 60) + 1))
    return dict(
        s="ok", t=[m * 60 for m in minutes], o=[100] * 101, h=[102] * 101, l=[99] * 101, c=[101] * 101
    )


@pytest.mark.parametrize("asset", ["GOLD", "SILVER", "WTI"])
def test_exact_pyth_index_and_closed_candles(now, asset):
    def respond(request):
        assert request.url.host == "pyth.dourolabs.app"
        assert request.url.params["symbol"] == asset_spec(asset).index
        assert request.url.params["resolution"] == "1"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=history(now))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await fetch_pyth_seed(client, now, asset, "test-secret")

    seed = asyncio.run(run())
    assert len(seed["candles"]) == 100
    assert max(validate_seed(seed, now, asset)) == int(now // 60) - 1
    with pytest.raises(ValueError):
        validate_seed(seed, now, "BTC")
    bad = copy.deepcopy(seed)
    bad["index"] = "Metal.XAU/USD"
    with pytest.raises(ValueError, match="index"):
        validate_seed(bad, now, asset)


@pytest.mark.parametrize(
    "damage", ["gap", "stale", "unaligned", "duplicate", "bad_ohlc", "unauthorized", "missing_key"]
)
def test_bad_history_fails_without_exposing_key(now, damage):
    data = history(now)
    if damage == "gap":
        data["t"][50] += 60
    if damage == "stale":
        data["t"] = [t - 600 for t in data["t"]]
    if damage == "unaligned":
        data["c"].pop()
    if damage == "duplicate":
        data["t"][51] = data["t"][50]
    if damage == "bad_ohlc":
        data["h"][50] = 1

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401 if damage == "unauthorized" else 200, json=data)
            )
        ) as client:
            return await fetch_pyth_seed(
                client, now, "GOLD", "" if damage == "missing_key" else "secret-value"
            )

    with pytest.raises(ValueError) as exc:
        asyncio.run(run())
    assert "secret-value" not in str(exc.value)


def test_pyth_seed_initializes_indicators_not_live_reference(commodity, store):  # noqa: F811
    asset, raw, series = commodity
    from btc15.bleep_seed import seeded_inputs
    from btc15.domain import parse_market

    market = parse_market(raw, series)
    now = market.close_time - 300
    data = history(now)
    candles = [
        [t / 60, op, high, low, close]
        for t, op, high, low, close in zip(data["t"][:-1], data["o"], data["h"], data["l"], data["c"])
    ]
    config = replace(
        Strategy.load(f"config/settlement-edge-{asset.lower()}-paper.json"), bleep_exchange_seed_enabled=True
    )
    e = Engine(store, config, execute=False)
    body = dict(version=1, asset=asset, index=asset_spec(asset).index, provider="pyth", candles=candles)
    e.ingest(dict(received=now, payload=dict(type="bleep_seed", msg=body)))
    assert e.bleep_seed["provider"] == "pyth"
    assert seeded_inputs(e.bleep_candles, e.bleep_seed["last_minute"], [], now)["candles"] == 100
    assert not e.ticks and not e._lead_history and not e.executor.orders
    assert e.last_received == -float("inf")


def test_secret_is_not_in_settings_repr():
    assert "test-secret" not in repr(Settings(pyth_pro_api_key="test-secret"))


def test_commodity_collector_records_pyth_seed(commodity, store, tmp_path, monkeypatch):  # noqa: F811
    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.storage import read_events

    asset, raw, series = commodity
    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])

    async def download(client, now, requested_asset, api_key):
        assert requested_asset == asset and api_key == "test-secret"
        data = history(now)
        return dict(
            version=1,
            asset=asset,
            index=asset_spec(asset).index,
            provider="pyth",
            candles=[[t / 60, 100, 102, 99, 101] for t in data["t"][:-1]],
        )

    monkeypatch.setattr("btc15.bleep_seed.fetch_pyth_seed", download)
    config = replace(
        Strategy.load(f"config/settlement-edge-{asset.lower()}-paper.json"), bleep_exchange_seed_enabled=True
    )
    run = asyncio.run(
        runner.collect(
            Settings(data_dir=str(tmp_path), pyth_pro_api_key="test-secret"),
            config,
            store,
            paper=True,
            duration=0.2,
        )
    )
    path = store.list(kind="raw_source", run_id=run)[0]["body"]["journal"]
    seeds = [r for r in read_events(path) if r["payload"]["type"] == "bleep_seed"]
    assert len(seeds) == 1 and seeds[0]["payload"]["msg"]["provider"] == "pyth"
    assert "test-secret" not in json.dumps(seeds)
