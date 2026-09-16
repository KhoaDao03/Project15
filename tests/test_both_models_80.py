from dataclasses import replace

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import component_probability_rejections, evaluate


@pytest.mark.parametrize("floor", [0.80, 0.85])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize(
    "a,b,late,allowed",
    [
        (0.92725, 0.772642, False, False),
        (0.799, 0.99, False, False),
        (0.80, 0.80, False, True),
        (0.8499, 0.95, False, True),
        (0.85, 0.85, False, True),
        (0.80, 0.80, True, False),
        (0.90, 0.80, True, True),
    ],
)
def test_component_gate_and_late_floor(store, config, market, now, side, a, b, late, allowed, floor):
    minimum = 0.80 if late else floor
    allowed = allowed and min(a, b) >= minimum
    now = market.close_time - (60 if late else 300)
    c = replace(
        config,
        both_models_80_enabled=True,
        standard_component_min_probability=floor,
        bleep_probability_blend_enabled=True,
        entry_value_filters_enabled=False,
        entry_probability_deductions=False,
        min_probability=0.8,
        late_min_probability=0.85,
        passive=False,
        sustained_lead_enabled=late,
        late_entry_enabled=late,
        min_entry_price=0.75,
        max_entry_price=0.95,
    )
    p = dict(
        p_yes=(a + b) / 2 if side == "yes" else 1 - (a + b) / 2,
        p_no=(a + b) / 2 if side == "no" else 1 - (a + b) / 2,
        blend={"project15_p_" + side: a, "bleep": {"p_" + side: b}},
        lead=dict(side=side, lead_sigma=3, confirmed_late=True, confirmation_samples=3),
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
    assert not {"MIN_EDGE", "MIN_EV"} & {r["code"] for r in d["reasons"]}
    assert d["component_probabilities"] == dict(project15=a, bleep=b)
    if min(a, b) < minimum:
        assert d["effective_max_entry_price"] is None
    e = initialize(store, market, now, c, "PAPER")
    if allowed:
        order = e.submit(market, book, d, "components", now, True)
        assert order is not None
        e.aggressive(market, book, now + c.latency_seconds)
        assert store.list(kind="fill")
    elif min(a, b) < minimum:
        # A caller cannot bypass the component gate just by labelling it a candidate.
        assert e.submit(market, book, {**d, "decision": "TRADE_CANDIDATE"}, "invalid", now, True) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "0.9", 1.1, 0.7999])
def test_invalid_or_low_component_fails_closed(bad):
    assert (
        component_probability_rejections(dict(project15=0.9, bleep=bad))[0]["code"] == "BLEEP_MIN_PROBABILITY"
    )
    assert (
        component_probability_rejections(dict(project15=bad, bleep=0.9))[0]["code"]
        == "PROJECT15_MIN_PROBABILITY"
    )


def test_config_identity_and_missing_blend():
    assert Strategy().version == "1766c001ffaa6835"
    with pytest.raises(ValueError, match="requires Bleep"):
        replace(Strategy(), both_models_80_enabled=True)
    assert len(component_probability_rejections({})) == 2


@pytest.mark.parametrize(
    "remaining,allowed,path",
    [
        (481, False, "standard"),
        (480, True, "standard"),
        (121, True, "standard"),
        (120, True, "late_settlement"),
        (16, True, "late_settlement"),
        (15, False, "late_settlement"),
    ],
)
def test_active_entry_window(market, remaining, allowed, path):
    c = Strategy.load("config/settlement-edge-active-paper.json")
    now = market.close_time - remaining
    p = dict(
        p_yes=0.90,
        p_no=0.10,
        blend=dict(project15_p_yes=0.90, bleep=dict(p_yes=0.90)),
        lead=dict(
            side="yes", lead_sigma=3, confirmed_normal=True, confirmed_late=True, confirmation_samples=3
        ),
    )
    d = evaluate(
        market,
        make_book("yes", ".89", ".90", now),
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    assert d["entry_path"] == path


@pytest.mark.parametrize("floor", [0.49, 1.01, float("nan"), True])
def test_invalid_standard_floor(floor):
    with pytest.raises(ValueError):
        replace(Strategy(), standard_component_min_probability=floor)
