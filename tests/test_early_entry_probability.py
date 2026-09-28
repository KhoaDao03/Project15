import hashlib
import json
from dataclasses import asdict

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import model_name
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate

THRESHOLDS = {"BTC": .87, "ETH": .86, "SOL": .83, "XRP": .85, "DOGE": .83, "BNB": .86, "HYPE": .84}


@pytest.mark.parametrize("asset,early", THRESHOLDS.items())
@pytest.mark.parametrize("remaining", [480.001, 480, 479.999, 420.001, 420, 419.999, 120, 1])
@pytest.mark.parametrize("delta", [0, -.001])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_signal_and_paper_submission_agree_on_schedule(store, market, asset, early, remaining, delta, side):
    suffix = "active" if asset == "BTC" else asset.lower()
    config = Strategy.load(f"config/settlement-edge-{suffix}-paper.json")
    now = market.close_time - remaining
    floor = early if 420 < remaining <= 480 else .83
    assert config.probability_floor(remaining <= 120, remaining=remaining) == floor
    book = make_book(side, ".89", ".90", now)
    p = {"p_" + side: floor + delta, "lead": dict(side=side, confirmed_normal=True, confirmed_late=True)}
    d = evaluate(market, book, Tick(now, now, market.spec.strike + (10 if side == "yes" else -10)),
                 dict(regime="NORMAL"), p, dict(score=100, reasons=[]), now, config)
    codes = {r["code"] for r in d["reasons"]}
    assert ("ENTRY_WINDOW" in codes) == (remaining > 480 or remaining <= 1)
    assert ("MIN_PROBABILITY" in codes) == (delta < 0)
    assert (d["effective_max_entry_price"] is None) == (delta < 0)
    if delta < 0:
        # Bypass the signal result to exercise the independent submission recheck.
        d["decision"] = "TRADE_CANDIDATE"
    ex = initialize(store, market, now, config, "PAPER")
    order = ex.submit(market, book, d, "early-window", now, True)
    assert (order is not None) == (1 < remaining <= 480 and delta == 0)


@pytest.mark.parametrize("value", [-.01, 1.01, float("nan"), True, "0.87"])
def test_invalid_early_probability(value):
    with pytest.raises(ValueError):
        Strategy(early_min_probability=value)


def test_legacy_config_retains_probability_and_identity():
    c = Strategy()
    assert c.probability_floor(remaining=450) == c.min_probability
    values = asdict(c)
    values.pop("early_min_probability")
    previous = {"probability_model": model_name(c.asset), **values}
    assert c.version == hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()[:16]
