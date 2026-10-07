# ruff: noqa: F811
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_hourly_contracts import hourly  # noqa: F401

from btc15.domain import Book, parse_market
from btc15.execution import PaperExecutor


def candidate():
    return dict(
        decision="TRADE_CANDIDATE",
        side="yes",
        entry_path="standard",
        conservative_probability=0.95,
        lead=dict(side="yes", confirmed_normal=True, lead_sigma=3),
    )


def quote(now):
    book = Book()
    book.snapshot(dict(yes_dollars_fp=[[".89", "100"]], no_dollars_fp=[[".90", "100"]]), now)
    return book


def test_paper_event_cap_pending_killed_and_restart(hourly, store):
    raw, series, c = hourly
    m = parse_market(raw, series)
    now = m.close_time - 300
    ex = PaperExecutor(store, "hourly", "PAPER", c)

    def submit(ticker, n):
        mm = replace(m, ticker=ticker, event_ticker=ticker.rsplit("-T", 1)[0])
        if store.state("hourly", ticker) is None:
            for phase in [
                "DISCOVER_MARKET",
                "VALIDATE_MARKET",
                "WARMUP",
                "ENTRY_WINDOW",
                "EVALUATING",
                "TRADE_CANDIDATE",
            ]:
                store.transition("hourly", "PAPER", ticker, phase, now)
        return ex.submit(mm, quote(now), candidate(), str(n), now, True)

    tickers = [m.event_ticker + f"-T{i}" for i in [1, 2, 3]]
    for n, t in enumerate(tickers[:2]):
        assert submit(t, n) is not None
    assert submit(tickers[2], 3) is None
    assert store.list(kind="execution_rejection", limit=1)[0]["body"]["reason"] == "EVENT_TRADE_LIMIT"
    saved = ex.snapshot()
    ex = PaperExecutor(store, "hourly", "PAPER", c)
    ex.restore(saved)
    assert submit(tickers[2], 4) is None
    ex.cancel(tickers[0], now + 1, "killed")
    assert submit(tickers[2], 5) is not None
    ex.cancel(tickers[1], now + 1, "killed")
    ex.cancel(tickers[2], now + 1, "killed")
    assert submit(c.asset_spec.series + "-26OCT0614-T1", 6) is not None


@pytest.mark.anyio
async def test_hourly_settlement_recovery(hourly, store):
    from btc15.live_settlement_recovery import recover_live_settlements

    raw, series, c = hourly
    m = parse_market(raw, series)
    now = m.close_time + 70
    final = dict(
        raw, status="finalized", result="yes", settlement_ts=datetime.fromtimestamp(now - 1, UTC).isoformat()
    )

    class Client:
        async def get(self, path):
            return {"series": series} if path.startswith("series/") else {"market": final}

    @asynccontextmanager
    async def client():
        yield Client()

    manual = SimpleNamespace(
        client=client,
        rows=lambda: [
            dict(
                origin="bot",
                request=dict(ticker=m.ticker, action="buy"),
                exchange_order=dict(fill_count_fp="10"),
            )
        ],
    )
    args = (
        manual,
        {c.asset: dict(config=c, run_id="hourly")},
        {c.asset: store},
        {m.ticker: dict(asset=c.asset, close_time=m.close_time)},
        now,
    )
    assert await recover_live_settlements(*args) == [m.ticker]
    assert await recover_live_settlements(*args) == []


def test_candidate_publication_is_per_strike_and_invalidates(hourly, store):
    from btc15.hourly import publish_candidates

    raw, series, c = hourly
    m = parse_market(raw, series)
    now = m.close_time - 300
    tickers = [m.event_ticker + f"-T{i}" for i in range(7)]
    decisions = {t: dict(timestamp=now, decision="TRADE_CANDIDATE", ticker=t) for t in tickers}
    engine = SimpleNamespace(
        store=store,
        run_id="ladder",
        mode="PAPER",
        latest=decisions,
        _last_op={},
        markets={
            t: replace(m, ticker=t, raw=dict(m.raw, volume_fp=str(i * 100))) for i, t in enumerate(tickers)
        },
    )
    published = {}
    assert publish_candidates(engine, published)
    assert not publish_candidates(engine, published)
    for t in tickers:
        assert store.read_market_display("evaluation:ladder:" + t)["market"] == t
    assert len(store.read_market_display("hourly_candidates:ladder")) == 7
    engine.latest[tickers[0]] = dict(
        timestamp=now + 1, decision="NO_TRADE", reasons=[dict(code="BOOK_INVALID")]
    )
    engine.latest[tickers[1]]["timestamp"] = now + 1
    assert publish_candidates(engine, published)
    assert store.read_market_display("evaluation:ladder:" + tickers[0])["body"]["decision"] == "NO_TRADE"
    assert tickers[0] not in store.read_market_display("hourly_candidates:ladder")
    assert store.read_market_display("hourly_candidates:ladder")[tickers[1]]["since"] == now


def test_hourly_paper_disabled_stop(hourly, store):
    from test_exit_execution_v2 import held, quote, sells

    raw, series, c = hourly
    m = parse_market(raw, series)
    now = m.close_time - 300
    ex = held(store, m, now, c)
    for n, bid in enumerate([".40", ".01", "0"], 1):
        ex.monitor(m, quote(now + n, [(bid, 10)]), {"conservative_yes": 0}, now + n, "hold")
    assert not sells(store) and not store.list(kind="exit_intent")


def test_paper_filled_strikes_keep_event_slots(hourly, store):
    from btc15.execution import Position

    raw, series, config = hourly
    market = parse_market(raw, series)
    ex = PaperExecutor(store, "hourly", "PAPER", config)
    # Filled inventory restored independently still consumes event slots.
    for strike in (1, 2):
        ticker = market.event_ticker + f"-T{strike}"
        ex.positions[ticker] = Position(str(strike), ticker, "yes", quantity=10, bought=10)
    assert (
        ex.submit(market, quote(market.close_time - 300), candidate(), "third", market.close_time - 300, True)
        is None
    )
    assert store.list(kind="execution_rejection")[0]["body"]["reason"] == "EVENT_TRADE_LIMIT"


def test_paper_pass_uses_volume_priority(hourly, store):
    from btc15.engine import Engine

    raw, series, config = hourly
    market = parse_market(raw, series)
    engine = Engine(store, config, execute=False, run_id="hourly")
    for i, volume in [(1, 100), (2, 500), (3, 300)]:
        ticker = market.event_ticker + f"-T{i}"
        engine.markets[ticker] = replace(market, ticker=ticker, raw=dict(raw, volume_fp=str(volume)))
    assert [
        m.raw["volume_fp"]
        for _, m in engine.processing_markets(market.close_time - 300, "cfbenchmarks_value", {})
    ] == ["500", "300", "100"]


def test_hourly_manual_ticker_grammar(hourly):
    from btc15.manual_trading import TICKER

    raw, _, _ = hourly
    assert TICKER.fullmatch(raw["ticker"])
    assert not TICKER.fullmatch(raw["ticker"].replace("-T", "-B"))


def test_probability_display_updates_ineligible_strikes_without_changing_candidates(store):
    from btc15.config import Strategy
    from btc15.fleet import market_probability
    from btc15.hourly import publish_probability_display

    config = Strategy()
    member = dict(run_id="display", config=config)
    decision = dict(timestamp=100, ticker="held", decision="NO_TRADE", side="no",
                    conservative_probability=.9, probability=dict(p_yes=.1, p_no=.9),
                    versions=dict(config=config.version), reasons=[])
    engine = SimpleNamespace(store=store, run_id="display", latest={"held": decision})
    store.publish_market_display({"other": {"since": 99}}, "hourly_candidates:display")
    publish_probability_display(engine)
    record = store.read_market_display("probability_display:display")["held"]
    assert market_probability(record, member, dict(ticker="held", fresh=True), 101, True)["confidence"] == .9
    engine.latest["held"] = dict(decision, timestamp=102, conservative_probability=.85)
    publish_probability_display(engine)
    record = store.read_market_display("probability_display:display")["held"]
    assert market_probability(record, member, dict(ticker="held", fresh=True), 103, True)["confidence"] == .85
    assert not market_probability(record, member, dict(ticker="held", fresh=False), 103, True)["available"]
    assert not market_probability(record, member, dict(ticker="held", fresh=True), 110, True)["available"]
    assert store.read_market_display("hourly_candidates:display") == {"other": {"since": 99}}
    assert store.read_market_display("evaluation:display:held") is None
    engine.latest.clear()
    publish_probability_display(engine)
    assert store.read_market_display("probability_display:display") == {}
