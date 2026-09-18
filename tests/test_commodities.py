import copy
import json
from pathlib import Path

import pytest
from bleep_helpers import inputs

from btc15.api import subscriptions
from btc15.assets import asset_spec
from btc15.config import Strategy
from btc15.demo import generate
from btc15.domain import parse_market
from btc15.engine import Engine
from btc15.storage import read_events
from btc15.strategies.settlement_edge.bleep import probability, settlement_distribution
from btc15.strategies.settlement_edge.model import Tick


@pytest.fixture(params=["GOLD", "SILVER", "WTI"])
def commodity(request):
    fixture = json.loads(Path("tests/fixtures/commodities-20260917.json").read_text())[request.param]
    return request.param, fixture["market"], fixture["series"]


def test_verified_contract_and_feed(commodity):
    symbol, raw, series = commodity
    market = parse_market(raw, series)
    assert market.spec.reference_source == "Pyth"
    assert market.spec.sample_times == [market.close_time]
    assert market.spec.averaging_window_seconds == 0
    assert subscriptions([market.ticker], symbol)[0]["params"] == dict(
        channels=["pyth_value"], underlying_tickers=[asset_spec(symbol).index]
    )
    assert not any("cfbenchmarks" in str(s) for s in subscriptions([market.ticker], symbol))
    bad = copy.deepcopy(raw)
    bad["rules_primary"] = bad["rules_primary"].replace("close price", "average price")
    with pytest.raises(ValueError):
        parse_market(bad, series)
    with pytest.raises(ValueError):
        parse_market(raw, {**series, "settlement_sources": [{"name": "CF Benchmarks"}]})


def test_terminal_model_does_not_average_final_minute(commodity):
    symbol, raw, series = commodity
    spec = parse_market(raw, series).spec
    now = spec.settlement_end - 10
    ticks = [Tick(now - 20, now - 20, spec.strike * 0.9), Tick(now, now, spec.strike * 1.01)]
    result = probability(spec, ticks, now, inputs(ticks[-1].price), Strategy(asset=symbol))
    assert result["p_yes"] == 0.98
    assert result["known_samples"] == 0
    assert result["settlement_mean"] == pytest.approx(ticks[-1].price)
    features = dict(rv_30=0, rv_60=0, ewma=0)
    result = settlement_distribution(spec, ticks, now, features, 1)
    assert result["remaining_samples"] == 1
    assert result["variance_time"] == 10
    assert result["settlement_mean"] == ticks[-1].price
    with pytest.raises(ValueError, match="official commodity settlement"):
        probability(spec, ticks, spec.settlement_end, inputs(ticks[-1].price), Strategy(asset=symbol))


def test_reference_feed_isolated_by_asset(commodity, store):
    symbol, raw, series = commodity
    now = parse_market(raw, series).close_time - 100
    engine = Engine(store, Strategy(asset=symbol), execute=False)

    def row(index, at):
        return dict(
            id=str(at),
            received=at,
            connection_id="pyth-test",
            payload=dict(
                type="pyth_value",
                msg=dict(underlying_ticker=index, value_usd="100.5", source_ts_ms=at * 1000),
            ),
        )

    assert engine.ingest(row(asset_spec(symbol).index, now))
    assert engine.ticks[-1].price == 100.5
    assert not engine.ingest(row("Metal.XAU/USD", now + 1))
    assert len(engine.ticks) == 1


def test_commodity_strategy_reaches_paper_fill(commodity, store, tmp_path):
    symbol, raw, series = commodity
    market = parse_market(raw, series)
    config = Strategy.load(f"config/settlement-edge-{symbol.lower()}-paper.json")
    engine = Engine(store, config, "BACKTEST", record_evaluations=False)
    path = generate(tmp_path / "synthetic.jsonl", start=market.open_time)
    for row in read_events(path):
        payload = row["payload"]
        msg = payload["msg"]
        if payload["type"] == "metadata":
            msg.update(series=series, markets=[raw], fee_changes={raw["event_ticker"]: []})
        elif payload["type"] == "cfbenchmarks_value":
            tick = json.loads(msg["data"])
            payload.update(
                type="pyth_value",
                msg=dict(
                    underlying_ticker=asset_spec(symbol).index,
                    source_ts_ms=tick["time"],
                    value_usd=str(float(tick["value"]) / 78000 * market.spec.strike),
                ),
            )
        elif "market_ticker" in msg:
            msg["market_ticker"] = market.ticker
        assert engine.ingest(row)
        if engine.executor.positions:
            break
    else:
        pytest.fail("Commodity paper strategy never filled synthetic scenario")
    assert store.list("fill", engine.run_id)
    assert (
        store.list("opportunity", engine.run_id)[0]["body"]["settlement_spec"]["reference_source"] == "Pyth"
    )


@pytest.mark.parametrize(
    "remaining,allowed", [(480.01, False), (480, True), (120, True), (1.01, True), (1, False)]
)
def test_commodity_new_window_and_capped_threshold(commodity, remaining, allowed):
    from test_strategy_reverification import make_book

    from btc15.strategies.settlement_edge.rules import evaluate

    symbol, raw, series = commodity
    market = parse_market(raw, series)
    c = Strategy.load(f"config/settlement-edge-{symbol.lower()}-paper.json")
    assert c.min_probability == c.late_min_probability == 0.85
    assert c.entry_window_start == 480 and c.entry_cutoff == 1
    assert c.take_profit == 0.99 and c.fixed_stop_price == 0.55
    assert not c.bleep_exchange_seed_enabled
    now = market.close_time - remaining
    lead = dict(side="yes", confirmed_normal=True, confirmed_late=True, confirmation_samples=1)
    for confidence in [0.85, 0.8499, 0.83]:
        d = evaluate(
            market,
            make_book("yes", ".89", ".90", now),
            Tick(now, now, market.spec.strike * 1.01),
            dict(regime="NORMAL"),
            dict(p_yes=confidence, lead=lead),
            dict(score=100, reasons=[]),
            now,
            c,
        )
        assert (d["decision"] == "TRADE_CANDIDATE") == (allowed and confidence == 0.85)


def test_commodity_uses_btc_sigma_and_cap(commodity):
    import math

    from btc15.strategies.settlement_edge.bleep import capped_confidence

    symbol, raw, series = commodity
    spec = parse_market(raw, series).spec
    now = spec.settlement_end - 120
    spot = spec.strike * 1.001
    f = inputs(spot)
    f["reference_rolling_atr"] = spot * 0.002
    f["bleep"]["atr"] = spot * 10
    result = probability(spec, [Tick(now, now, spot)], now, f, Strategy(asset=symbol))
    assert result["model"] == "bleep-reference-atr-finish-v5"
    assert result["atr_source"] == "official_reference_rolling"
    assert result["sigma_t"] == pytest.approx(spot * 0.002 * math.sqrt(2) * 1.35)
    assert capped_confidence(0.98, 0.76, 0.78, symbol) == 0.83


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("available", [9, 10])
def test_commodity_buys_ten_or_none(commodity, store, side, available):
    from test_execution import ready
    from test_strategy_reverification import make_book

    from btc15.domain import D

    symbol, raw, series = commodity
    market = parse_market(raw, series)
    now = market.close_time - 300
    c = Strategy.load(f"config/settlement-edge-{symbol.lower()}-paper.json")
    ex = ready(store, market, now, c)
    book = make_book(side, ".89", ".90", now)
    d = dict(
        decision="TRADE_CANDIDATE",
        side=side,
        conservative_probability=0.95,
        entry_path="standard",
        lead=dict(side=side, confirmed_normal=True, confirmed_late=True),
    )
    order = ex.submit(market, book, d, "op", now, True)
    assert order is not None and order.quantity == 10
    setattr(book, "no" if side == "yes" else "yes", {D(".10"): D(available)})
    book.received = book.source_time = now + 1
    ex.aggressive(market, book, now + 1)
    assert (market.ticker in ex.positions) == (available == 10)
    if available == 10:
        assert ex.positions[market.ticker].quantity == 10
    else:
        assert not store.list(kind="fill")


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("reason,trigger", [("TAKE_PROFIT", ".99"), ("HARD_STOP", ".55")])
def test_commodity_exit_commits_remaining_held_contracts(commodity, store, side, reason, trigger):
    from test_exit_execution_v2 import held, quote, sells

    symbol, raw, series = commodity
    market = parse_market(raw, series)
    now = market.close_time - 300
    c = Strategy.load(f"config/settlement-edge-{symbol.lower()}-paper.json")
    ex = held(store, market, now, c, side)
    ex.monitor(market, quote(now, [(trigger, 10)], side), {}, now, "intent")
    assert ex.positions[market.ticker].exit_reason == reason
    ex.monitor(market, quote(now + 0.3, [(trigger, 4)], side), {}, now + 0.3, "partial")
    assert ex.positions[market.ticker].quantity == 6
    # Trigger clears; commitment remains, even during model warmup.
    ex.monitor(market, quote(now + 0.6, [(".90", 6)], side), {}, now + 0.6, "remaining")
    assert sum(f["quantity"] for f in sells(store)) == 10
    assert all(f["reason"] == reason for f in sells(store))
    assert market.ticker not in ex.positions
