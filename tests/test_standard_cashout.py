from dataclasses import replace

import pytest
from test_exit_execution_v2 import held, quote, sells

from btc15.domain import D
from btc15.execution import PaperExecutor
from btc15.strategies.settlement_edge.rules import FeeAccumulator


def cashout_position(store, market, now, config, side="yes"):
    ex = held(
        store,
        market,
        now,
        replace(config, standard_cashout_enabled=True, exit_probability=0.60),
        side,
    )
    ex.positions[market.ticker].cost = 7.50
    ex.positions[market.ticker].fees = 0.1313
    return ex


def observe(ex, market, at, event, levels=None, probability=0.99, received=None):
    side = ex.positions[market.ticker].side
    ex.monitor(
        market,
        quote(at if received is None else received, levels or [(".88", 10)], side),
        {} if probability is None else {"conservative_" + side: probability},
        at,
        event,
    )


@pytest.mark.parametrize("side", ["yes", "no"])
def test_net_fees_target_and_cashout_precedes_probability_exit(store, market, now, config, side):
    ex = cashout_position(store, market, now, config, side)
    observe(ex, market, now + 1, "price-gain-only", [(".85", 10)])
    observe(ex, market, now + 2, "still-below-net-target", [(".87", 10)])
    assert not ex.positions[market.ticker].exit_reason
    # No raw model probability/reference confirmations are required for cashout.
    observe(ex, market, now + 3, "cashout-before-invalidation", probability=0.59)
    intent = store.list(kind="exit_intent")[0]["body"]
    assert intent["reason"] == "STANDARD_CASHOUT"
    audit = intent["decision"]["standard_cashout"]
    assert audit["sell_limit"] == 0.88
    assert audit["min_net_profit"] == 1.0
    assert audit["net_profit_per_contract"] == 0.10
    assert audit["time_remaining"] == 297
    observe(ex, market, now + 3.25, "cashout-fill", probability=0.99)
    assert sells(store)[0]["reason"] == "STANDARD_CASHOUT"
    assert store.list(kind="trade_result")[0]["body"]["net_pnl"] == pytest.approx(1.0947)


def test_exact_net_threshold_and_entry_quantity(store, market, now, config):
    ex = cashout_position(store, market, now, config)
    pos = ex.positions[market.ticker]
    pos.bought = pos.quantity = 3
    pos.cost = 3 * 0.75
    exit_fee = FeeAccumulator(config.fee_balance_precision).charge(0.88, 3, config.taker_fee_rate, "sell")
    pos.fees = 3 * 0.88 - exit_fee - pos.cost - 0.30
    observe(ex, market, now + 1, "exact", [(".88", 3)])
    assert ex.positions[market.ticker].exit_reason == "STANDARD_CASHOUT"
    observe(ex, market, now + 1.25, "fill", [(".88", 3)])
    assert store.list(kind="trade_result")[0]["body"]["net_pnl"] == pytest.approx(0.30)


@pytest.mark.parametrize("consumed", [False, True])
def test_full_unconsumed_depth_required_at_intent(store, market, now, config, consumed):
    ex = cashout_position(store, market, now, config)
    if consumed:
        ex.exit_consumed[(market.ticker, D(".88"))] = D(".01")
    levels = [(".88", 10 if consumed else 9.99), (".87", 10)]
    observe(ex, market, now + 1, "insufficient-depth", levels)
    assert not ex.positions[market.ticker].exit_reason
    assert not store.list(kind="exit_intent")


@pytest.mark.parametrize("remaining", [120, 119.999, 60])
def test_cashout_excluded_in_final_two_minutes(store, market, now, config, remaining):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, market.close_time - remaining, "outside-cashout-window")
    assert not ex.positions[market.ticker].exit_reason


def test_crossing_two_minute_boundary_cancels_cashout_and_restores_probability_exit(
    store, market, now, config
):
    ex = cashout_position(store, market, now, config)
    intent_at = market.close_time - 120.1
    observe(ex, market, intent_at, "last-window-intent")
    assert ex.positions[market.ticker].exit_reason == "STANDARD_CASHOUT"
    observe(ex, market, intent_at + 0.25, "window-ended", probability=0.59)
    assert not sells(store)
    assert ex.positions[market.ticker].exit_reason == "INVALIDATION"
    cancellations = store.list(kind="exit_cancelled")
    assert cancellations[0]["body"]["reason"] == "CASHOUT_WINDOW_ENDED"
    observe(ex, market, intent_at + 0.50, "risk-fill", probability=0.59)
    assert sells(store)[0]["reason"] == "INVALIDATION"


def test_checkpoint_latency_and_first_eligible_ioc_floor(store, market, now, config):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "intent")
    recovered = PaperExecutor(store, "run", "PAPER", ex.config)
    recovered.restore(store.load_checkpoint("run"))
    assert recovered.positions[market.ticker].value_limit == 0.88
    observe(recovered, market, now + 1.24, "too-early")
    observe(recovered, market, now + 1.30, "queued-book", received=now + 1.24)
    assert not sells(store)
    observe(recovered, market, now + 1.35, "price-below-floor", [(".87", 10)])
    assert not sells(store)
    assert not recovered.positions[market.ticker].exit_reason
    assert store.list(kind="exit_cancelled")[0]["body"]["reason"] == "IOC_REMAINDER_EXPIRED"


def test_partial_cashout_accounts_for_all_proceeds_fees_and_original_bought_quantity(
    store, market, now, config
):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "intent")
    observe(ex, market, now + 1.25, "partial", [(".90", 4), (".87", 6)])
    assert [(row["quantity"], row["price"]) for row in sells(store)] == [(4, 0.90)]
    pos = ex.positions[market.ticker]
    assert pos.quantity == 6 and pos.bought == 10
    # The remainder is reconsidered with prior proceeds and fees, then must wait
    # another full latency interval before the new protected IOC can execute.
    assert pos.exit_reason == "STANDARD_CASHOUT"
    assert ex._exit_eligible[market.ticker] == now + 1.50
    assert ex.positions[market.ticker].value_limit == 0.86
    observe(ex, market, now + 1.50, "remaining-fill", [(".87", 6)])
    result = store.list(kind="trade_result")[0]["body"]
    assert result["net_pnl"] >= 1.0
    assert result["bought"] == 10


def test_hard_stop_overrides_committed_cashout_before_latency(store, market, now, config):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "cashout-intent")
    observe(ex, market, now + 1.1, "stop-before-cashout-eligible", [(".55", 10)], probability=None)
    assert ex.positions[market.ticker].exit_reason == "HARD_STOP"
    assert ex._exit_eligible[market.ticker] == now + 1.35
    observe(ex, market, now + 1.35, "stop-fill", [(".55", 10)], probability=None)
    assert sells(store)[0]["reason"] == "HARD_STOP"


def test_partial_cashout_remainder_retains_hard_stop(store, market, now, config):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "intent")
    observe(ex, market, now + 1.25, "partial", [(".88", 4), (".87", 6)])
    observe(ex, market, now + 1.30, "stop", [(".55", 6)], probability=None)
    assert ex.positions[market.ticker].exit_reason == "HARD_STOP"
    observe(ex, market, now + 1.55, "stop-fill", [(".55", 6)], probability=None)
    assert market.ticker not in ex.positions
    assert sum(row["quantity"] for row in sells(store)) == 10
    assert store.list(kind="trade_result")[0]["body"]["reason"] == "HARD_STOP"


def test_expired_cashout_restores_probability_exit(store, market, now, config):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "intent")
    observe(ex, market, now + 3.26, "late-data", [(".70", 10)], probability=0.59)
    assert not sells(store)
    assert ex.positions[market.ticker].exit_reason == "INVALIDATION"
    assert store.list(kind="exit_cancelled")[0]["body"]["reason"] == "IOC_DATA_TIMEOUT"


@pytest.mark.parametrize("price,expected", [(".99", "TAKE_PROFIT"), (".55", "HARD_STOP")])
def test_existing_price_exits_keep_priority(store, market, now, config, price, expected):
    ex = cashout_position(store, market, now, config)
    observe(ex, market, now + 1, "existing-exit", [(price, 10)], probability=0.59)
    assert ex.positions[market.ticker].exit_reason == expected


def test_expensive_entry_cannot_claim_unreachable_cashout(store, market, now, config):
    ex = cashout_position(store, market, now, replace(config, take_profit=None))
    ex.positions[market.ticker].cost = 9.50
    observe(ex, market, now + 1, "near-payout", [(".999", 10)])
    assert not ex.positions[market.ticker].exit_reason
