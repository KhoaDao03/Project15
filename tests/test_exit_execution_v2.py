from dataclasses import replace

import pytest
from test_execution import ready

from btc15.domain import Book, D
from btc15.execution import PaperExecutor, Position


def held(store, market, now, config, side="yes"):
    c = replace(config, hold_value_exit_enabled=False, latency_seconds=0.25, slippage=0.002)
    ex = ready(store, market, now, c)
    ex.state(market.ticker, "ORDER_PENDING", now, "op")
    ex.state(market.ticker, "POSITION_OPEN", now, "op")
    ex.positions[market.ticker] = Position("op", market.ticker, side, 10, 10, 9, 0.1, opened=now)
    return ex


def quote(when, levels, side="yes"):
    return Book(**{side: {D(p): D(q) for p, q in levels}}, received=when, source_time=when, valid=True)


def sells(store):
    return [r["body"] for r in store.list(kind="fill") if r["body"]["action"] == "sell"]


@pytest.mark.parametrize("side", ["yes", "no"])
def test_backlogged_snapshot_cannot_satisfy_latency(store, market, now, config, side):
    ex = held(store, market, now, config, side)
    p = {"conservative_" + side: 0.9}
    ex.monitor(market, quote(now + 1, [(".50", 10)], side), p, now + 1.1, "intent")
    eligible = now + 1.35
    # Snapshot predates intent as well as eligibility, but is processed afterward.
    ex.monitor(market, quote(now + 1.05, [(".50", 10)], side), p, eligible + 0.01, "queued")
    assert not sells(store)
    # Fresh recovery cancels, even when its mark lies within existing extrema.
    ex.monitor(market, quote(eligible + 0.02, [(".90", 10)], side), p, eligible + 0.02, "recovery")
    assert not ex.positions[market.ticker].exit_reason
    ex.monitor(market, quote(now + 2, [(".50", 10)], side), p, now + 2, "new-stop")
    assert ex._exit_eligible[market.ticker] == now + 2.25
    ex.monitor(market, quote(now + 2.249, [(".50", 10)], side), p, now + 2.3, "still-old")
    assert not sells(store)
    ex.monitor(market, quote(now + 2.25, [(".50", 10)], side), p, now + 2.31, "eligible")
    assert sells(store)[0]["price"] == 0.5


@pytest.mark.parametrize("side", ["yes", "no"])
def test_depth_prices_stress_and_accounting_are_separate(store, market, now, config, side):
    ex = held(store, market, now, config, side)
    p = {"conservative_" + side: 0.6}
    levels = [(".81", 6), (".80", 4)]
    ex.monitor(market, quote(now + 1, levels, side), p, now + 1, "intent")
    ex.monitor(market, quote(now + 1.25, levels, side), p, now + 1.25, "fill")
    fills = sorted(sells(store), key=lambda x: -x["price"])
    assert [(f["quantity"], f["price"]) for f in fills] == [(6, 0.81), (4, 0.8)]
    stress = sorted([r["body"] for r in store.list(kind="exit_stress")], key=lambda x: -x["displayed_price"])
    assert [s["stressed_price"] for s in stress] == [0.80, 0.79]
    result = store.list(kind="trade_result")[0]["body"]
    assert result["proceeds"] == pytest.approx(8.06)
    assert result["net_pnl"] == pytest.approx(8.06 - 9 - 0.1 - sum(f["fee"] for f in fills))
    assert all(f["book_received_at"] >= f["exit_eligible_at"] for f in fills)


def test_profit_target_fills_at_target_stress_does_not(store, market, now, config):
    ex = held(store, market, now, config)
    p = {"conservative_yes": 0.99}
    ex.monitor(market, quote(now + 1, [(".99", 10)]), p, now + 1, "intent")
    ex.monitor(market, quote(now + 1.25, [(".99", 10)]), p, now + 1.25, "fill")
    assert sells(store)[0]["price"] == 0.99
    stress = store.list(kind="exit_stress")[0]["body"]
    assert stress["stressed_price"] == 0.988
    assert stress["status"] == "NOT_FILLABLE_AT_LIMIT"
    assert stress["stressed_fee"] is None and stress["stressed_proceeds"] is None


def test_partial_fill_recovery_restart_preserves_inventory_and_consumed_depth(store, market, now, config):
    ex = held(store, market, now, config)
    p = {"conservative_yes": 0.9}
    for t, event in [(1, "intent"), (1.25, "partial")]:
        ex.monitor(market, quote(now + t, [(".5", 3)]), p, now + t, event)
    assert ex.positions[market.ticker].quantity == 7
    ex.monitor(market, quote(now + 1.3, [(".9", 10)]), p, now + 1.3, "recover")
    recovered = PaperExecutor(store, "run", "PAPER", ex.config)
    recovered.restore(store.load_checkpoint("run"))
    assert recovered.positions[market.ticker].quantity == 7
    assert not recovered.positions[market.ticker].exit_reason
    for t, event in [(2, "new"), (2.25, "same-depth")]:
        recovered.monitor(market, quote(now + t, [(".5", 3)]), p, now + t, event)
    assert sum(f["quantity"] for f in sells(store)) == 3
    recovered.monitor(market, quote(now + 2.3, [(".5", 10)]), p, now + 2.3, "growth")
    assert sum(f["quantity"] for f in sells(store)) == 10


def test_missing_model_does_not_claim_recovery_and_new_trigger_restarts_latency(store, market, now, config):
    ex = held(store, market, now, config)
    ex.monitor(market, quote(now + 1, [(".81", 10)]), {"conservative_yes": 0.6}, now + 1, "prob")
    ex.monitor(market, quote(now + 1.3, [(".81", 10)]), {}, now + 1.3, "missing")
    assert ex.positions[market.ticker].exit_reason == "INVALIDATION"
    assert not sells(store)
    ex.monitor(market, quote(now + 1.4, [(".5", 10)]), {}, now + 1.4, "stop")
    assert ex.positions[market.ticker].exit_reason == "HARD_STOP"
    assert ex._exit_eligible[market.ticker] == now + 1.65
    assert not sells(store)


def test_unavailable_stress_tick_cannot_block_primary_sale(store, market, now, config):
    ex = held(store, market, now, config)
    for t, event in [(1, "intent"), (1.25, "fill")]:
        ex.monitor(market, quote(now + t, [(".001", 10)]), {}, now + t, event)
    assert sells(store)[0]["price"] == 0.001
    assert store.list(kind="exit_stress")[0]["body"]["status"] == "NO_VALID_TICK"


def test_missing_probability_pending_exit_is_read_only(store, market, now, config, monkeypatch):
    ex = held(store, market, now, config)
    ex.monitor(market, quote(now + 1, [(".81", 10)]), {"conservative_yes": 0.6}, now + 1, "intent")

    def fail():
        raise AssertionError("Unchanged unavailable model must not copy portfolio")

    monkeypatch.setattr(ex, "snapshot", fail)
    ex.monitor(market, quote(now + 1.3, [(".81", 10)]), {}, now + 1.3, "unavailable")


def test_stress_and_cancellation_are_queryable_separately(store, market, now, config, tmp_path):
    from fastapi.testclient import TestClient

    from btc15.config import Settings
    from btc15.dashboard import create_app

    ex = held(store, market, now, config)
    for t, price in [(1, ".5"), (1.1, ".9"), (2, ".5"), (2.25, ".5")]:
        ex.monitor(market, quote(now + t, [(price, 10)]), {"conservative_yes": 0.9}, now + t, str(t))
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)), config=ex.config)) as client:
        for kind, count in [
            ("exit_stress", 1),
            ("exit_cancelled", 1),
            ("exit_intent", 2),
            ("trade_result", 1),
            ("fill", 1),
        ]:
            response = client.get("/api/records", params=dict(kind=kind, mode="PAPER", run_id="run"))
            assert response.status_code == 200
            assert len(response.json()["rows"]) == count
