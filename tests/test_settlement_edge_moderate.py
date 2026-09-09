from dataclasses import asdict
from pathlib import Path

import pytest

from btc15.config import Strategy
from btc15.domain import Book
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate

PRESET = Path(__file__).resolve().parents[1] / "config/settlement-edge-paper-moderate.json"


def decision(
    market,
    config,
    *,
    ask=0.90,
    probability=0.93,
    remaining=300,
    quality_reasons=(),
    extras=(),
    regime="NORMAL",
):
    now = market.close_time - remaining
    book = Book()
    book.snapshot(
        dict(yes_dollars_fp=[[str(ask - 0.02), "20"]], no_dollars_fp=[[str(ask), "20"]]),
        now,
    )
    return evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime=regime),
        dict(conservative_yes=probability),
        dict(score=100, reasons=list(quality_reasons)),
        now,
        config,
        extras,
    )


def test_preset_changes_only_declared_entry_thresholds():
    original = asdict(Strategy())
    moderate = asdict(Strategy.load(PRESET))
    assert {k: v for k, v in moderate.items() if v != original[k]} == {
        "entry_window_start": 600,
        "min_entry_price": 0.80,
        "min_edge": 0.02,
        "min_ev": 0.02,
    }


@pytest.mark.parametrize(
    "inputs,old_reason",
    [
        ({}, "MIN_EV"),
        ({"ask": 0.82}, "MIN_PRICE"),
        ({"probability": 0.97, "remaining": 540}, "ENTRY_WINDOW"),
    ],
)
def test_moderate_admits_previously_rejected_positive_edge(market, inputs, old_reason):
    original = decision(market, Strategy(), **inputs)
    assert old_reason in {r["code"] for r in original["reasons"]}
    moderate = decision(market, Strategy.load(PRESET), **inputs)
    assert moderate["decision"] == "TRADE_CANDIDATE"
    assert moderate["net_ev"] >= 0.02


@pytest.mark.parametrize(
    "inputs,reason",
    [
        ({"probability": 0.92}, "MIN_EV"),
        ({"ask": 0.82, "probability": 0.89}, "MIN_PROBABILITY"),
        ({"remaining": 120}, "ENTRY_WINDOW"),
        ({"remaining": 601}, "ENTRY_WINDOW"),
        ({"quality_reasons": ["STALE_REFERENCE"]}, "STALE_REFERENCE"),
        ({"quality_reasons": ["STALE_BOOK"]}, "STALE_BOOK"),
        ({"extras": ["UNVERIFIED_FEES"]}, "UNVERIFIED_FEES"),
        ({"regime": "EXTREME"}, "REGIME"),
    ],
)
def test_moderate_keeps_safety_and_confidence_gates(market, inputs, reason):
    result = decision(market, Strategy.load(PRESET), **inputs)
    assert result["decision"] == "NO_TRADE"
    assert reason in {r["code"] for r in result["reasons"]}
