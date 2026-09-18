
"""Public contract fixtures plus isolated paper/replay asset boundaries."""

import asyncio
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from bleep_helpers import inputs
from fastapi.testclient import TestClient

from btc15.api import KalshiClient, subscriptions
from btc15.assets import ASSETS
from btc15.bleep_seed import fetch_seed, validate_seed
from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.demo import generate
from btc15.domain import parse_market
from btc15.engine import Engine
from btc15.reference_history import history_body, validate_history
from btc15.storage import read_events
from btc15.strategies.settlement_edge.bleep import probability
from btc15.strategies.settlement_edge.model import Tick, lead_evidence


@pytest.fixture(params=["ETH", "SOL", "XRP"])
def contract(request):
    asset = request.param
    fixture = json.loads((Path(__file__).parent / f"fixtures/{asset.lower()}15-20260912.json").read_text())
    return ASSETS[asset], fixture["series"], fixture["markets"][0]


def test_public_contract_and_strict_asset_precision(contract):
    asset, series, raw = contract
    market = parse_market(raw, series)
    assert market.spec.index_name == asset.index
    assert market.spec.round_digits == asset.round_digits
    for field, value in [
        ("custom_strike", {"round_digits": "3"}),
        ("rules_primary", raw["rules_primary"].replace(asset.rule_index, "BRTI")),
        ("ticker", raw["ticker"].replace(asset.series, "KXBTC15M")),
    ]:
        with pytest.raises(ValueError):
            parse_market({**raw, field: value}, series)


def test_discovery_and_subscriptions_select_asset(contract):
    asset, series, raw = contract
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/series/" + asset.series):
            return httpx.Response(200, json={"series": series})
        assert request.url.params["series_ticker"] == asset.series
        assert request.url.params["exchange_index"] == "2"
        return httpx.Response(200, json={"markets": [raw] if request.url.params["status"] == "open" else []})

    async def run():
        client = KalshiClient(Settings(asset=asset.symbol))
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            base_url=Settings.rest_url + "/", transport=httpx.MockTransport(handler)
        )
        try:
            assert await client.discover() == (series, [raw])
        finally:
            await client.close()

    asyncio.run(run())
    assert len(requests) == 3
    subs = subscriptions([raw["ticker"]], asset.symbol)
    assert subs[0]["params"]["index_ids"] == subs[1]["params"]["index_ids"] == [asset.index]
    assert subs[2]["params"]["market_tickers"] == [raw["ticker"]]


def test_precision_used_by_settlement_model_and_lead(contract, config):
    asset, series, raw = contract
    config = replace(config, asset=asset.symbol)
    spec = parse_market(raw, series).spec
    unit = 10**-spec.round_digits
    spec = replace(spec, strike=1.23 + unit)
    now = spec.settlement_end - 100
    ticks = [Tick(now, now, 1.23 + 0.6 * unit)]
    p = probability(spec, ticks, now, inputs(ticks[-1].price), config)
    assert p["effective_boundary"] == pytest.approx(spec.strike - unit / 2)
    assert spec.yes(str(1.23 + 0.6 * unit))
    assert not spec.yes(str(1.23 + 0.4 * unit))
    evidence = lead_evidence(spec, ticks, now, {}, p, config)
    assert evidence["lead_sigma"] == pytest.approx(
        abs(p["settlement_mean"] - spec.strike) / max(unit, p["settlement_std"]))
    assert p["atr"] == max(ticks[-1].price * 0.00015, 1e-8)


def test_asset_hash_is_frozen_and_btc_compatible():
    c = Strategy()
    # Pin the historical built-in control, not an operator-edited active preset.
    assert c.version == Strategy().version
    assert replace(c, asset="BTC").version == c.version
    assert len({replace(c, asset=a).version for a in ASSETS}) == len(ASSETS)
    for invalid in ["DOGE", "eth", None, []]:
        with pytest.raises(ValueError):
            replace(c, asset=invalid)


def reference_row(index, now, value=1.5):
    return dict(
        id=str(now),
        received=now,
        connection_id="test",
        payload=dict(
            type="cfbenchmarks_value",
            msg=dict(
                index_id=index, data=json.dumps(dict(type="value", id=index, time=now * 1000, value=value))
            ),
        ),
    )


def test_reference_history_and_feed_cannot_cross_assets(contract, config, store):
    asset, series, raw = contract
    c = replace(config, asset=asset.symbol)
    now = parse_market(raw, series).close_time - 300
    body = history_body([Tick(now - 1, now - 1, 1.5)], now, c)
    assert body["index"] == asset.index
    assert validate_history(body, now, c)
    with pytest.raises(ValueError):
        validate_history(body, now, config)
    engine = Engine(store, c, execute=False)
    assert engine.ingest(reference_row(asset.index, now))
    before = list(engine.ticks)
    assert not engine.ingest(reference_row("BRTI", now + 1, 60000))
    assert engine.ticks == before
    assert not engine.healthy


def test_foreign_metadata_and_paper_database_rejected(contract, config, store, raw, series):
    asset, _, _ = contract
    c = replace(config, asset=asset.symbol)
    engine = Engine(store, c, execute=False)
    assert not engine.ingest(
        dict(id="metadata", received=1, payload=dict(type="metadata", msg=dict(series=series, markets=[raw])))
    )
    assert not engine.markets
    with pytest.raises(ValueError, match="separate paper database"):
        Engine(store, config)
    assert len(store.run_summaries("PAPER")) == 1


def test_exchange_seed_selects_asset_and_cannot_cross_runs(contract):
    asset, _, _ = contract
    now = 100000 * 60
    requested = []
    rows = [[m * 60, 1, 2, 1.4, 1.5, 100] for m in range(99900, 100000)]

    def handler(request):
        requested.append(request)
        assert request.url.path == f"/products/{asset.symbol}-USD/candles"
        return httpx.Response(200, json=rows)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_seed(client, now, asset.symbol)

    body = asyncio.run(run())
    assert len(requested) == 1 and body["asset"] == asset.symbol
    assert len(validate_seed(body, now, asset.symbol)) == 100
    with pytest.raises(ValueError, match="asset"):
        validate_seed(body, now)
    del body["asset"]
    with pytest.raises(ValueError, match="asset"):
        validate_seed(body, now, asset.symbol)


def test_paper_fill_resume_and_official_settlement(contract, config, store, tmp_path):
    asset, _, _ = contract
    c = replace(config, asset=asset.symbol)
    path = generate(tmp_path / "synthetic.jsonl", asset=asset.symbol)
    rows = list(read_events(path))
    engine = Engine(store, c, "PAPER", record_evaluations=False)
    for row in rows:
        assert engine.ingest(row)
        if engine.executor.positions:
            break
    else:
        pytest.fail("Synthetic asset scenario did not fill")
    before = copy.deepcopy(engine.executor.snapshot())
    with pytest.raises(ValueError):
        Engine(store, replace(c, asset="BTC"), run_id=engine.run_id, resume=True)
    resumed = Engine(store, c, "PAPER", run_id=engine.run_id, resume=True, record_evaluations=False)
    after = resumed.executor.snapshot()
    assert after["positions"] == before["positions"]
    assert after["risk"] == before["risk"]
    assert not any(order.active for order in resumed.executor.orders.values())
    ticker = next(iter(resumed.executor.positions))
    held = resumed.markets[ticker]
    metadata = rows[0]["payload"]["msg"]
    evidence = dict(
        source="kalshi_rest",
        series=metadata["series"],
        market={**held.raw, "status": "finalized", "result": "yes"},
    )
    assert resumed.executor.settle(held, "yes", held.close_time + 10, evidence=evidence)
    assert not resumed.executor.positions
    results = store.list(kind="trade_result", run_id=engine.run_id)
    assert len(results) == 1 and results[0]["market"].startswith(asset.series)
    client = TestClient(create_app(store, config=c, settings=Settings(data_dir=str(tmp_path))))
    health = client.get("/api/health").json()
    assert health["asset"] == asset.symbol and health["reference_digits"] == asset.round_digits
    assert client.get("/api/strategy").json()["name"] == f"{asset.symbol}15 Settlement Edge"
    assert client.put("/api/strategy", json=asdict(replace(c, asset="BTC"))).status_code == 422


def test_active_bleep_preset_replays_through_ioc_fill(contract, store, tmp_path):
    asset, _, _ = contract
    config = replace(Strategy.load(f"config/settlement-edge-{asset.symbol.lower()}-paper.json"))
    engine = Engine(store, config, "BACKTEST", record_evaluations=False)
    path = generate(tmp_path / "bleep.jsonl", asset=asset.symbol)
    for row in read_events(path):
        assert engine.ingest(row)
        if engine.executor.positions:
            break
    else:
        pytest.fail("Active Bleep preset did not fill the synthetic asset scenario")
    fills = store.list(kind="fill", run_id=engine.run_id)
    assert fills and fills[0]["body"]["price"] == 0.9
    opportunity = store.list(kind="opportunity", run_id=engine.run_id)[0]["body"]
    assert opportunity["probability"]["model"] == "bleep-reference-atr-finish-v5"
    assert opportunity["settlement_spec"]["index_name"] == asset.index


def test_collector_uses_selected_feed_fees_and_display(contract, config, store, tmp_path, monkeypatch):
    import time

    from btc15 import runner

    asset, series, raw = contract
    sent, reads = [], []
    now = time.time()
    payloads = [
        reference_row(asset.index, now)["payload"],
        dict(
            type="cfbenchmarks_value_5hz",
            msg=dict(index_id=asset.index, value_usd="1.2345", source_ts_ms=now * 1000),
        ),
    ]

    class Client:
        last_clock_skew = 0

        def __init__(self, settings):
            assert settings.asset == asset.symbol

        def headers(self, *args):
            return {}

        async def discover(self):
            return series, [raw]

        async def pages(self, *args):
            for item in []:
                yield item

        async def get(self, path, params=None):
            reads.append((path, params))
            if path.startswith("markets/"):
                return {"market": raw}
            return {"trading_active": True, "series_fee_change_arr": []}

        async def close(self):
            pass

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            sent.append(json.loads(message))

        async def recv(self):
            if payloads:
                return json.dumps(payloads.pop(0))
            await asyncio.Future()

    monkeypatch.setattr(runner, "KalshiClient", Client)
    monkeypatch.setattr(runner.websockets, "connect", lambda *a, **kw: Socket())
    run = asyncio.run(
        runner.collect(
            Settings(data_dir=str(tmp_path)), replace(config, asset=asset.symbol), store, duration=0.3
        )
    )
    assert [s["params"]["index_ids"] for s in sent if "index_ids" in s["params"]] == [[asset.index]] * 2
    assert ("series/fee_changes", {"series_ticker": asset.series, "show_historical": True}) in reads
    assert store.read_market_display("reference")["reference_5hz"]["value"] == "1.2345"
    assert {r["body"]["code"] for r in store.list(kind="health", run_id=run)} <= {"DISCONNECT"}
