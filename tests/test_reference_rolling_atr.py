import copy
from dataclasses import replace

import pytest
from bleep_helpers import inputs

from btc15.config import Strategy
from btc15.strategies.settlement_edge.bleep import probability
from btc15.strategies.settlement_edge.model import Tick, features


def reference_ticks():
    return [Tick(t, t, 100 + (t // 60 % 5) * 2 + (t % 60) / 60) for t in range(2401)]


def test_last_fifteen_reference_candles_including_live_minute():
    ticks = reference_ticks()
    f = features(ticks, 2400, Strategy())
    candles = []
    for m in range(26, 41):
        ps = [t.price for t in ticks if int(t.source // 60) == m]
        candles.append((max(ps), min(ps), ps[-1]))
    expected = (
        sum(
            max(high - low, abs(high - prev[2]), abs(low - prev[2]))
            for prev, (high, low, close) in zip(candles, candles[1:])
        )
        / 14
    )
    assert f["reference_rolling_atr"] == pytest.approx(expected)
    # Long-lived and freshly loaded processes agree on the same final 15 candles.
    assert features(ticks[-841:], 2400, Strategy())["reference_rolling_atr"] == f["reference_rolling_atr"]
    future = ticks + [Tick(2401, 2401, 1e6), Tick(2399.5, 2401, 1e6)]
    assert features(future, 2400, Strategy())["reference_rolling_atr"] == f["reference_rolling_atr"]


def test_reference_gap_prevents_stitching_rolling_window():
    ticks = [t for t in reference_ticks() if not 35 * 60 <= t.source < 36 * 60]
    assert features(ticks, 2400, Strategy())["reference_rolling_atr"] is None


@pytest.mark.parametrize("asset", ["BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI"])
def test_seed_atr_cannot_change_crypto_probability(market, asset):
    c = Strategy(asset=asset)
    now = market.close_time - 120
    spot = market.spec.strike + 20
    f = inputs(spot)
    f["reference_rolling_atr"] = 20
    ticks = [Tick(now, now, spot)]
    a = probability(market.spec, ticks, now, f, c)
    other = copy.deepcopy(f)
    other["bleep"]["atr"] = 1e6
    b = probability(market.spec, ticks, now, other, c)
    for key in ["p_yes", "p_no", "sigma_t", "atr"]:
        assert a[key] == b[key]
    assert b["atr_source"] == "official_reference_rolling"
    assert b["atr"] == 20
    del other["reference_rolling_atr"]
    floor = probability(market.spec, ticks, now, other, c)
    assert floor["atr_source"] == "reference_price_floor"
    assert floor["atr"] == pytest.approx(spot * 0.00015)


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True])
def test_invalid_reference_atr_rejects(market, bad):
    now = market.close_time - 120
    f = inputs(market.spec.strike)
    f["reference_rolling_atr"] = bad
    with pytest.raises(ValueError, match="BLEEP_ATR"):
        probability(market.spec, [Tick(now, now, market.spec.strike)], now, f, Strategy())


def test_commodity_predictor_uses_reference_atr():
    from btc15.strategies.settlement_edge.bleep import model_name

    assert model_name("GOLD") == "bleep-reference-atr-finish-v5"
    assert replace(Strategy(), asset="GOLD").version != Strategy().version
