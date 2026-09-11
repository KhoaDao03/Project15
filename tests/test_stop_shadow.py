import pytest
from test_exit_execution_v2 import held, quote

from btc15.stop_shadow import ConfirmationExecutor, StopShadow


def candidate(store, market, now, config):
    original = held(store, market, now, config)
    ex = ConfirmationExecutor(store, "run", "PAPER", original.config)
    ex.restore(original.snapshot())
    return ex


def step(ex, market, now, elapsed, source, bid=".50", p=0.9, available=True, depth=10):
    ex.confirmation_monitor(
        market,
        quote(now + elapsed, [(bid, depth)]),
        {} if p is None else {"conservative_yes": p},
        now + elapsed,
        str(elapsed),
        now + source,
        available=available,
    )


def test_distinct_confirmations_and_execution_latency(store, market, now, config):
    ex = candidate(store, market, now, config)
    step(ex, market, now, 1, 1)
    step(ex, market, now, 1.1, 1)
    assert not ex.positions[market.ticker].exit_reason
    step(ex, market, now, 2, 2)
    assert ex._exit_eligible[market.ticker] == now + 2.25
    step(ex, market, now, 2.25, 2)
    assert not ex.positions


def test_timeout_does_not_need_a_third_reference(store, market, now, config):
    ex = candidate(store, market, now, config)
    step(ex, market, now, 1, 1)
    step(ex, market, now, 3, 1)
    assert ex.positions[market.ticker].exit_reason == "HARD_STOP"
    step(ex, market, now, 3.25, 1)
    assert not ex.positions


@pytest.mark.parametrize("condition", ["probability", "emergency", "unavailable", "gap"])
def test_immediate_bypasses_keep_latency_and_do_not_rearm(store, market, now, config, condition):
    ex = candidate(store, market, now, config)
    kw = {
        "probability": dict(p=0.6),
        "emergency": dict(bid=".40"),
        "unavailable": dict(p=None),
        "gap": dict(available=False),
    }[condition]
    step(ex, market, now, 1, 1, **kw)
    assert ex.positions[market.ticker].exit_reason
    step(ex, market, now, 1.25, 1)
    assert not ex.positions


def test_meaningful_recovery_resets_wait(store, market, now, config):
    ex = candidate(store, market, now, config)
    step(ex, market, now, 1, 1)
    step(ex, market, now, 1.2, 1, bid=".9", depth=0.01)
    assert market.ticker in ex.confirmations
    step(ex, market, now, 1.3, 1, bid=".9")
    assert market.ticker not in ex.confirmations
    step(ex, market, now, 2, 2)
    assert ex.confirmations[market.ticker]["count"] == 1
    assert not ex.positions[market.ticker].exit_reason


def test_reference_gap_falls_back_and_checkpoint_preserves_release(store, market, now, config):
    ex = candidate(store, market, now, config)
    step(ex, market, now, 1, 1)
    step(ex, market, now, 1.5, 3)
    restored = ConfirmationExecutor(store, "run", "PAPER", ex.config)
    restored.restore(store.load_checkpoint("run"))
    step(restored, market, now, 1.75, 3)
    assert not restored.positions


@pytest.mark.parametrize("primary_first", [False, True])
def test_shadow_mirrors_committed_purchase_and_keeps_primary_untouched(
    store, market, now, config, tmp_path, primary_first
):
    from types import SimpleNamespace

    from btc15.strategies.settlement_edge.model import Tick

    primary_ex = held(store, market, now, config)
    # Set up before the prospective purchase; existing positions are intentionally excluded.
    pos = primary_ex.positions.pop(market.ticker)
    primary = SimpleNamespace(
        executor=primary_ex,
        store=store,
        run_id="run",
        mode="PAPER",
        config=primary_ex.config,
        clock=None,
        markets={market.ticker: market},
        books={},
        ticks=[],
        healthy=True,
        clock_ok=True,
        exchange_open=True,
    )
    shadow = StopShadow(primary, tmp_path / "shadow.db")
    primary_ex.positions[market.ticker] = pos
    with pytest.raises(RuntimeError):
        with store.transaction():
            primary_ex.record(
                "fill",
                dict(action="buy", side="yes", quantity=10, price=0.9, fee=0.1),
                now,
                market.ticker,
                "op",
            )
            raise RuntimeError("rollback")
    shadow.process(dict(received=now, id="rolled-back"), dict(type="heartbeat", msg={}))
    assert not shadow.executor.positions
    primary_ex.record(
        "fill", dict(action="buy", side="yes", quantity=10, price=0.9, fee=0.1), now, market.ticker, "op"
    )
    shadow.process(dict(received=now, id="buy"), dict(type="heartbeat", msg={}))
    assert shadow.executor.positions[market.ticker].bought == 10
    assert len(store.list(kind="fill")) == 1

    def close_primary():
        primary_ex.positions[market.ticker].proceeds = 9.5
        primary_ex.positions[market.ticker].quantity = 0
        primary_ex.state(market.ticker, "EXITING", now + 0.5, "op")
        primary_ex.finish(market.ticker, now + 0.5, "TAKE_PROFIT")

    if primary_first:
        close_primary()
    before = primary_ex.snapshot()
    for t in (1, 1.25):
        primary.books[market.ticker] = quote(now + t, [(".99", 10)])
        primary.ticks = [Tick(now + t, now + t, market.spec.strike + 50)]
        shadow.process(
            dict(received=now + t, id=str(t)),
            dict(type="orderbook_snapshot", msg=dict(market_ticker=market.ticker)),
        )
    assert primary_ex.snapshot() == before
    assert not shadow.executor.positions
    if not primary_first:
        close_primary()
    shadow.process(dict(received=now + 2, id="result"), dict(type="heartbeat", msg={}))
    assert len(store.list(kind="trade_result")) == 1
    assert len(store.list(kind="stop_shadow_comparison")) == 1
    before_failure = primary_ex.snapshot()
    shadow._process = lambda *a: (_ for _ in ()).throw(RuntimeError("shadow-only failure"))
    shadow.process(dict(received=now + 3), {})
    assert shadow.failed
    assert primary_ex.snapshot() == before_failure
    assert any(r["body"]["status"] == "FAILED" for r in store.list(kind="stop_shadow_status"))
    shadow.store.engine.dispose()


def test_repeated_market_shadow_keeps_trades_separate(store, market, now, config):
    ex = ConfirmationExecutor(store, "shadow-cycles", "BACKTEST", config)

    def buy(op, stamp):
        return dict(
            id=op + "-fill",
            opportunity_id=op,
            timestamp=stamp,
            body=dict(action="buy", side="yes", quantity=10, price=0.9, fee=0.063),
        )

    ex.mirror_buy(buy("first", now), market)
    ex.mirror_buy(buy("overlap", now + 1), market)
    assert ex.positions[market.ticker].quantity == 10
    assert ex.positions[market.ticker].opportunity_id == "first"
    assert "overlap" in ex.excluded_trades
    resumed = ConfirmationExecutor(store, "shadow-cycles", "BACKTEST", config)
    resumed.restore(ex.snapshot())
    assert "overlap" in resumed.excluded_trades
    resumed.state(market.ticker, "EXITING", now + 2, "first")
    resumed.positions[market.ticker].quantity = 0
    resumed.positions[market.ticker].proceeds = 8
    resumed.finish(market.ticker, now + 2, "INVALIDATION")
    resumed.mirror_buy(buy("third", now + 3), market)
    assert resumed.positions[market.ticker].opportunity_id == "third"
    assert resumed.positions[market.ticker].quantity == 10
    assert len(store.list(kind="trade_result", run_id="shadow-cycles")) == 1
