from dataclasses import replace
from decimal import Decimal

import pytest

from btc_probability.models import research_settlement, round_with_lean, simulate
from btc_probability.schema import Reference

from .test_models import spec


@pytest.mark.parametrize("lean,expected", [(0.6, "76492.63"), (0.4, "76492.62")])
def test_user_exact_half_cent_rule(lean, expected):
    assert round_with_lean("76492.625", lean) == Decimal(expected)
    # Non-ties keep ordinary nearest-cent rounding, regardless of lean.
    assert round_with_lean("76492.6249", lean) == Decimal("76492.62")
    assert round_with_lean("76492.6251", lean) == Decimal("76492.63")


def test_neutral_has_no_invented_direction():
    with pytest.raises(ValueError, match="NO_DIRECTIONAL_LEAN"):
        round_with_lean("76492.625", 0.5)


@pytest.mark.parametrize("target,lean,expected", [("76492.62", "YES", 1), ("76492.63", "NO", 0)])
def test_research_uses_pre_rounding_average_and_preserves_guard(target, lean, expected):
    s = replace(
        spec(),
        target=target,
        verified=False,
        rounding=None,
        unresolved=("ROUNDING_TIE_UNSPECIFIED",),
        research_tie_policy="user-model-lean-v1",
    )
    known = {t: "76492.625" for t in s.sample_times}
    result = research_settlement(s, Reference("76480", 60, 60), known, 60, 0)
    assert result["lean"] == lean
    assert result["result"]["p_yes"] == expected
    assert not result["verified_settlement"] and not s.verified and s.rounding is None
    with pytest.raises(ValueError, match="UNVERIFIED_RULES"):
        simulate(s, Reference("76480", 60, 60), known, 60, 0)
    with pytest.raises(ValueError, match="MISSING_OR_NONCAUSAL"):
        research_settlement(s, Reference("76480", 60, 60), {}, 60, 0)
    with pytest.raises(ValueError, match="UNSUPPORTED_RESEARCH_RULES"):
        research_settlement(replace(s, unresolved=("PRIMARY_RULES_CHANGED",)), None, known, 60, 0)
