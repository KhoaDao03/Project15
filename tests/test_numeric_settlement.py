import copy
import json
from pathlib import Path

import pytest

from btc15.domain import parse_market


@pytest.fixture(
    params=json.loads((Path(__file__).parent / "fixtures/numeric-settlement-20260921.json").read_text())
)
def contract(request):
    return copy.deepcopy(request.param)


def test_numeric_threshold_published_contract(contract):
    raw, series = contract["market"], contract["series"]
    market = parse_market(raw, series)
    assert market.spec.strike == raw["floor_strike"]
    assert market.spec.comparison_operator == ">="


@pytest.mark.parametrize("change", ["strike", "time", "operator", "index", "extra", "rounding"])
def test_numeric_threshold_rejects_conflicts(contract, change):
    raw, series = contract["market"], contract["series"]
    if change == "strike":
        raw["floor_strike"] += 1
    elif change == "time":
        raw["rules_primary"] = raw["rules_primary"].replace("12:00 AM", "12:01 AM")
    elif change == "operator":
        raw["strike_type"] = "less"
    elif change == "index":
        raw["rules_primary"] = raw["rules_primary"].replace("USD_RTI", "UNKNOWN")
    elif change == "extra":
        raw["rules_primary"] += " Extra settlement condition."
    else:
        raw["rules_secondary"] = raw["rules_secondary"].replace("decimal places", "digits")
    with pytest.raises(ValueError):
        parse_market(raw, series)
