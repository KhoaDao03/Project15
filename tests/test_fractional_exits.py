from decimal import Decimal

import pytest

from btc15.domain import Book, D
from btc15.execution import PaperExecutor


def candidate(side):
    return dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.99)


def ready(store, market, now, config, mode):
    for state in (
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ):
        store.transition("exit-run", mode, market.ticker, state, now)
    return PaperExecutor(store, "exit-run", mode, config)


def exit_book(side, quantity, when):
    price = D(".50") if side == "yes" else D(".05")
    levels = {price: D(quantity)}
    return Book(
        yes=levels if side == "yes" else {},
        no=levels if side == "no" else {},
        received=when,
        source_time=when,
        valid=True,
    )


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_four_fractional_exits_close_exactly(store, market, book, now, config, mode, side):
    executor = ready(store, market, now, config, mode)
    order = executor.submit(market, book, candidate(side), "op", now, True)
    assert order is not None
    executor.fill(order, 0.40, order.limit, now + 1, True)
    assert D(executor.positions[market.ticker].quantity) == D(".40")

    probability = {"conservative_yes": 0.99, "conservative_no": 0.99}
    executor.monitor(market, exit_book(side, ".10", now + 2), probability, now + 2, "intent")
    assert not [r for r in store.list(kind="fill") if r["body"]["action"] == "sell"]

    expected_remaining = [D(".30"), D(".20"), D(".10"), D("0")]
    for index, (visible, remaining) in enumerate(
        zip((".10", ".20", ".30", ".40"), expected_remaining), start=3
    ):
        executor.monitor(
            market,
            exit_book(side, visible, now + index),
            probability,
            now + index,
            f"exit-{index}",
        )
        if remaining:
            assert D(executor.positions[market.ticker].quantity) == remaining
            checkpoint = store.load_checkpoint("exit-run")
            assert D(checkpoint["positions"][market.ticker]["quantity"]) == remaining

    sells = [r for r in store.list(kind="fill") if r["body"]["action"] == "sell"]
    assert [D(r["body"]["quantity"]) for r in sells] == [D(".10")] * 4
    assert sum((D(r["body"]["quantity"]) for r in sells), D(0)) == D(".40")
    assert market.ticker not in executor.positions
    assert market.ticker not in executor.risk.reserved
    assert store.state("exit-run", market.ticker) == "CLOSED"
    results = store.list(kind="trade_result")
    assert len(results) == 1
    assert D(results[0]["body"]["quantity"]) == D("0")
    checkpoint = store.load_checkpoint("exit-run")
    assert market.ticker not in checkpoint["positions"]
    assert D(checkpoint["exit_consumed"][0][2]) == D(".40")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_exit_depth_is_floored_never_rounded_up(store, market, book, now, config, mode):
    executor = ready(store, market, now, config, mode)
    order = executor.submit(market, book, candidate("yes"), "op", now, True)
    executor.fill(order, 0.02, order.limit, now + 1, True)
    probability = {"conservative_yes": 0.99, "conservative_no": 0.99}
    executor.monitor(market, exit_book("yes", ".019", now + 2), probability, now + 2, "intent")
    executor.monitor(market, exit_book("yes", ".019", now + 3), probability, now + 3, "sell")
    sells = [r for r in store.list(kind="fill") if r["body"]["action"] == "sell"]
    assert len(sells) == 1
    assert D(sells[0]["body"]["quantity"]) == D(".01")
    assert D(executor.positions[market.ticker].quantity) == D(".01")
    assert not store.list(kind="trade_result")


def test_legacy_exit_consumed_binary_drift_normalizes_on_restore(store, market, book, now, config):
    executor = ready(store, market, now, config, "PAPER")
    order = executor.submit(market, book, candidate("yes"), "op", now, True)
    executor.fill(order, 0.10, order.limit, now + 1, True)
    snapshot = executor.snapshot()
    snapshot["exit_consumed"] = [[market.ticker, ".50", 0.30000000000000004]]
    store.checkpoint("exit-run", snapshot)

    restored = PaperExecutor(store, "exit-run", "PAPER", config)
    restored.restore(store.load_checkpoint("exit-run"))
    assert restored.exit_consumed[(market.ticker, D(".50"))] == Decimal("0.30")
