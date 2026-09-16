import math
from dataclasses import replace

import pytest
from test_sustained_lead import reference, setup_engine

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import blend_probability, settlement_distribution
from btc15.strategies.settlement_edge.model import Tick


def inputs(spot):
    return dict(
        reference=spot,
        rv_30=0.00001,
        rv_60=0.00001,
        ewma=0.00001,
        bleep=dict(
            atr=10.0,
            stoch_k=50.0,
            stoch_k_previous=50.0,
            bb_upper=spot + 50,
            bb_lower=spot - 50,
            bb_middle=spot,
        ),
    )


def predict(spec, ticks, now, f=None):
    return blend_probability(
        dict(p_yes=0.5, p_no=0.5, uncertainty=0.02),
        spec,
        f or inputs(ticks[-1].price),
        now,
        settlement_aware=True,
        ticks=ticks,
        clamp=True,
    )["blend"]["bleep"]


@pytest.mark.parametrize("known", [0, 1, 30, 59, 60])
def test_discrete_average_covariance(market, known):
    spec = market.spec
    now = spec.settlement_start + known
    ticks = [
        Tick(spec.settlement_start + i, spec.settlement_start + i, spec.strike) for i in range(known + 1)
    ]
    d = settlement_distribution(spec, ticks, now, inputs(spec.strike), 10)
    remaining = 60 - known
    expected = sum(k * k for k in range(1, remaining + 1)) / 3600
    assert d["variance_time"] == pytest.approx(expected)
    assert d["settlement_std"] == pytest.approx(10 / math.sqrt(60) * math.sqrt(expected))
    assert d["known_samples"] == known
    assert d["settlement_mean"] == pytest.approx(spec.strike)


def test_pre_window_shared_uncertainty(market):
    spec = market.spec
    now = spec.settlement_start - 240
    d = settlement_distribution(spec, [Tick(now, now, spec.strike)], now, inputs(spec.strike), 10)
    assert d["variance_time"] == pytest.approx(240 + sum(k * k for k in range(1, 61)) / 3600)


def test_observed_average_changes_probability_with_same_spot(market):
    spec = market.spec
    now = spec.settlement_start + 30
    spot = spec.strike + 1

    def path(offset):
        return [
            Tick(spec.settlement_start + i, spec.settlement_start + i, spec.strike + offset)
            for i in range(30)
        ] + [Tick(now, now, spot)]

    low = predict(spec, path(-20), now)
    high = predict(spec, path(20), now)
    assert low["p_yes"] < 0.1 and high["p_yes"] > 0.9
    assert low["known_samples"] == high["known_samples"] == 30
    assert low["remaining_samples"] == 30


def test_volatility_spike_reduces_confidence(market):
    spec = market.spec
    now = spec.settlement_start - 120
    spot = spec.strike + 20
    ticks = [Tick(now, now, spot)]
    f = inputs(spot)
    calm = predict(spec, ticks, now, f)
    spike = predict(spec, ticks, now, {**f, "rv_30": 0.002})
    assert calm["volatility_source"] == "atr" and spike["volatility_source"] == "reference"
    assert spike["settlement_std"] > calm["settlement_std"]
    assert abs(spike["p_yes"] - 0.5) < abs(calm["p_yes"] - 0.5)


@pytest.mark.parametrize("operator", [">=", ">", "<", "<="])
def test_completed_average_uses_actual_rounded_contract_result(market, operator):
    spec = replace(market.spec, comparison_operator=operator, rounding="half_even")
    ticks = [Tick(spec.settlement_start + i, spec.settlement_start + i, spec.strike) for i in range(61)]
    p = predict(spec, ticks, spec.settlement_end)
    assert p["p_yes"] == float(spec.yes(spec.strike))
    assert p["p_no"] == 1 - p["p_yes"]
    assert p["indicator_weight"] == 0 and not p["safety_clamp_applied"]


def test_complements_and_rounding_boundary(market):
    spec = market.spec
    now = spec.settlement_start - 100
    ticks = [Tick(now, now, spec.strike)]
    ps = {op: predict(replace(spec, comparison_operator=op), ticks, now) for op in [">=", ">", "<", "<="]}
    assert ps[">="]["p_yes"] + ps["<"]["p_yes"] == pytest.approx(1)
    assert ps[">"]["p_yes"] + ps["<="]["p_yes"] == pytest.approx(1)
    assert ps[">="]["effective_boundary"] == pytest.approx(spec.strike - 0.005)
    assert ps[">"]["effective_boundary"] == pytest.approx(spec.strike + 0.005)


def test_causal_samples_and_missing_or_duplicate_slots(market):
    spec = market.spec
    now = spec.settlement_start + 30
    ticks = [Tick(spec.settlement_start + i, spec.settlement_start + i, spec.strike + 1) for i in range(31)]
    base = predict(spec, ticks, now)
    assert (
        predict(
            spec,
            ticks + [Tick(now + 1, now + 1, 1e9), Tick(now - 0.5, now + 1, 1e9)],
            now,
            inputs(ticks[-1].price),
        )
        == base
    )
    with pytest.raises(ValueError, match="missing observed"):
        predict(spec, ticks[:15] + ticks[16:], now)
    with pytest.raises(ValueError, match="duplicate sample"):
        predict(
            spec,
            ticks[:15]
            + [Tick(spec.settlement_start + 14.5, spec.settlement_start + 14.5, spec.strike)]
            + ticks[15:],
            now,
        )
    with pytest.raises(ValueError, match="BLEEP_VOLATILITY"):
        predict(spec, ticks, now, {**inputs(spec.strike + 1), "rv_30": float("nan")})


def test_historical_config_hash_unchanged():
    c = Strategy()
    assert c.version == "1766c001ffaa6835"
    legacy = replace(c, bleep_probability_blend_enabled=True)
    assert replace(legacy, bleep_settlement_model_enabled=True).version != legacy.version


@pytest.mark.parametrize("remaining", [300, 40])
def test_engine_records_new_model(store, market, config, remaining):
    c = replace(
        config,
        bleep_probability_blend_enabled=True,
        bleep_settlement_model_enabled=True,
        entry_probability_deductions=False,
        entry_window_start=420,
    )
    now = market.close_time - remaining
    e, _ = setup_engine(store, market, c, now, "yes")
    spot = market.spec.strike + 300
    e.ticks = [Tick(t, t, spot) for t in range(int(now) - 2100, int(now))]
    for i in range(5):
        reference(e, market, now + i, spot)
    d = e.latest[market.ticker]
    assert d["versions"]["probability"] == d["probability"]["model"] == "settlement-bleep-equal-v2"
    assert d["probability"]["blend"]["bleep"]["model"] == "bleep-settlement-reference-v2"
    assert d["probability"]["blend"]["bleep"]["known_samples"] == max(0, 60 - remaining + 4)
    assert market.ticker in e.executor.orders


@pytest.mark.parametrize("asset", ["active", "eth", "sol", "xrp"])
@pytest.mark.parametrize(
    "remaining,allowed",
    [(480, False), (420.01, False), (420, True), (419, True), (120, True), (15.01, True), (15, False)],
)
def test_deployed_entry_window(market, asset, remaining, allowed):
    from test_strategy_reverification import make_book

    from btc15.strategies.settlement_edge.rules import evaluate

    c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
    assert c.entry_window_start == 420 and c.bleep_settlement_model_enabled
    assert c.min_probability == c.late_min_probability == 0
    assert c.standard_component_min_probability == c.late_component_min_probability == 0.78
    now = market.close_time - remaining
    p = dict(
        p_yes=0.7,
        p_no=0.3,
        blend=dict(project15_p_yes=0.55, bleep=dict(p_yes=0.85)),
        lead=dict(side="yes", confirmed_normal=True, confirmed_late=True, confirmation_samples=1),
    )
    d = evaluate(
        market,
        make_book("yes", ".89", ".90", now),
        Tick(now, now, market.spec.strike + 1),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    assert ("ENTRY_WINDOW" in {r["code"] for r in d["reasons"]}) != allowed
