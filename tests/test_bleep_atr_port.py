"""Acceptance checks for the requested Bleepblorp 0.2 crypto rules."""

import math
from dataclasses import replace

import pytest
from bleep_helpers import inputs
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import capped_confidence, probability, safety_clamp
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


@pytest.mark.parametrize(
    "asset,multiplier",
    [("BTC", 1.35), ("ETH", 1.25), ("SOL", 1), ("XRP", 1), ("GOLD", 1.35), ("SILVER", 1.35), ("WTI", 1.35)],
)
@pytest.mark.parametrize("remaining", [480, 120, 10, 1])
@pytest.mark.parametrize("offset", [-20, 20])
def test_atr_finish_matches_normal_cdf_and_fading_adjustment(market, asset, multiplier, remaining, offset):
    now = market.close_time - remaining
    spot = market.spec.strike + offset
    ticks = [Tick(t, t, spot) for t in range(int(now) - 60, int(now) + 1)]
    f = inputs(spot)
    f["reference_rolling_atr"] = 20
    f["bleep"]["atr"] = 20
    p = probability(market.spec, ticks, now, f, Strategy(asset=asset))
    sigma = 20 * math.sqrt(remaining / 60) * multiplier
    base = (1 + math.erf(offset / sigma / math.sqrt(2))) / 2
    weight = min(0.15, remaining / 900)
    expected = min(0.98, max(0.02, (1 - weight) * base + weight * 0.5))
    assert p["sigma_t"] == pytest.approx(sigma)
    assert p["sigma_multiplier"] == multiplier
    assert p["indicator_weight"] == weight
    assert p["p_yes"] == pytest.approx(expected, abs=1e-7)
    assert p["p_no"] == 1 - p["p_yes"]


def test_xrp_floor_is_relative_not_one_cent(market):
    now = market.close_time - 120
    spec = replace(market.spec, strike=0.5, round_digits=2)
    f = inputs(0.501)
    p = probability(spec, [Tick(now, now, 0.501)], now, f, Strategy(asset="XRP"))
    assert p["atr"] == pytest.approx(0.501 * 0.00015)
    assert p["sigma_t"] == pytest.approx(p["atr"] * math.sqrt(2))


@pytest.mark.parametrize("p,expected", [(0.95, 0.78), (0.05, 0.22)])
def test_safety_cap_five_points_below_requested_floor(p, expected):
    assert safety_clamp(p, 0.49, 0.78) == pytest.approx(expected)
    assert safety_clamp(p, 0.5, 0.78) == p


@pytest.mark.parametrize(
    "asset,premium",
    [
        ("BTC", 0.06),
        ("ETH", 0.06),
        ("SOL", 0.10),
        ("XRP", 0.10),
        ("GOLD", 0.06),
        ("SILVER", 0.06),
        ("WTI", 0.06),
    ],
)
def test_market_cap_exact_boundary_and_never_increases_probability(asset, premium):
    mid = 0.83 - premium
    assert capped_confidence(0.98, mid - 0.01, mid + 0.01, asset) == pytest.approx(0.83)
    assert capped_confidence(0.98, mid - 0.011, mid + 0.009, asset) < 0.83
    assert capped_confidence(0.60, 0.89, 0.90, asset) == 0.60
    assert capped_confidence(0.99, 0.98, 0.99, asset) == 0.98


@pytest.mark.parametrize("bid,ask", [(None, 0.9), (0.9, None), (0.91, 0.9), (float("nan"), 0.9)])
def test_unusable_market_quotes_cannot_pass(bid, ask):
    assert capped_confidence(0.98, bid, ask, "BTC") is None


@pytest.mark.parametrize("asset", ["BTC", "ETH", "SOL", "XRP"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_evaluation_and_submission_recheck_current_market_cap(store, market, asset, side):
    # Widen only the price range to exercise a cap that the production 80c minimum
    # can otherwise obscure. No EV/price gate substitutes for the confidence gate.
    c = replace(Strategy.load("config/settlement-edge-active-paper.json"), asset=asset, min_entry_price=0.5)
    now = market.close_time - 300
    lead = dict(side=side, confirmed_normal=True, confirmed_late=True, confirmation_samples=1)
    p = {"p_" + side: 0.97, "lead": lead}
    high = make_book(side, ".89", ".90", now)
    d = evaluate(
        market,
        high,
        Tick(now, now, market.spec.strike + (10 if side == "yes" else -10)),
        dict(regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert d["decision"] == "TRADE_CANDIDATE"
    assert d["market_cap_applied"] == (asset in ("BTC", "ETH"))
    low = make_book(side, ".69", ".70", now)
    blocked = evaluate(
        market,
        low,
        Tick(now, now, market.spec.strike + (10 if side == "yes" else -10)),
        dict(regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert "MIN_PROBABILITY" in {r["code"] for r in blocked["reasons"]}
    ex = initialize(store, market, now, c, "PAPER")
    assert ex.submit(market, low, d, "test", now, True) is None


@pytest.mark.parametrize("remaining", [480, 60, 2])
def test_non_neutral_indicator_nudge_fades_toward_expiry(market, remaining):
    now = market.close_time - remaining
    spot = market.spec.strike + 20
    f = inputs(spot)
    f["reference_rolling_atr"] = 20
    f["bleep"].update(atr=20, stoch_k=60, stoch_k_previous=55)
    ticks = [Tick(t, t, spot) for t in range(int(now) - 60, int(now) + 1)]
    p = probability(market.spec, ticks, now, f, Strategy(asset="BTC"))
    assert p["lean"] == 0.55
    weight = min(0.15, remaining / 900)
    expected = (1 - weight) * p["base_p_up"] + weight * (0.5 + 0.12 * 0.55)
    assert p["p_yes"] == pytest.approx(min(0.98, max(0.02, expected)))
