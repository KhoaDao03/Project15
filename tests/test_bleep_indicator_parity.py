import json
from pathlib import Path

import pytest

from btc15.strategies.settlement_edge.bleep import indicator_lean


def test_matches_bleepblorp02_typescript_boundary_cases():
    fixture = json.loads(Path("tests/fixtures/bleep02-indicator-lean.json").read_text())
    for c in fixture["cases"]:
        result = indicator_lean(
            100 + c["position"] * 10,
            dict(stoch_k=c["k"], stoch_k_previous=c["previous"], bb_middle=100, bb_upper=110, bb_lower=90),
        )
        assert result == pytest.approx(c["expected"], abs=1e-12), c
