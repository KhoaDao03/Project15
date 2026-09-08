import math
from dataclasses import replace

import pytest

from btc15.domain import Book, D, SettlementSpecification, order_direction, parse_market
from btc15.model import Tick, features, probability, quality


def test_current_contract_fixture(market, raw):
    assert market.spec.comparison_operator == ">="
    assert market.spec.strike == raw["floor_strike"]
    assert market.expiration_time - market.close_time == 604800
    assert market.spec.yes(market.spec.strike)
    assert not market.spec.yes(market.spec.strike - 0.01)
    assert market.exchange_index == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("ticker", "KXETH15M-TEST-00"),
        ("floor_strike", None),
        ("strike_type", "greater"),
        ("rules_primary", "Bitcoin might go up"),
        ("price_ranges", []),
        ("notional_value_dollars", "2.0000"),
        ("close_time", "2026-09-08T23:00:00Z"),
    ],
)
def test_parser_fail_closed(raw, series, field, value):
    raw[field] = value
    with pytest.raises((ValueError, TypeError, KeyError)):
        parse_market(raw, series)


@pytest.mark.parametrize(
    "action,side,price,expected",
    [
        ("buy", "yes", 0.86, ("bid", 0.86)),
        ("sell", "yes", 0.86, ("ask", 0.86)),
        ("buy", "no", 0.86, ("ask", 0.14)),
        ("sell", "no", 0.86, ("bid", 0.14)),
    ],
)
def test_four_order_directions(action, side, price, expected):
    assert order_direction(action, side, price) == expected


def test_complements_and_fractional_depth(book, now):
    assert book.bid("yes") == 0.88
    assert book.ask("yes") == 0.90
    assert book.bid("no") == 0.1
    assert book.ask("no") == 0.12
    book.delta(dict(side="no", price_dollars=".90", delta_fp="-.25", ts_ms=now * 1000), now)
    assert book.no[D(".1")] == D("20.50")
    with pytest.raises(ValueError):
        book.delta(dict(side="no", price_dollars=".90", delta_fp="-100", ts_ms=now * 1000), now)
    assert not book.valid


@pytest.mark.parametrize("price,down,up", [(0.899, 0.89, 0.9), (0.9955, 0.995, 0.996), (0.0905, 0.09, 0.091)])
def test_tapered_ticks(market, price, down, up):
    assert market.snap(price) == down
    assert market.snap(price, up=True) == up
    assert market.valid_tick(down)
    assert not market.valid_tick(price)


def test_bounds_seed_and_no_future_data(market, config, now):
    ticks = [Tick(now - 1, now - 1, market.spec.strike)]
    a = probability(market.spec, ticks, now, 0.0001, config)
    b = probability(market.spec, ticks + [Tick(now + 1, now + 1, 1e9)], now, 0.0001, config)
    assert a == b
    assert 0 <= a["conservative_yes"] <= a["p_yes"] <= 1
    assert 0 <= a["conservative_no"] <= a["p_no"] <= 1
    assert a["p_yes"] + a["p_no"] == 1
    assert a["simulation_uncertainty"] > 0


def test_final_minute_known_observations_not_resimulated(config):
    spec = SettlementSpecification("CF Benchmarks", "BRTI", 100, 940, 1000, ">=")
    ticks = [Tick(940 + i, 940 + i, 200 if i <= 59 else 1) for i in range(1, 61)]
    p = probability(spec, ticks, 1000, 100, config)
    assert p["known_samples"] == 60 and p["p_yes"] == 1
    with pytest.raises(ValueError, match="Missing past"):
        probability(spec, ticks[1:], 1000, 0.001, config)
    # Sample at start boundary is excluded; final close sample included.
    assert probability(spec, [Tick(940, 940, 1e9)] + ticks, 1000, 100, config) == p


def test_rounding_tie_ambiguity(config):
    spec = SettlementSpecification("CF Benchmarks", "BRTI", 100.01, 940, 1000, ">=")
    with pytest.raises(ValueError, match="Ambiguous"):
        spec.yes("100.005")
    assert not spec.yes("100.005", "half_even")
    assert spec.yes("100.005", "half_up")
    assert replace(spec, comparison_operator="<").favored(99) == "yes"


def test_features_causal_indicator_warmup(config, book, now):
    ticks = [Tick(now - i, now - i, 78000 + math.sin(i) * 5) for i in range(2000, -1, -1)]
    f = features(ticks, now, config)
    assert f == features(ticks + [Tick(now + 1, now + 1, 1e9)], now, config)
    assert f["atr"] is not None and f["bollinger"] is not None and f["stochastic_rsi"] is not None
    assert f["sigma"] > 0
    q = quality(f, ticks, book, now + 10, config)
    assert "STALE_REFERENCE" in q["reasons"] and "STALE_BOOK" in q["reasons"]


def test_book_crossing_rejected(now):
    b = Book()
    with pytest.raises(ValueError, match="Crossed"):
        b.snapshot(dict(yes_dollars_fp=[[".9", "1"]], no_dollars_fp=[[".8", "1"]]), now)
    assert not b.valid


def test_pending_strike_is_not_inferred(raw, series):
    raw["status"] = "initialized"
    raw["floor_strike"] = None
    with pytest.raises(ValueError, match="Strike not yet published"):
        parse_market(raw, series)


@pytest.mark.parametrize(
    "overrides",
    [
        {"paths": 100.5},
        {"passive": "true"},
        {"min_edge": "NaN"},
        {"bankroll": float("nan")},
        {"no_new_entry": 480},
    ],
)
def test_invalid_configuration_rejected(overrides):
    from btc15.config import Strategy

    with pytest.raises(ValueError):
        Strategy(**overrides)
