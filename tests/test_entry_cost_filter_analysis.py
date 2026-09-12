"""Boundary checks for the offline filter experiment; no live strategy mutation."""

import runpy
from pathlib import Path

import pytest

passes = runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_entry_cost_filters.py"))["passes"]


@pytest.mark.parametrize(
    "proxy,profit,expected",
    [(0.01, 0.05, True), (0.009999, 0.05, False), (0.01, 0.049999, False), (-0.01, 0.10, False)],
)
def test_both_independent_minima(proxy, profit, expected):
    report = dict(
        status="AVAILABLE",
        target_sale=dict(
            status="AVAILABLE", probability_weighted_proxy_per_contract=proxy, net_per_contract=profit
        ),
    )
    assert passes(report, 0.01, 0.05) is expected


def test_missing_target_is_not_accepted():
    assert not passes(dict(status="UNAVAILABLE"), 0, 0)
    assert not passes(dict(status="AVAILABLE", target_sale=dict(status="DISABLED")), 0, 0)
