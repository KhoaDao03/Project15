import re
from dataclasses import replace
from types import MethodType

import test_position_management as management_tests

from btc15.domain import Book, dumps
from btc15.storage import states

scenario = management_tests.scenario


def add_closed(e, market, count):
    with e.store.transaction() as conn:
        for i in range(count):
            ticker = f"closed-{i}"
            e.markets[ticker] = replace(market, ticker=ticker)
            e.books[ticker] = Book()
            conn.execute(states.insert().values(run_id=e.run_id, market=ticker, state="CLOSED", version=1))


def test_closed_markets_are_not_requeried_per_event(scenario, market, now, monkeypatch):
    s = scenario()
    e = s.e
    add_closed(e, market, 1000)
    e.process(now + 0.6, "first", "heartbeat", {})
    original = e.store.state

    def state(run, ticker):
        assert not ticker.startswith("closed-"), "Historical market reentered the hot loop"
        return original(run, ticker)

    monkeypatch.setattr(e.store, "state", state)
    for i in range(20):
        e.process(now + 0.61, str(i), "heartbeat", {})
    assert len(e._processing_tickers) == 1
    assert len(e.markets) == 1001  # Late settlement/metadata identity remains available.


def test_local_routing_keeps_due_and_position_checks(scenario, market, now):
    e = scenario().e
    idle = replace(market, ticker="idle")
    e.markets[idle.ticker] = idle
    e.last_evaluation[idle.ticker] = now

    def tickers(t, kind, msg):
        return [k for k, _ in e.processing_markets(t, kind, msg)]

    assert tickers(now + 0.1, "orderbook_delta", {"market_ticker": market.ticker}) == [market.ticker]
    assert tickers(now + 0.1, "orderbook_delta", {"market_ticker": "idle"}) == [market.ticker, "idle"]
    assert tickers(now + 2, "trade", {"market_ticker": market.ticker}) == [market.ticker, "idle"]
    assert tickers(now + 0.1, "cfbenchmarks_value", {}) == [market.ticker, "idle"]
    assert tickers(market.close_time, "trade", {"market_ticker": market.ticker}) == [market.ticker, "idle"]


def normalize(value):
    return re.sub(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<id>", dumps(value))


def test_routing_preserves_records_and_portfolio(scenario, market, series, now):
    from test_settlement_recovery import proof

    outcomes = []
    for legacy in (True, False):
        s = scenario()
        e = s.e
        add_closed(e, market, 20)
        idle = replace(market, ticker="idle")
        e.markets["idle"] = idle
        e.books["idle"] = Book()
        e.last_evaluation["idle"] = now + 100
        if legacy:
            e.processing_markets = MethodType(lambda self, *args: list(self.markets.items()), e)
        for i, (kind, target, bid) in enumerate(
            [
                ("orderbook_delta", "unrelated", ".88"),
                ("heartbeat", "", ".88"),
                ("orderbook_snapshot", market.ticker, ".50"),
                ("orderbook_delta", market.ticker, ".50"),
                ("disconnect", "", ".50"),
            ],
            1,
        ):
            s.refresh(now + i, bid)
            e.process(now + i, str(i), kind, {"market_ticker": target})
        e.flush_rejections(now + 61, force=True)
        assert market.ticker not in e.executor.positions
        assert (
            e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series))
            == "SETTLED"
        )
        assert e.store.list(kind="hold_to_settlement_comparison", run_id=e.run_id)
        records = [
            dict(kind=r["kind"], timestamp=r["timestamp"], market=r["market"], body=r["body"])
            for r in e.store.list(run_id=e.run_id, limit=None)
            if r["kind"] != "run"
        ]
        outcomes.append((normalize(e.executor.snapshot()), sorted(normalize(r) for r in records)))
    assert outcomes[0] == outcomes[1]
