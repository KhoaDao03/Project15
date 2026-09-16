import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


@pytest.mark.parametrize("asset", ["active", "eth", "sol", "xrp"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("remaining", [300, 40])
@pytest.mark.parametrize(
    "project15,bleep,reason",
    [
        (0.90, 0.78, None),
        (0.8998, 0.78, None),
        (0.50, 0.78, None),
        (0.95, 0.7799, "BLEEP_MIN_PROBABILITY"),
        (0.79, 0.91, None),
    ],
)
def test_active_blend_probability_gates(store, market, asset, side, remaining, project15, bleep, reason):
    c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
    assert c.bleep_probability_blend_enabled and not c.bleep_probability_only_enabled
    assert c.min_probability == c.late_min_probability == 0.0
    assert c.standard_component_min_probability == c.late_component_min_probability == 0.78
    assert not c.project15_probability_veto_enabled
    assert c.fixed_stop_price == 0.55
    now = market.close_time - remaining
    selected = (project15 + bleep) / 2
    p = dict(
        p_yes=selected if side == "yes" else 1 - selected,
        p_no=selected if side == "no" else 1 - selected,
        blend={"project15_p_" + side: project15, "bleep": {"p_" + side: bleep}},
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
    codes = {r["code"] for r in d["reasons"]}
    assert "PROJECT15_MIN_PROBABILITY" not in codes
    assert (d["decision"] == "TRADE_CANDIDATE") == (reason is None)
    if reason:
        assert reason in codes
    executor = initialize(store, market, now, c, "PAPER")
    # The final floor is enforced by evaluation; submission also guards the component floor.
    submission = {**d, "decision": "TRADE_CANDIDATE"} if reason == "BLEEP_MIN_PROBABILITY" else d
    order = executor.submit(market, book, submission, "test", now, True)
    assert (order is not None) == (reason is None)
