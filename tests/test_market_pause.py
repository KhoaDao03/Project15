"""Venue-pause regressions: real Engine/executor/SQLite, synthetic inputs."""

import copy
from dataclasses import replace

import pytest
import test_position_management as management_tests
from fastapi.testclient import TestClient
from test_settlement_recovery import proof

from btc15.config import Settings
from btc15.dashboard import create_app
from btc15.domain import Book, D
from btc15.engine import Engine
from btc15.execution import PaperExecutor
from btc15.storage import Store
from btc15.strategies.settlement_edge.model import Tick

scenario = management_tests.scenario


def make_book(side, bid, ask, now, depth="100"):
    held = {D(bid): D(depth)} if D(depth) else {}
    other = {D(1) - D(ask): D(100)}
    return Book(
        yes=held if side == "yes" else other,
        no=other if side == "yes" else held,
        received=now,
        source_time=now,
        valid=True,
    )


def emit(s, when, kind, msg, *, connection="VENUE"):
    s.clock[0] = when
    return s.e.ingest(
        dict(
            id=f"{kind}-{when}",
            received=when,
            monotonic_ns=int(when * 1e9),
            connection_id=connection,
            payload=dict(type=kind, msg=msg),
        )
    )


def venue(s, market, when, event="deactivated"):
    assert emit(s, when, "market_lifecycle_v2", dict(market_ticker=market.ticker, event_type=event))


def quote(s, market, when, bid=".88", depth="100", connection="VENUE"):
    reference = market.spec.strike + (200 if s.side == "yes" else -200)
    s.e.ticks = [Tick(when, when, reference)]
    ask = str(min(D(".999"), D(bid) + D(".02")))
    b = make_book(s.side, bid, ask, when, depth)
    assert emit(
        s,
        when,
        "orderbook_snapshot",
        dict(
            market_ticker=market.ticker,
            ts_ms=when * 1000,
            yes_dollars_fp=[[str(p), str(q)] for p, q in b.yes.items()],
            no_dollars_fp=[[str(1 - p), str(q)] for p, q in b.no.items()],
        ),
        connection=connection,
    )


def metadata(
    s, market, series, when, *, request_started_at=None, source="kalshi_rest", raw=None, clock_skew=0
):
    assert emit(
        s,
        when,
        "metadata",
        dict(
            markets=[copy.deepcopy(raw if raw is not None else market.raw)],
            series=copy.deepcopy(series),
            fee_changes={market.event_ticker: []},
            series_fee_changes=[],
            exchange_status=dict(trading_active=True),
            clock_skew=clock_skew,
            source=source,
            request_started_at=request_started_at,
        ),
    )


def submitted(store):
    return [r for r in store.list(kind="order", limit=None) if r["body"].get("status") == "submitted"]


def sells(store):
    return [r for r in store.list(kind="fill", limit=None) if r["body"]["action"] == "sell"]


def setup(scenario, mode="PAPER", side="yes", buy=False):
    s = scenario(mode, side, buy=buy)
    s.e.connection = "VENUE"
    return s


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_lifecycle_deactivation_does_not_allow_reentry(scenario, store, market, now, mode, side):
    s = setup(scenario, mode, side)
    venue(s, market, now)
    quote(s, market, now + 0.1)
    assert not submitted(store), "A quote must not independently undo venue deactivation"
    assert not store.list(kind="fill")
    assert "VENUE_PAUSED" in [r["code"] for r in s.e.latest[market.ticker]["reasons"]]
    assert market.ticker in store.load_checkpoint(s.e.run_id)["venue_pauses"]


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_pause_cancels_remainder_and_blocks_price_exit(scenario, store, market, now, mode, side):
    s = setup(scenario, mode, side, buy=True)
    before = copy.deepcopy(s.e.executor.risk.reserved)
    venue(s, market, now + 1)
    assert not s.e.executor.orders[market.ticker].active
    for i in (2, 3):
        quote(s, market, now + i, ".50")
        s.e.executor.monitor(market, s.e.books[market.ticker], {}, now + i, f"direct-{i}")
    assert not sells(store)
    assert D(s.e.executor.positions[market.ticker].quantity) == D(".40")
    assert s.e.executor.risk.reserved == before
    assert len(store.list(kind="market_pause")) == 1


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_activation_requires_new_active_metadata_and_new_book(
    scenario, store, market, series, now, mode, side
):
    s = setup(scenario, mode, side)
    venue(s, market, now)
    quote(s, market, now + 0.1)
    venue(s, market, now + 1, "activated")
    quote(s, market, now + 1.1)
    assert not submitted(store)
    metadata(s, market, series, now + 2, request_started_at=now + 0.5)
    assert market.ticker in s.e.executor.venue_pauses  # Request predates activation hint.
    metadata(s, market, series, now + 3, request_started_at=now + 2.5)
    assert not s.e.executor.venue_pauses
    assert not s.e.books[market.ticker].valid
    assert not submitted(store)  # Confirmation is not a fresh executable book.
    quote(s, market, now + 3.1)
    assert len(submitted(store)) == 1
    assert len(store.list(kind="market_resume")) == 1


@pytest.mark.parametrize("request_case", [None, "old", True, "equal", "before", "future", "nan", "inf"])
def test_missing_stale_or_invalid_request_provenance_cannot_resume(
    scenario, store, market, series, now, request_case
):
    s = setup(scenario)
    venue(s, market, now)
    value = {
        "equal": now,
        "before": now - 1,
        "future": now + 5,
        "nan": float("nan"),
        "inf": float("inf"),
    }.get(request_case, request_case)
    metadata(s, market, series, now + 1, request_started_at=value)
    quote(s, market, now + 1.1)
    assert s.e.executor.venue_pauses and not submitted(store)


@pytest.mark.parametrize("source,skew", [(None, 0), ("cache", 0), ("kalshi_rest", 100)])
def test_untrusted_source_or_bad_clock_does_not_resume(scenario, store, market, series, now, source, skew):
    s = setup(scenario)
    venue(s, market, now)
    metadata(s, market, series, now + 1, request_started_at=now + 0.5, source=source, clock_skew=skew)
    assert s.e.executor.venue_pauses and not submitted(store)


def test_rest_inactive_also_persists_and_later_active_can_recover(scenario, store, market, series, now):
    s = setup(scenario)
    metadata(s, market, series, now, request_started_at=now - 1, raw={**market.raw, "status": "inactive"})
    quote(s, market, now + 0.1)
    assert s.e.executor.venue_pauses and not submitted(store)
    metadata(s, market, series, now + 1)  # Legacy/undated active response cannot clear it.
    quote(s, market, now + 1.1)
    assert s.e.executor.venue_pauses and not submitted(store)
    metadata(s, market, series, now + 2, request_started_at=now + 1.5)
    quote(s, market, now + 2.1)
    assert not s.e.executor.venue_pauses and len(submitted(store)) == 1


def test_new_pause_supersedes_inflight_active_poll(scenario, store, market, series, now):
    s = setup(scenario)
    venue(s, market, now)
    venue(s, market, now + 1)
    metadata(s, market, series, now + 2, request_started_at=now + 0.5)
    quote(s, market, now + 2.1)
    assert s.e.executor.venue_pauses[market.ticker]["since"] == now + 1
    assert not submitted(store)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_restart_preserves_pause_then_permits_verified_exit(
    scenario, store, market, series, now, tmp_path, mode, side
):
    s = setup(scenario, mode, side, buy=True)
    venue(s, market, now + 1)
    run = s.e.run_id
    url = str(store.engine.url)
    store.engine.dispose()
    reopened = Store(url)
    try:
        resumed = Engine(
            reopened,
            s.e.config,
            mode,
            run_id=run,
            resume=True,
            clock=lambda: s.clock[0],
            record_evaluations=False,
        )
        s.e = resumed
        resumed.connection = "VENUE"
        resumed.healthy = resumed.clock_ok = resumed.exchange_open = True
        assert resumed.executor.venue_pauses
        quote(s, market, now + 2, ".50")
        quote(s, market, now + 3, ".50")
        assert not sells(reopened)
        metadata(s, market, series, now + 4, request_started_at=now + 3.5)
        quote(s, market, now + 5, ".50")
        quote(s, market, now + 6, ".50")
        assert not resumed.executor.positions and not resumed.executor.risk.reserved
        assert len(reopened.list(kind="trade_result")) == 1
        assert sum(D(r["body"]["quantity"]) for r in sells(reopened)) == D(".40")
        with TestClient(
            create_app(reopened, settings=Settings(data_dir=str(tmp_path)), config=s.e.config)
        ) as client:
            response = client.get("/api/trades", params=dict(mode=mode, run_id=run))
            assert response.status_code == 200 and response.json()["total"] == 1
            audit = client.get("/api/records", params=dict(mode=mode, run_id=run, kind="market_pause"))
            assert audit.status_code == 200 and audit.json()["total"] == 1
    finally:
        reopened.engine.dispose()


def test_reconnect_and_old_active_metadata_do_not_release_pause(scenario, store, market, series, now):
    s = setup(scenario)
    venue(s, market, now)
    quote(s, market, now + 1, connection="NEW")
    assert s.e.executor.venue_pauses and not submitted(store)
    s.e.connection = "VENUE"
    metadata(s, market, series, now + 2, request_started_at=now - 1)
    assert s.e.executor.venue_pauses and not submitted(store)


@pytest.mark.parametrize("kind", ["rules", "identity", "malformed", "halted", "error"])
def test_active_confirmation_never_clears_metadata_or_unclassified_quarantine(
    scenario, store, market, series, now, kind
):
    s = setup(scenario, buy=True)
    venue(s, market, now + 1)
    raw = copy.deepcopy(market.raw)
    if kind == "rules":
        raw["floor_strike"] += 10
    elif kind == "identity":
        raw["event_ticker"] = "KXBTC15M-DIFFERENT"
    elif kind == "malformed":
        raw["floor_strike"] = None
    else:
        s.e.state(market.ticker, kind.upper(), now + 1.1)
    metadata(s, market, series, now + 2, raw=raw, request_started_at=now + 1.5)
    venue(s, market, now + 3, "activated")
    metadata(s, market, series, now + 4, request_started_at=now + 3.5)
    assert s.e.executor.venue_pauses
    assert s.e.executor.positions and not sells(store)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("outcome", ["yes", "no"])
def test_paused_positions_can_still_settle_once(scenario, store, market, series, now, mode, side, outcome):
    s = setup(scenario, mode, side, buy=True)
    venue(s, market, now + 1)
    assert (
        s.e.settle(market.ticker, outcome, market.close_time + 1, evidence=proof(market.raw, series, outcome))
        == "SETTLED"
    )
    assert (
        s.e.settle(market.ticker, outcome, market.close_time + 2, evidence=proof(market.raw, series, outcome))
        == "ALREADY_SETTLED"
    )
    assert not s.e.executor.venue_pauses and not s.e.executor.positions
    assert not s.e.executor.risk.reserved
    result = store.list(kind="trade_result")[0]["body"]
    assert len(store.list(kind="trade_result")) == 1
    cost = sum(r["body"]["price"] * r["body"]["quantity"] + r["body"]["fee"] for r in store.list(kind="fill"))
    assert result["net_pnl"] == pytest.approx((0.4 if side == outcome else 0) - cost)


def test_active_confirmation_does_not_clear_risk_halt_or_retry_cancelled_entry(
    scenario, store, market, series, now
):
    s = setup(scenario, buy=True)
    venue(s, market, now + 1)
    s.e.executor.halt(now + 1.1)
    metadata(s, market, series, now + 2, request_started_at=now + 1.5)
    assert not s.e.executor.venue_pauses and s.e.executor.risk.halted
    quote(s, market, now + 3, ".50")
    quote(s, market, now + 4, ".50")
    assert len(store.list(kind="trade_result")) == 1
    assert s.e.executor.risk.halted and len(submitted(store)) == 1
    assert s.e.executor.risk.day(now)["trades"] == 1


@pytest.mark.parametrize("event", ["closed", "determined"])
def test_terminal_lifecycle_restriction_cannot_be_released_by_activation_hint(
    scenario, store, market, series, now, event
):
    s = setup(scenario)
    venue(s, market, now, event)
    venue(s, market, now + 1, "activated")
    metadata(s, market, series, now + 2, request_started_at=now + 1.5)
    quote(s, market, now + 2.1)
    assert s.e.executor.venue_pauses[market.ticker]["event"] == event
    assert not submitted(store)


@pytest.mark.parametrize("operation", ["pause", "resume"])
def test_pause_and_resume_audit_checkpoint_write_is_atomic(
    scenario, store, market, now, monkeypatch, operation
):
    s = setup(scenario, buy=True)
    ex = s.e.executor
    if operation == "resume":
        ex.pause_market(market, now + 1, "deactivated")
    before = copy.deepcopy(ex.snapshot())
    checkpoint = store.load_checkpoint(s.e.run_id)
    count = len(store.list(limit=None))
    original = store.checkpoint

    def broken(*args, **kwargs):
        raise RuntimeError("Synthetic checkpoint failure")

    def action():
        if operation == "pause":
            ex.pause_market(market, now + 1, "deactivated")
        else:
            return ex.resume_market(
                market, now + 2, request_started_at=now + 1.5, source="kalshi_rest", clock_ok=True
            )

    monkeypatch.setattr(store, "checkpoint", broken)
    with pytest.raises(RuntimeError, match="Synthetic checkpoint"):
        action()
    assert ex.snapshot() == before
    assert store.load_checkpoint(s.e.run_id) == checkpoint
    assert len(store.list(limit=None)) == count
    monkeypatch.setattr(store, "checkpoint", original)
    action()
    assert bool(ex.venue_pauses) == (operation == "pause")


def test_legacy_checkpoint_defaults_and_market_specific_rejection(scenario, store, market, now):
    s = setup(scenario)
    old = s.e.executor.snapshot()
    old.pop("venue_pauses", None)
    restored = PaperExecutor(store, s.e.run_id, "PAPER", s.e.config)
    restored.restore(old)
    assert restored.venue_pauses == {}
    restored.pause_market(market, now, "deactivated")
    assert (
        restored.submit(
            market,
            s.e.books[market.ticker],
            dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.99),
            "refused",
            now + 0.1,
            True,
        )
        is None
    )
    row = store.list(kind="execution_rejection")[0]
    assert row["body"]["reason"] == "VENUE_PAUSED"
    assert row["body"]["details"]["pause"]["event"] == "deactivated"
    other = replace(market, ticker=market.ticker + "1")
    for state in (
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ):
        store.transition(s.e.run_id, "PAPER", other.ticker, state, now)
    order = restored.submit(
        other,
        s.e.books[market.ticker],
        dict(decision="TRADE_CANDIDATE", side="yes", conservative_probability=0.99),
        "other",
        now + 0.1,
        True,
    )
    assert order and order.market == other.ticker
