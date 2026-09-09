import pytest
from fastapi.testclient import TestClient

from btc15 import engine as module
from btc15.config import Strategy
from btc15.dashboard import create_app
from btc15.domain import Book, D
from btc15.storage import market_display, records
from btc15.strategies.settlement_edge.model import Tick


@pytest.mark.parametrize("config", [Strategy()])
@pytest.mark.parametrize("fill_order", [False, True])
def test_only_filled_entry_is_retained(store, market, now, config, monkeypatch, fill_order):
    engine = module.Engine(store, config, execute=True, clock=lambda: clock[0], record_evaluations=False)
    clock = [now]
    ask = 0.90 if config.passive else 0.60
    engine.markets[market.ticker] = market
    book = Book(
        yes={D(ask) - D(".02"): D(100)}, no={1 - D(ask): D(100)}, received=now, source_time=now, valid=True
    )
    engine.books[market.ticker] = book
    engine.ticks = [Tick(now, now, market.spec.strike * 1.005)]
    engine.healthy = engine.clock_ok = engine.exchange_open = True
    engine.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
    engine.series_fee_changes = []
    engine.fee_changes = {market.event_ticker: []}
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        engine.state(market.ticker, state, now)
    monkeypatch.setattr(
        module,
        "features",
        lambda *a: dict(
            sigma=0.0001,
            reference=market.spec.strike * 1.005,
            history_seconds=3600,
            max_gap=0,
            shock=False,
            volatility_disagreement=0,
            regime="NORMAL",
        ),
    )
    p = dict(p_yes=0.5, p_no=0.5, conservative_yes=0.5, conservative_no=0.5)
    monkeypatch.setattr(module, "probability", lambda *a: dict(p))
    for i in range(10):
        engine.process(now + i / 100, str(i), "orderbook_delta", dict(market_ticker=market.ticker))
    assert not store.list(kind="opportunity")
    assert not store.list(kind="transition")
    assert engine.latest[market.ticker]["decision"] == "NO_TRADE"

    p.update(p_yes=0.995, conservative_yes=0.99)
    engine._model_cache.clear()
    clock[0] = now + 1
    engine.process(now + 1, "candidate", "orderbook_delta", dict(market_ticker=market.ticker))
    order = engine.executor.orders[market.ticker]
    assert store.list(kind="order")
    assert not store.list(kind="opportunity")
    assert order.entry_evidence["decision"] == "TRADE_CANDIDATE"
    assert store.load_checkpoint(engine.run_id)["orders"][market.ticker]["entry_evidence"]
    if not fill_order:
        engine.executor.cancel(market.ticker, now + 2, "test cancellation")
        assert not store.list(kind="opportunity") and not store.list(kind="fill")
        assert not store.load_checkpoint(engine.run_id)["orders"][market.ticker]["entry_evidence"]
        return

    # Entry evidence and the fill must commit together, including on retry.
    original_add = store.add

    def fail_fill(kind, *a, **kw):
        if kind == "fill":
            raise RuntimeError("fill write failed")
        return original_add(kind, *a, **kw)

    with monkeypatch.context() as patch:
        patch.setattr(store, "add", fail_fill)
        with pytest.raises(RuntimeError, match="fill write failed"):
            engine.executor.fill(order, 1, order.limit, now + 2, config.passive)
    assert not store.list(kind="opportunity") and not store.list(kind="fill")
    order = engine.executor.orders[market.ticker]
    assert order.entry_evidence is not None
    engine.executor.fill(order, 1, order.limit, now + 2, config.passive)
    if order.remaining:
        engine.executor.fill(order, order.remaining, order.limit, now + 2.1, config.passive)
    assert len(store.list(kind="opportunity")) == 1
    evidence = store.list(kind="opportunity")[0]
    assert evidence["id"] == order.opportunity_id
    assert evidence["body"]["timestamp"] == now + 1
    assert evidence["body"]["raw_archive"] is False
    engine.settle(market.ticker, "yes", market.close_time + 1)
    assert store.list(kind="trade_result")
    with TestClient(create_app(store)) as client:
        assert client.get("/api/replay/" + order.opportunity_id).status_code == 200


def test_live_projection_replaces_history_and_stays_visible(store):
    for i in range(20):
        store.publish_record("status", dict(connected=True), "run", "PAPER", i)
        store.publish_record(
            "evaluation", dict(decision="NO_TRADE", timestamp=i), "run", "PAPER", i, "market", str(i)
        )
    from sqlalchemy import func, select

    with store.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(records)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(market_display)).scalar() == 2
    assert store.list(kind="opportunity") == []
    assert store.list(kind="status", run_id="run")[0]["timestamp"] == 19
    assert store.list(kind="status", run_id="other") == []
    with TestClient(create_app(store)) as client:
        assert client.get("/api/evaluation?run_id=run").json()["record"]["body"]["decision"] == "NO_TRADE"
        assert client.get("/api/records").json()["total"] == 0
