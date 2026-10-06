import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_hourly_contracts import hourly  # noqa: F401
from test_manual_trading import venue  # noqa: F401

from btc15.config import Strategy
from btc15.domain import parse_market
from btc15.live_automation import LiveAutomation, LiveControl


@pytest.fixture
def hourly_live(hourly, venue, store, monkeypatch):  # noqa: F811
    import test_manual_trading

    import btc15.live_automation as module

    raw, series, c = hourly
    m = parse_market(raw, series)
    _, state, manual, _ = venue
    clock = [m.close_time - 300]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(module, "health", lambda *args: dict(healthy=True))
    worker = LiveAutomation(manual, {c.asset: dict(config=c, run_id="hourly")}, {c.asset: store})
    worker.running = True
    state["fill_count"] = "10.00"

    def select(ticker=m.ticker, **changes):
        monkeypatch.setattr(test_manual_trading, "TICKER", ticker)
        state["ticker"] = ticker
        market = dict(
            ticker=ticker,
            close_time=datetime.fromtimestamp(m.close_time, UTC).isoformat(),
            fresh=True,
            book=dict(yes_bid=0.89, yes_ask=0.90, no_bid=0.89, no_ask=0.90),
        )
        store.publish_market_display(
            dict(run_id="hourly", connected=True, published_at=clock[0], markets=[market])
        )
        d = dict(
            timestamp=clock[0],
            model_evaluated_at=clock[0],
            versions=dict(config=worker.members[c.asset]["config"].version),
            reasons=[],
            decision="TRADE_CANDIDATE",
            side="yes",
            probability=dict(p_yes=0.98, p_no=0.98),
            expected_fill_price=0.90,
            settlement_spec=dict(strike=m.spec.strike),
        )
        d.update(changes)
        store.publish_record(
            "evaluation", d, "hourly", "PAPER", clock[0], ticker, key="evaluation:hourly:" + ticker
        )
        control = dict(
            ticker=ticker,
            asset=c.asset,
            enabled=True,
            contracts=10,
            revision=1,
            paused=False,
            config_version=worker.members[c.asset]["config"].version,
            stop_price=0,
            close_time=m.close_time,
        )
        if not worker.assets():
            control = worker.configure(
                LiveControl(
                    ticker=ticker, enabled=True, contracts=10, revision=0, confirm="ENABLE_REAL_TRADING"
                )
            )
        else:
            worker.write(control)
        return control

    control = select()
    return worker, state, manual, control, clock, store, select


@pytest.mark.parametrize(
    "offset,ask,expected", [(None, 0.90, 0.96), (0.01, 0.90, 0.91), (0.01, 0.96, 0.96), (0.01, 0.905, 0.91), (0, 0.90, 0.90)]
)
def test_hourly_limit_and_blackout(hourly_live, offset, ask, expected):
    from btc15.entry_schedule import entry_blackout

    w, state, manual, control, clock, store, select = hourly_live
    asset = control["asset"]
    w.members[asset]["config"] = replace(w.members[asset]["config"], entry_limit_offset=offset)
    # Update the policy identity just as configuration activation does.
    policy = w.assets()[asset]
    policy["config_version"] = w.members[asset]["config"].version
    with manual.db() as db:
        db.execute("UPDATE live_assets SET body=? WHERE asset=?", (json.dumps(policy), asset))
    control = select(expected_fill_price=ask)
    assert entry_blackout(clock[0])
    side, limit, d = w.entry(control, clock[0])
    assert side == "yes" and limit == Decimal(str(expected))
    assert d["hourly_entry"]["signal_ask"] == ask
    assert d["hourly_entry"]["strike"] > 0 and d["hourly_entry"]["model_probability"] == 0.98


@pytest.mark.parametrize(
    "state,filled",
    [("submitting", "0"), ("unknown", "0"), ("accepted", "0"), ("complete", "1"), ("complete", "10")],
)
def test_live_event_cap_counts_pending_partial_and_filled(hourly_live, monkeypatch, state, filled):
    w, _, manual, control, clock, store, select = hourly_live
    event = control["ticker"].rsplit("-T", 1)[0]
    rows = [
        dict(
            request=dict(ticker=event + f"-T{i}", action="buy"),
            state=state,
            exchange_order=dict(fill_count_fp=filled),
        )
        for i in [1, 2]
    ]
    monkeypatch.setattr(manual, "rows", lambda *args, **kwargs: rows)
    with pytest.raises(HTTPException, match="EVENT_TRADE_LIMIT"):
        w.entry(control, clock[0])
    rows[0]["request"]["ticker"] = event.replace("0613", "0614") + "-T1"
    assert w.entry(control, clock[0])[0] == "yes"
    rows[0]["request"]["ticker"] = event + "-T1"
    rows[0].update(state="complete", exchange_order=dict(fill_count_fp="0"))
    assert w.entry(control, clock[0])[0] == "yes"


@pytest.mark.anyio
async def test_hourly_live_holds_and_records_fill(hourly_live):
    w, state, manual, control, clock, store, select = hourly_live
    await w.step_market(control)
    assert len(state["posts"]) == 1
    row = manual.rows()[0]
    obs = row["timing"]["hourly_entry"]
    assert obs["limit_sent"] == 0.96
    assert obs["signal_ask"] == 0.90
    assert obs["fill_price"] == 0.90 and obs["fill_minus_signal_ask"] == 0
    state["position"] = "10"
    snapshot = store.read_market_display()
    snapshot["markets"][0]["book"]["yes_bid"] = 0.01
    store.publish_market_display(snapshot)
    assert w.detect_stops() == []
    await w.step_market(control)
    assert len(state["posts"]) == 1
    restarted = LiveAutomation(manual, w.members, w.stores)
    restarted.running = True
    await restarted.step_market(control)
    assert len(state["posts"]) == 1


@pytest.mark.anyio
async def test_live_event_lock_prevents_third_concurrent_buy(hourly_live, monkeypatch):
    w, _, manual, control, clock, store, select = hourly_live
    event = control["ticker"].rsplit("-T", 1)[0]
    controls = [select(event + f"-T{i}") for i in [1, 2, 3]]
    # All three quote books must remain visible during the concurrent pass.
    snapshot = store.read_market_display()
    snapshot["markets"] = [dict(snapshot["markets"][0], ticker=c["ticker"]) for c in controls]
    store.publish_market_display(snapshot)
    orders = []
    monkeypatch.setattr(manual, "rows", lambda *args, **kwargs: orders)

    async def reserve(c, **kwargs):
        w.entry(c, clock[0])
        await asyncio.sleep(0)  # race between decision and durable reservation
        orders.append(dict(request=dict(ticker=c["ticker"], action="buy"), state="unknown"))

    monkeypatch.setattr(w, "_locked_step_market", reserve)
    results = await asyncio.gather(*(w.step_market(c) for c in controls), return_exceptions=True)
    assert len(orders) == 2
    assert isinstance(results[2], HTTPException) and "EVENT_TRADE_LIMIT" in results[2].detail


def test_all_candidates_cycled_in_first_qualified_then_volume_order(hourly_live):
    w, _, manual, control, clock, store, select = hourly_live
    event = control["ticker"].rsplit("-T", 1)[0]
    tickers = [event + f"-T{i}" for i in range(1, 9)]
    for t in tickers:
        select(t)
    candidates = {t: dict(since=clock[0], volume=i) for i, t in enumerate(tickers)}
    candidates[tickers[0]]["since"] -= 1
    store.publish_market_display(candidates, "hourly_candidates:hourly")
    cycled = [c["ticker"] for c in w.cycle_controls() if c["ticker"] in candidates]
    assert cycled == [tickers[0], *reversed(tickers[1:])]


def test_stale_per_strike_decision_rejected(hourly_live):
    w, _, _, c, clock, _, _ = hourly_live
    clock[0] += 2.01
    with pytest.raises(HTTPException, match="fresh matching"):
        w.entry(c, clock[0])


def test_fifteen_minute_still_has_blackout(hourly_live):
    w, _, _, c, clock, _, _ = hourly_live
    asset = c["asset"]
    w.members[asset]["config"] = Strategy.load("config/settlement-edge-eth-paper.json")
    with pytest.raises(HTTPException, match="ENTRY_TIME_BLACKOUT"):
        w.entry(c, clock[0])


@pytest.mark.anyio
async def test_hourly_history_and_global_guard_include_fills(hourly_live):
    from btc15.global_loss_guard import GlobalLossGuard
    from btc15.live_fallback import LiveFallbackStore

    w, state, manual, control, clock, store, select = hourly_live
    await w.step_market(control)
    ticker, asset = control["ticker"], control["asset"]
    store.add(
        "settlement", dict(result="yes"), "hourly", "PAPER", manual.rows()[0]["created_at"] + 3600, ticker
    )
    view = LiveFallbackStore(store, manual.path, "hourly", asset)
    other = LiveFallbackStore(store, manual.path, "hourly", w.members[asset]["config"].asset_spec.underlying)
    guard = GlobalLossGuard(manual, w.members, w.stores)
    try:
        assert view.list("trade_result")[0]["body"]["net_pnl"] == pytest.approx(0.97)
        assert other.list("trade_result") == []
        assert guard.calculate() == pytest.approx(0.97)
        w.global_loss_guard = guard
        guard.apply(-50, clock[0])
        assert not w.assets()[asset]["enabled"]
        with pytest.raises(HTTPException, match="all new live buys disabled"):
            w.entry(control, clock[0])
    finally:
        view.close_history()
        other.close_history()
        for history in guard.histories.values():
            history.close_history()


@pytest.mark.anyio
async def test_missing_fill_cost_is_not_recorded_as_zero(hourly_live):
    w, _, manual, control, _, _, _ = hourly_live
    await w.step_market(control)
    row = manual.rows()[0]
    del row["exchange_order"]["taker_fill_cost_dollars"]
    manual.save(row)
    observation = manual.row(row["id"])["timing"]["hourly_entry"]
    assert "fill_price" not in observation and "fill_minus_signal_ask" not in observation


def test_sync_only_current_hour_and_disabled_by_default(hourly_live):
    w, _, manual, control, clock, store, _ = hourly_live
    original = store.read_market_display()
    next_market = dict(
        original["markets"][0],
        ticker=control["ticker"].replace("0613", "0614"),
        close_time=datetime.fromtimestamp(control["close_time"] + 3600, UTC).isoformat(),
    )
    current = dict(original["markets"][0], ticker=control["ticker"].rsplit("-T", 1)[0] + "-T1")
    original["markets"].extend([current, next_market])
    store.publish_market_display(original)
    w.sync_markets()
    assert w.control(current["ticker"]) is not None and w.control(next_market["ticker"]) is None
    # New asset policy is absent unless the owner's configure action creates it.
    with manual.db() as db:
        db.execute("DELETE FROM live_assets")
        db.execute("DELETE FROM live_controls")
    fresh = LiveAutomation(manual, w.members, w.stores)
    fresh.sync_markets()
    assert fresh.assets() == {} and fresh.controls() == {}


def test_missing_hourly_signal_ask_blocks_entry(hourly_live):
    w, _, _, control, clock, _, select = hourly_live
    select(expected_fill_price=None)
    with pytest.raises(HTTPException, match="Hourly signal ask unavailable"):
        w.entry(control, clock[0])


def test_hourly_candidate_priority_is_first_signal_then_volume(hourly_live):
    w, _, _, control, _, store, _ = hourly_live
    event = control["ticker"].rsplit("-T", 1)[0]
    tickers = [event+"-T"+str(i) for i in (1, 2, 3)]
    for ticker in tickers:
        w.write(dict(control, ticker=ticker))
    store.publish_market_display({
        tickers[0]: dict(since=1, volume=10),
        tickers[1]: dict(since=2, volume=1000),
        tickers[2]: dict(since=1, volume=20),
    }, "hourly_candidates:hourly")
    assert [c["ticker"] for c in w.cycle_controls()][:3] == [tickers[2], tickers[0], tickers[1]]
