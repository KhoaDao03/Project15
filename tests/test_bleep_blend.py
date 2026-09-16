import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from test_exit_execution_v2 import held, quote
from test_sustained_lead import reference, setup_engine

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import blend_probability, indicator_inputs, mode_b_probability
from btc15.strategies.settlement_edge.model import Tick, features, probability

REFERENCE = json.loads((Path(__file__).parent / "fixtures/bleep-mode-b-reference.json").read_text())


@pytest.mark.parametrize("group", REFERENCE, ids=lambda g: g["name"])
def test_original_bleep_typescript_parity(market, group):
    inputs = indicator_inputs(group["candles"])
    spec = replace(market.spec, strike=60000)
    for case in group["cases"]:
        expected = case["expected"]
        for key in ("atr", "stoch_k", "stoch_k_previous", "bb_middle", "bb_upper", "bb_lower"):
            assert inputs[key] == pytest.approx(expected[key], abs=1e-10)
        result = mode_b_probability(spec, case["spot"], case["seconds_left"], inputs)
        assert result["p_yes"] == pytest.approx(expected["p_yes"], abs=1e-12)
        assert result["lean"] == pytest.approx(expected["lean"], abs=1e-12)
        assert result["safety_clamp_applied"] is False


def history(now, center=60000):
    return [Tick(t, t, center + 10 * math.sin(t / 117)) for t in range(int(now) - 2100, int(now) + 1)]


def test_full_history_causality_and_gaps(config):
    c = replace(config, bleep_probability_blend_enabled=True)
    now = 3600 * 100 + 30
    ticks = history(now)
    f = features(ticks, now, c)
    assert f["bleep"]["candles"] >= 33
    future = [Tick(now + 1, now + 1, 1e9), Tick(now - 0.5, now + 1, 1e9)]
    assert features(ticks + future, now, c) == f
    assert features(ticks[-300:], now, c)["bleep"] is None
    # A missing latest closed minute cannot reuse an older indicator sequence.
    gap = [t for t in ticks if not now // 60 * 60 - 60 <= t.source < now // 60 * 60]
    assert features(gap, now, c)["bleep"] is None
    assert "bleep" not in features(ticks, now, config)


@pytest.mark.parametrize("operator", [">=", ">", "<", "<="])
def test_equal_blend_complements_and_comparator(market, config, now, operator):
    c = replace(config, bleep_probability_blend_enabled=True)
    spec = replace(market.spec, strike=60000, comparison_operator=operator)
    ticks = history(now, 60100)
    f = features(ticks, now, c)
    original = probability(spec, ticks, now, f["sigma"], c)
    mixed = blend_probability(original, spec, f, now)
    bleep = mixed["blend"]["bleep"]
    assert mixed["p_yes"] == (original["p_yes"] + bleep["p_yes"]) / 2
    assert mixed["p_no"] == 1 - mixed["p_yes"]
    assert (bleep["p_yes"] > 0.5) == (operator in (">=", ">"))
    assert mixed["conservative_yes"] == max(0, mixed["p_yes"] - original["uncertainty"])
    assert mixed["conservative_no"] == max(0, mixed["p_no"] - original["uncertainty"])
    assert mixed["settlement_mean"] == original["settlement_mean"]
    assert mixed["model"] == "settlement-bleep-equal-v1"
    assert original["model"] == "settlement-mc-logwalk-v1"


def test_missing_bleep_never_silently_uses_mc(market, config, now):
    f = features(history(now)[-300:], now, replace(config, bleep_probability_blend_enabled=True))
    with pytest.raises(ValueError, match="BLEEP_WARMUP"):
        blend_probability({}, market.spec, f, now)


@pytest.mark.parametrize("bleep_only", [False, True])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("remaining", [300, 40])
def test_engine_uses_blend_for_entry_and_persists_components(
    store, market, config, side, remaining, bleep_only
):
    c = replace(
        config,
        bleep_probability_blend_enabled=not bleep_only,
        bleep_probability_only_enabled=bleep_only,
        entry_probability_deductions=False,
    )
    start = market.close_time - remaining
    e, _ = setup_engine(store, market, c, start, side)
    price = market.spec.strike + (300 if side == "yes" else -300)
    e.ticks = [Tick(t, t, price) for t in range(int(start) - 2100, int(start))]
    for i in range(5):
        reference(e, market, start + i, price)
    latest = e.latest[market.ticker]
    assert latest["versions"]["probability"] == (
        "bleep-mode-b-v1" if bleep_only else "settlement-bleep-equal-v1"
    )
    p = latest["probability"]
    assert latest["conservative_probability"] == p["p_" + side]
    assert p["p_yes"] == (
        p["blend"]["bleep"]["p_yes"]
        if bleep_only
        else (p["blend"]["project15_p_yes"] + p["blend"]["bleep"]["p_yes"]) / 2
    )
    assert p["lead"]["confirmed_late" if remaining == 40 else "confirmed_normal"]
    assert market.ticker in e.executor.orders
    e.process(start + 4.3, "fill", "heartbeat", {})
    assert market.ticker in e.executor.positions
    saved = store.list(kind="opportunity", run_id=e.run_id)[0]["body"]
    assert saved["probability"]["blend"] == p["blend"]


def test_engine_warmup_blocks_orders(store, market, config, now):
    c = replace(config, bleep_probability_blend_enabled=True)
    e, price = setup_engine(store, market, c, now)
    for i in range(6):
        reference(e, market, now + i, price)
    assert not e.executor.orders
    assert "BLEEP_WARMUP" in e.latest[market.ticker]["reasons"][0]["actual"]


def test_bleep_disagreement_rejects_otherwise_strong_mc_entry(store, market, config, now):
    c = replace(config, bleep_probability_blend_enabled=True, entry_probability_deductions=False)
    e, _ = setup_engine(store, market, c, now)
    price = market.spec.strike + 80
    # Old volatility remains in Wilder ATR after recent MC volatility has cooled.
    e.ticks = [
        Tick(t, t, price + (2000 * math.sin(t / 9) if t < now - 400 else 0))
        for t in range(int(now) - 2100, int(now))
    ]
    for i in range(5):
        reference(e, market, now + i, price)
    decision = e.latest[market.ticker]
    p = decision["probability"]
    assert p["blend"]["project15_p_yes"] > 0.95
    assert p["p_yes"] < e.config.min_probability
    assert not e.executor.orders
    assert "MIN_PROBABILITY" in [r["code"] for r in decision["reasons"]]


def test_adjusted_blend_controls_probability_exit(store, market, config, now):
    c = replace(
        config,
        bleep_probability_blend_enabled=True,
        exit_probability=0.60,
        hold_value_exit_enabled=False,
        standard_cashout_enabled=False,
    )
    ex = held(store, market, now, c)
    f = features(history(now, market.spec.strike - 100), now, c)
    original = dict(p_yes=0.90, p_no=0.10, uncertainty=0.03)
    mixed = blend_probability(original, market.spec, f, now)
    assert mixed["conservative_yes"] < 0.60
    ex.monitor(market, quote(now + 1, [(".80", 10)]), mixed, now + 1, "blend-exit")
    assert ex.positions[market.ticker].exit_reason == "INVALIDATION"


def test_config_identity_and_validation():
    default = Strategy()
    assert default.version == "1766c001ffaa6835"
    assert replace(default, bleep_probability_blend_enabled=True).version != default.version
    for bad in (1, "true", None):
        with pytest.raises(ValueError, match="bleep_probability_blend_enabled"):
            replace(default, bleep_probability_blend_enabled=bad)
    active = Strategy.load("config/settlement-edge-active-paper.json")
    assert not active.bleep_probability_only_enabled
    assert active.bleep_probability_blend_enabled
    assert not active.standard_cashout_enabled
    assert (active.min_entry_price, active.max_entry_price) == (0.80, 0.95)


@pytest.mark.parametrize("operator", [">=", ">", "<", "<="])
def test_bleep_only_ignores_project15_probability_and_uncertainty(market, config, now, operator):
    c = replace(config, bleep_probability_only_enabled=True)
    spec = replace(market.spec, comparison_operator=operator)
    f = features(history(now, spec.strike + 100), now, c)
    for p_yes, uncertainty in [(0.01, 0.99), (0.99, 0.01)]:
        original = dict(
            p_yes=p_yes, p_no=1 - p_yes, uncertainty=uncertainty, settlement_mean=123, settlement_std=4
        )
        result = blend_probability(original, spec, f, now, clamp=True, bleep_only=True)
        b = mode_b_probability(spec, f["reference"], spec.settlement_end - now, f["bleep"], clamp=True)
        assert result["p_yes"] == result["conservative_yes"] == b["p_yes"]
        assert result["p_no"] == result["conservative_no"] == b["p_no"]
        assert result["settlement_mean"] == 123
        assert result["settlement_std"] == 4
        assert result["blend"]["weight_bleep"] == 1
    with pytest.raises(ValueError, match="BLEEP_WARMUP"):
        blend_probability(original, spec, {**f, "bleep": None}, now, bleep_only=True)


def test_bleep_only_config_validation(config):
    assert replace(config, bleep_probability_only_enabled=True).version != config.version
    with pytest.raises(ValueError, match="either"):
        replace(config, bleep_probability_only_enabled=True, bleep_probability_blend_enabled=True)
    with pytest.raises(ValueError, match="boolean"):
        replace(config, bleep_probability_only_enabled=1)
