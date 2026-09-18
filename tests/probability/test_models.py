from dataclasses import replace
from decimal import Decimal

import pytest

from btc_probability.models import simulate, terminal, wilson
from btc_probability.reference import ReferenceHistory
from btc_probability.schema import ContractSpec, Reference


def spec(end=60, **overrides):
    values = dict(
        series_ticker="SYNTHETIC",
        market_ticker="SYNTHETIC-1",
        event_ticker="SYNTHETIC-E",
        target="100",
        target_source="synthetic",
        open_time=-840,
        trading_close_time=end,
        observation_end_time=end,
        sample_times=tuple(range(end - 59, end + 1)),
        reference_index="BRTI",
        comparison=">=",
        equality_yes=True,
        rounding="ROUND_HALF_UP",
        decimal_places=2,
        payout="1",
        currency="USD",
        lifecycle="active",
        rules_url="synthetic://rules",
        rules_hash="test",
        metadata_received=-840,
        verified=True,
        synthetic=True,
    )
    values.update(overrides)
    return ContractSpec(**values)


@pytest.mark.parametrize(
    "price,seconds,expected",
    [
        (76269, 870, 0.327435),
        (76384, 690, 0.866225),
        (76403, 650, 0.921652),
        (76448, 520, 0.989516),
        (76369, 310, 0.910089),
    ],
)
def test_formula_regression(price, seconds, expected):
    result = terminal(price, 76304.78, seconds, 0.20)
    assert result["p_yes"] == pytest.approx(expected, abs=0.0000006)
    assert result["p_yes"] + result["p_no"] == 1


@pytest.mark.parametrize("comparison,expected", [(">=", 1), (">", 0), ("<=", 1), ("<", 0)])
@pytest.mark.parametrize("seconds,sigma", [(0, 0.2), (100, 0)])
def test_deterministic_equality(comparison, expected, seconds, sigma):
    assert terminal("100", "100", seconds, sigma, comparison=comparison)["p_yes"] == expected


def test_invalid_and_tails():
    for args in [
        (0, 100, 10, 0.2),
        (100, -1, 10, 0.2),
        (100, 100, -1, 0.2),
        (100, 100, 10, -1),
        ("nan", 100, 1, 0.2),
    ]:
        with pytest.raises(ValueError):
            terminal(*args)
    assert terminal(1e8, 100, 1, 0.01)["p_yes"] == 1
    assert terminal(1, 100, 1, 0.01)["p_yes"] == 0


def test_monotonicity_in_price_and_strike():
    ps = [terminal(p, 100, 300, 0.5)["p_yes"] for p in range(95, 106)]
    ks = [terminal(100, k, 300, 0.5)["p_yes"] for k in range(95, 106)]
    assert ps == sorted(ps)
    assert ks == sorted(ks, reverse=True)
    s = spec()
    samples = [
        simulate(s, Reference(str(p), 0, 0), {}, 0, 0.5, paths=1000)["p_yes"] for p in (99.9, 100, 100.1)
    ]
    assert samples == sorted(samples)


def test_exact_rounding():
    half_up = spec(target="100.01")
    half_even = replace(half_up, rounding="ROUND_HALF_EVEN")
    assert half_up.yes(Decimal("100.005"))
    assert not half_even.yes(Decimal("100.005"))
    assert not replace(half_up, comparison=">", equality_yes=False).yes("100.005")


@pytest.mark.parametrize("m", [0, 1, 59, 60])
def test_sample_boundaries(m):
    s = spec()
    h = ReferenceHistory()
    for t in range(m + 1):
        h.add(Reference("101", t, t))
    ref, known, missing, flags = h.snapshot(s, m)
    assert len(known) == m and missing == 0 and not flags
    result = simulate(s, ref, known, m, 0.2, paths=1000)
    assert result["p_yes"] + result["p_no"] == 1
    if m == 60:
        assert result["method"] == "exact-decimal" and result["paths"] == 0


def test_missing_late_duplicate_conflicting_and_out_of_order():
    s = spec()
    h = ReferenceHistory()
    h.add(Reference("100", 1, 1))
    h.add(Reference("100", 1, 1))
    h.add(Reference("101", 3, 3))
    h.add(Reference("100", 2, 5, recovered=True))
    assert h.snapshot(s, 3)[2] == 1
    assert h.snapshot(s, 5)[2] == 2  # slots 4 and 5 still missing
    assert 2 not in h.snapshot(s, 3)[1]
    assert h.snapshot(s, 5)[1][2] == "100"
    h.add(Reference("99", 1, 6))
    assert "CONFLICTING_REFERENCE" in h.snapshot(s, 6)[3]
    with pytest.raises(ValueError, match="MISSING"):
        simulate(s, Reference("100", 3, 3), {1: "100", 3: "101"}, 3, 0.2, paths=100)


def test_last_tick_below_target_but_average_wins():
    known = {t: "101" for t in range(1, 60)}
    known[60] = "99"
    result = simulate(spec(), Reference("99", 60, 60), known, 60, 0.2)
    assert result["p_yes"] == 1


def test_correlated_paths_do_not_divide_uncertainty_by_sqrt60():
    # One-second observation spacing means most average uncertainty is shared.
    result = simulate(spec(end=600), Reference("100", 0, 0), {}, 0, 0.5, paths=20000)
    terminal_std = 100 * 0.5 * (600 / 31536000) ** 0.5
    assert result["std"] > terminal_std * 0.9
    assert result["std"] > terminal_std / (60**0.5) * 5


def test_terminal_mc_matches_analytic_under_matching_drift():
    s = spec(end=600, sample_times=(600,), rounding="none", target="100.02")
    result = simulate(s, Reference("100", 0, 0), {}, 0, 0.5, paths=100000, seed=15)
    p = terminal(100, 100.02, 600, 0.5)["p_yes"]
    assert result["numerical_interval"][0] <= p <= result["numerical_interval"][1]


def test_stale_timestamp_full_interval_and_precision():
    s = spec(end=600, sample_times=(600,), rounding="none")
    old = simulate(s, Reference("100", 0, 0), {}, 300, 0.5, paths=1000)
    fresh = simulate(s, Reference("100", 300, 300), {}, 300, 0.5, paths=1000)
    assert old["std"] > fresh["std"] * 1.3
    assert not old["precision_met"]
    assert wilson(1000, 1000)[0] < 1
    assert wilson(0, 1000)[1] > 0
    assert old == simulate(s, Reference("100", 0, 0), {}, 300, 0.5, paths=1000)


def test_zero_variance_settlement_is_exact():
    result = simulate(spec(target="100.01"), Reference("100.005", 0, 0), {}, 0, 0, paths=100)
    assert result["p_yes"] == 1
    assert result["numerical_interval"] == [1, 1]
    assert result["method"] == "zero-variance-model"


def test_subsecond_samples_are_not_silently_binned():
    h = ReferenceHistory()
    h.add(Reference("100", 1.2, 1.2))
    _, known, missing, flags = h.snapshot(spec(), 2)
    assert not known and missing == 2 and "MISSING_PAST_SAMPLES" in flags


def test_aggregate_crosscheck_preserves_start_boundary():
    h = ReferenceHistory()
    h.add(Reference("999", 0, 0))
    h.add(Reference("100", 1, 1))
    aggregate = dict(window_size=1, window_start_ts_ms=0, value="100.00000000")
    assert h.crosscheck(spec(), aggregate, 1) == "DIFFERENT_WINDOW_SEMANTICS"
    assert h.crosscheck(spec(), dict(aggregate, window_size=2), 1) == "DIFFERENT_WINDOW_SEMANTICS"
