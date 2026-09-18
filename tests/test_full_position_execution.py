from dataclasses import replace

import pytest
from test_execution import decision, ready
from test_exit_execution_v2 import held, quote, sells
from test_strategy_reverification import make_book

from btc15.config import Strategy
from btc15.domain import D


def full(config, **changes):
    return replace(
        config,
        full_position_execution=True,
        passive=False,
        fixed_contracts=10,
        max_contracts=10,
        max_trade_dollars=10,
        entry_value_filters_enabled=False,
        **changes,
    )


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("available", [6, 9.99, 10, 15])
def test_buy_full_size_or_none(store, config, market, now, side, available):
    c = full(config)
    ex = ready(store, market, now, c)
    book = make_book(side, ".89", ".90", now)
    order = ex.submit(market, book, {**decision(), "side": side}, "op", now, True)
    opposite = "no" if side == "yes" else "yes"
    setattr(book, opposite, {D(".100"): D(3), D(".099"): D(available) - 3, D(".097"): D(100)})
    book.received = book.source_time = now + 1
    ex.aggressive(market, book, now + 1)
    buys = [r["body"] for r in store.list(kind="fill")]
    if available < 10:
        assert not buys and not ex.positions
        assert order.remaining == 10 and not order.active
        assert any(r["body"].get("reason") == "full_size_unavailable" for r in store.list(kind="order"))
    else:
        assert ex.positions[market.ticker].quantity == 10
        assert sorted((f["price"], f["quantity"]) for f in buys) == [(0.9, 3), (0.901, 7)]
        assert order.remaining == 0 and not order.active


def test_no_risk_downsizing(store, config, market, now):
    c = full(config, max_open_exposure=6)
    ex = ready(store, market, now, c)
    assert ex.submit(market, make_book("yes", ".89", ".90", now), decision(), "op", now, True) is None
    assert store.list(kind="execution_rejection")[0]["body"]["reason"] == "FULL_SIZE_REQUIRED"


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("reason", ["HARD_STOP", "TAKE_PROFIT", "INVALIDATION"])
def test_sell_commits_all_depth_and_survives_trigger_clear(store, config, market, now, side, reason):
    c = full(config, fixed_stop_price=0.62, take_profit=0.99, exit_probability=0.70)
    ex = held(store, market, now, c, side)
    price = ".60" if reason == "HARD_STOP" else ".99" if reason == "TAKE_PROFIT" else ".90"
    p = {"conservative_" + side: 0.5 if reason == "INVALIDATION" else 0.99}
    ex.monitor(market, quote(now, [(price, 10)], side), p, now, "intent")
    assert ex.positions[market.ticker].exit_reason == reason
    # Even when the probability/price trigger clears, sell highest bids first.
    ex.monitor(
        market,
        quote(now + 1, [(".89", 3), (".88", 3)], side),
        {"conservative_" + side: 0.99},
        now + 1,
        "partial",
    )
    assert ex.positions[market.ticker].quantity == 4
    assert ex.positions[market.ticker].exit_reason == reason
    ex.monitor(
        market,
        quote(now + 2, [(".89", 3), (".88", 3)], side),
        {"conservative_" + side: 0.99},
        now + 2,
        "same-depth",
    )
    assert ex.positions[market.ticker].quantity == 4
    # Checkpoint recovery retains intent and consumed depth.
    from btc15.execution import PaperExecutor

    recovered = PaperExecutor(store, "run", "PAPER", ex.config)
    recovered.restore(ex.snapshot())
    recovered.monitor(
        market,
        quote(now + 3, [(".89", 3), (".88", 3), (".87", 4)], side),
        {"conservative_" + side: 0.99},
        now + 3,
        "new-depth",
    )
    assert market.ticker not in recovered.positions
    assert sorted((f["price"], f["quantity"]) for f in sells(store)) == [(0.87, 4), (0.88, 3), (0.89, 3)]
    assert all(f["reason"] == reason for f in sells(store))


def test_default_identity_and_validation():
    with pytest.raises(ValueError, match="aggressive fixed-contract"):
        Strategy(full_position_execution=True)


def test_full_entry_rolls_back_if_matching_is_interrupted(store, config, market, now, monkeypatch):
    ex = ready(store, market, now, full(config))
    book = make_book("yes", ".89", ".90", now)
    ex.submit(market, book, decision(), "op", now, True)
    book.no = {D(".100"): D(3), D(".099"): D(7)}
    book.received = book.source_time = now + 1
    original = ex.fill
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs) if calls == 1 else False

    monkeypatch.setattr(ex, "fill", interrupted)
    ex.aggressive(market, book, now + 1)
    assert not ex.positions and not store.list(kind="fill")
    assert ex.orders[market.ticker].remaining == 10
    assert not ex.orders[market.ticker].active
