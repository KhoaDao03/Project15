import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import indicator_inputs
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


def test_indicator_formulas_keep_reference_parity():
    groups = json.loads(Path("tests/fixtures/bleep-mode-b-reference.json").read_text())
    for group in groups:
        actual = indicator_inputs(group["candles"])
        for case in group["cases"]:
            for key in ("atr", "stoch_k", "stoch_k_previous", "bb_middle", "bb_upper", "bb_lower"):
                assert actual[key] == pytest.approx(case["expected"][key], abs=1e-10)


@pytest.mark.parametrize(
    "field",
    [
        "bleep_probability_blend_enabled",
        "bleep_probability_only_enabled",
        "project15_probability_veto_enabled",
        "both_models_80_enabled",
        "standard_component_min_probability",
        "late_component_min_probability",
        "bleep_settlement_model_enabled",
        "entry_probability_deductions",
        "paths",
        "seed",
        "calibration_penalty",
    ],
)
def test_removed_settings_are_not_accepted(field, tmp_path):
    assert field not in asdict(Strategy())
    path = tmp_path / "old.json"
    path.write_text(json.dumps({field: True}))
    with pytest.raises(TypeError):
        Strategy.load(path)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("remaining", [300, 40])
@pytest.mark.parametrize(
    "value,allowed", [(0.83, True), (0.8299, False), (None, False), (float("nan"), False)]
)
def test_bleep_floor_blocks_evaluation_and_forced_submission(store, market, side, remaining, value, allowed):
    c = Strategy.load("config/settlement-edge-active-paper.json")
    assert c.min_probability == c.late_min_probability == 0.83
    now = market.close_time - remaining
    book = make_book(side, ".89", ".90", now)
    lead = dict(side=side, confirmed_normal=True, confirmed_late=True, confirmation_samples=1)
    probability = {"p_" + side: value, "lead": lead}
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + (100 if side == "yes" else -100)),
        dict(regime="NORMAL"),
        probability,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    ex = initialize(store, market, now, c, "PAPER")
    forced = dict(d, decision="TRADE_CANDIDATE", conservative_probability=value)
    assert (ex.submit(market, book, forced, "test", now, True) is not None) == allowed


def test_cloud_configs_share_single_model_and_probability_floor(tmp_path):
    import runpy

    from btc15.fleet import load_members

    path = runpy.run_path("scripts/prepare_cloud.py")["prepare"](tmp_path / "cloud")
    members = load_members(path)
    assert set(members) == {"BTC", "ETH", "SOL", "XRP"}
    base = members["BTC"]["config"]
    for asset, member in members.items():
        assert replace(member["config"], asset="BTC") == base
        assert member["config"].min_probability == member["config"].late_min_probability == 0.83
        assert member["live_only"]
        assert member["config"].take_profit == 0.99 and member["config"].fixed_stop_price == 0.55


def test_missing_indicator_history_blocks_engine_without_fallback(store, market, config, now):
    from test_sustained_lead import reference, setup_engine

    engine, price = setup_engine(store, market, config, now)
    engine.ticks = engine.ticks[-300:]
    reference(engine, market, now, price)
    decision = engine.latest[market.ticker]
    assert decision["decision"] == "NO_TRADE"
    assert "BLEEP_WARMUP" in decision["reasons"][0]["actual"]
    assert not engine.executor.orders
    assert not decision["probability"]
