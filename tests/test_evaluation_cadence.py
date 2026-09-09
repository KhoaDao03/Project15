from btc15 import engine as module
from btc15.config import Strategy
from btc15.domain import D
from btc15.strategies.settlement_edge.model import Tick


def test_one_second_models_and_intermediate_quote_checks(store, config, market, book, now, monkeypatch):
    assert Strategy().evaluation_interval == 1
    engine = module.Engine(store, config, "PAPER", execute=False)
    engine.markets[market.ticker] = market
    engine.books[market.ticker] = book
    engine.ticks = [Tick(now - 1, now - 1, market.spec.strike + 10), Tick(now, now, market.spec.strike + 10)]
    calculations, checks = [], []

    def features(*args):
        return dict(
            sigma=0.0001,
            reference=market.spec.strike + 10,
            history_seconds=1000,
            max_gap=0,
            shock=False,
            volatility_disagreement=0,
            regime="NORMAL",
        )

    def probability(*args):
        calculations.append(args[2])
        return dict(p_yes=0.5, conservative_yes=0.5, conservative_no=0.5)

    evaluate = module.evaluate

    def checked(*args):
        checks.append(args[6])
        return evaluate(*args)

    monkeypatch.setattr(module, "features", features)
    monkeypatch.setattr(module, "probability", probability)
    monkeypatch.setattr(module, "evaluate", checked)
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        store.transition(engine.run_id, "PAPER", market.ticker, state, now)
    engine.process(now, "first", "heartbeat", {})
    transitions = len(store.list(kind="transition", run_id=engine.run_id))
    for i in range(1, 51):
        engine.process(now + i / 1000, f"unchanged-{i}", "orderbook_delta", {"market_ticker": market.ticker})
    assert len(store.list(kind="transition", run_id=engine.run_id)) == transitions
    assert calculations == [now]
    checks.clear()
    checks.append(now)
    book.yes[D(".88")] = D("20")
    book.received = now + 0.2
    engine.process(now + 0.2, "quote", "orderbook_delta", {"market_ticker": market.ticker})
    assert calculations == [now]
    assert checks == [now, now + 0.2]
    engine.process(now + 1.01, "next", "heartbeat", {})
    assert calculations == [now, now + 1.01]
    rows = store.list(kind="opportunity", run_id=engine.run_id)
    assert rows[-1]["body"]["model_evaluated_at"] == now + 1.01
    assert rows[-1]["body"]["model_age_seconds"] == 0
    assert not store.list(kind="order")
