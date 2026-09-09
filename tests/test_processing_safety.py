import pytest

from btc15 import engine as module
from btc15.strategies.settlement_edge.model import Tick


def test_expired_market_transitions_once_and_still_settles(store, config, market, now, monkeypatch):
    engine = module.Engine(store, config, execute=False)
    engine.markets[market.ticker] = market
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        store.transition(engine.run_id, "PAPER", market.ticker, state, now)
    engine.process(market.close_time, "close", "heartbeat", {})
    assert store.state(engine.run_id, market.ticker) == "SETTLEMENT_PENDING"

    def unexpected(*args, **kwargs):
        raise AssertionError("Expired market repeated database/cancellation work")

    with monkeypatch.context() as patch:
        patch.setattr(store, "state", unexpected)
        patch.setattr(engine.executor, "cancel", unexpected)
        for i in range(1000):
            engine.process(market.close_time + i / 1000, str(i), "orderbook_delta", {})
    settled = []
    monkeypatch.setattr(engine.executor, "settle", lambda *args: settled.append(args))
    engine.settle(market.ticker, "yes", market.close_time + 2)
    assert len(settled) == 1


@pytest.mark.parametrize("delay", [0, 10, "during_model"])
@pytest.mark.parametrize("model", ["control", "momentum", "volatility"])
def test_processing_lag_cancels_resting_order_before_trade_fill(
    store, config, market, book, now, monkeypatch, delay, model
):
    from dataclasses import replace

    from test_execution import decision, ready

    from btc15.strategies.momentum import Momentum, volatility_model

    if model != "control":
        config = replace(Momentum() if model == "momentum" else volatility_model(), max_entry_price=0.99)
        monkeypatch.setattr(
            module,
            "observations",
            lambda *args: dict(
                return_180=0.001,
                candle_age=0,
                reference_source="CF Benchmarks BRTI",
                volatility_regime="NORMAL",
                volatility_samples=40,
            ),
        )
    clock = [now + 1 + (delay if isinstance(delay, int) else 0)]
    engine = module.Engine(store, config, run_id="run", clock=lambda: clock[0])
    engine.executor = ready(store, market, now, config)
    engine.markets[market.ticker] = market
    engine.books[market.ticker] = book
    engine.ticks = [Tick(now, now, market.spec.strike + 200)]
    engine.healthy = engine.clock_ok = engine.exchange_open = True
    engine.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
    engine.series_fee_changes = []
    engine.fee_changes = {market.event_ticker: []}
    order = engine.executor.submit(market, book, decision(), "op", now, True)
    monkeypatch.setattr(
        module, "features", lambda *args: dict(sigma=0.0001, volatility_disagreement=0, regime="NORMAL")
    )

    def probability(*args):
        if delay == "during_model":
            clock[0] += 10
        return dict(conservative_yes=0.99)

    monkeypatch.setattr(module, "probability", probability)
    monkeypatch.setattr(module, "quality", lambda *args: dict(score=100, reasons=[]))
    engine.process(
        now + 1,
        "delayed-trade",
        "trade" if model == "control" else "orderbook_delta",
        dict(
            market_ticker=market.ticker,
            trade_id="delayed",
            ts_ms=(now + 1) * 1000,
            taker_outcome_side="no",
            yes_price_dollars=str(order.limit),
            count_fp="100",
        ),
    )
    assert not order.active
    assert bool(engine.executor.positions) == (delay == 0)
    assert bool(store.list(kind="fill")) == (delay == 0)
    assert ("PROCESSING_LAG" in [r["code"] for r in engine.latest[market.ticker]["reasons"]]) == (delay == 10)


def test_reference_display_continues_while_analysis_is_blocked(
    store, config, tmp_path, raw, series, monkeypatch
):
    import asyncio
    import threading

    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings

    fake_client(monkeypatch, raw, series)
    fake_socket(
        monkeypatch,
        [dict(type="cfbenchmarks_value_5hz", msg=dict(index_id="BRTI", value_usd="79200", source_ts_ms=1))],
    )
    release = threading.Event()
    entered = threading.Event()
    ingest = runner.Engine.ingest

    def slow(self, row):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Test worker timed out")
        return ingest(self, row)

    monkeypatch.setattr(runner.Engine, "ingest", slow)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(
            runner.collect(Settings(data_dir=str(tmp_path)), config, store, stop_event=stop)
        )
        try:
            for _ in range(100):
                display = store.read_market_display("reference")
                if display and display.get("reference_5hz"):
                    break
                await asyncio.sleep(0.02)
            assert entered.is_set()
            assert display["connected"] and display["reference_5hz"]["value"] == "79200"
            assert not store.read_market_display()  # Analysis has not published a quote snapshot.
        finally:
            stop.set()
            release.set()
            await asyncio.wait_for(task, 5)
        assert not store.read_market_display("reference")["connected"]

    asyncio.run(run())


def test_overflow_preserves_rejected_frame_and_never_acknowledges_clean_stop(
    store, config, tmp_path, raw, series, monkeypatch
):
    import asyncio
    import json

    import pytest
    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.config import Settings
    from btc15.storage import read_events

    fake_client(monkeypatch, raw, series)
    marker = dict(type="ticker", msg=dict(test_overflow=True))
    fake_socket(monkeypatch, [marker])
    original = asyncio.Queue

    class FullOnMarker(original):
        def put_nowait(self, item):
            if item and json.loads(item["payload"]) == marker:
                raise asyncio.QueueFull
            super().put_nowait(item)

    monkeypatch.setattr(runner.asyncio, "Queue", FullOnMarker)
    with pytest.raises(ExceptionGroup):
        asyncio.run(
            runner.collect(Settings(data_dir=str(tmp_path)), config, store, paper=True, record_all=True)
        )
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl"))))
    assert any(row["payload"] == marker for row in rows)
    assert not store.list("shutdown_complete")
    assert store.writer_owner() is None


def test_observation_cache_matches_uncached_at_fractional_boundaries_gaps_and_updates():
    from btc15.strategies.momentum import Momentum, ObservationCache, observations

    config = Momentum()
    cache = ObservationCache()
    ticks = [Tick(6000 + i + 0.25, 6000 + i + 0.5, 79000 + (i % 9)) for i in range(3600) if i % 137 != 0]
    calls = []

    def measured(*args):
        calls.append(args[1])
        return observations(*args)

    for now in [9599.5, 9599.51, 9599.52, 9599.9, 9600, 9600.24, 9600.25, 9601.25, 9601.26, 9602.26, 9603.5]:
        assert cache.get(ticks, now, config, measured) == observations(ticks, now, config)
    assert len(calls) < 11
    ticks.append(Tick(9604.25, 9604.5, 79001))
    assert cache.get(ticks, 9604.3, config) == observations(ticks, 9604.3, config)
    assert cache.get(ticks, 9604.5, config) == observations(ticks, 9604.5, config)
    # Replaced history and backwards clock movement cannot reuse future results.
    ticks = ticks[-500:]
    for now in [9660, 9599.5]:
        assert cache.get(ticks, now, config) == observations(ticks, now, config)
