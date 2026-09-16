from dataclasses import replace

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import effective_entry_ceiling, evaluate


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("enabled", [True, False])
def test_negative_value_evaluation_submission_and_fill(store, market, config, now, side, enabled):
    c = replace(
        config,
        passive=False,
        min_probability=0.8,
        min_entry_price=0.75,
        max_entry_price=0.95,
        entry_value_filters_enabled=enabled,
    )
    book = make_book(side, ".89", ".90", now)
    decision = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + (100 if side == "yes" else -100)),
        dict(volatility_disagreement=0, regime="NORMAL"),
        {"conservative_" + side: 0.82},
        dict(score=100, reasons=[]),
        now,
        c,
    )
    codes = {r["code"] for r in decision["reasons"]}
    assert decision["net_ev"] < 0
    assert ("MIN_EDGE" in codes) == enabled
    assert ("MIN_EV" in codes) == enabled
    assert (decision["decision"] == "TRADE_CANDIDATE") == (not enabled)
    ceiling = effective_entry_ceiling(market, 0.82, c)
    assert ceiling == 0.95 if not enabled else ceiling < 0.82
    # Submit an otherwise-valid candidate independently to exercise execution's
    # fresh-ask and IOC-limit checks, not just the evaluation gate.
    e = initialize(store, market, now, c, "PAPER")
    order = e.submit(
        market,
        book,
        dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.82),
        "negative-value",
        now,
        True,
    )
    assert (order is not None) == (not enabled)
    if order:
        assert store.list(kind="order")[0]["body"]["entry_checks"]["entry_value_filters_enabled"] is False
        assert e.risk.reserved[market.ticker] > order.quantity * 0.90
        e.aggressive(market, book, now + c.latency_seconds)
        fills = store.list(kind="fill")
        assert fills and fills[0]["body"]["price"] == 0.90
        assert fills[0]["body"]["fee"] > 0


def test_disabled_filters_keep_probability_price_and_spread_gates(market, config, now):
    c = replace(
        config,
        entry_value_filters_enabled=False,
        min_probability=0.8,
        min_entry_price=0.75,
        max_entry_price=0.95,
    )
    assert effective_entry_ceiling(market, 0.79, c) is None
    book = make_book("yes", ".60", ".70", now)
    d = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=0.79),
        dict(score=100, reasons=[]),
        now,
        c,
    )
    codes = {r["code"] for r in d["reasons"]}
    assert {"MIN_PRICE", "MIN_PROBABILITY", "SPREAD"} <= codes
    assert not {"MIN_EDGE", "MIN_EV"} & codes
    assert d["decision"] == "NO_TRADE"


def test_legacy_hash_and_flag_validation():
    assert Strategy().version == "1766c001ffaa6835"
    assert replace(Strategy(), entry_value_filters_enabled=False).version != Strategy().version
    for value in (0, "false", None):
        with pytest.raises(ValueError, match="entry_value_filters_enabled requires a boolean"):
            replace(Strategy(), entry_value_filters_enabled=value)
