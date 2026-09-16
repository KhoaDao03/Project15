from dataclasses import replace
import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


@pytest.mark.parametrize("asset", ["active", "eth", "sol", "xrp"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("remaining", [300, 40])
@pytest.mark.parametrize("bleep,allowed", [(0.85, True), (0.849, False)])
def test_historical_bleep_only_floor(store, market, asset, side, remaining, bleep, allowed):
    c = replace(
        Strategy.load(f"config/settlement-edge-{asset}-paper.json"),
        bleep_probability_only_enabled=True,
        bleep_probability_blend_enabled=False,
        standard_component_min_probability=0.85,
    )
    assert c.bleep_probability_only_enabled and not c.bleep_probability_blend_enabled
    assert not c.project15_probability_veto_enabled
    assert c.min_probability == c.late_min_probability == 0.85
    assert c.standard_component_min_probability == 0.85 and c.both_models_80_enabled
    now = market.close_time - remaining
    p = dict(
        p_yes=bleep if side == "yes" else 1 - bleep,
        p_no=bleep if side == "no" else 1 - bleep,
        blend={"project15_p_" + side: 0.01, "bleep": {"p_" + side: bleep}},
        lead=dict(
            side=side, lead_sigma=3, confirmed_normal=True, confirmed_late=True, confirmation_samples=3
        ),
    )
    book = make_book(side, ".89", ".90", now)
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + (100 if side == "yes" else -100)),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    assert "PROJECT15_MIN_PROBABILITY" not in {r["code"] for r in d["reasons"]}
    executor = initialize(store, market, now, c, "PAPER")
    assert (executor.submit(market, book, d, "test", now, True) is not None) == allowed
