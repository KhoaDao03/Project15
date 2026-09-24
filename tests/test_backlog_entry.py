"""Operator-authorized BTC backlog entries; other rejection paths stay enforced."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_live_automation import live, venue  # noqa: F401

from btc15 import backlog_entry


def report():
    return dict(
        healthy=False,
        status_age=0.1,
        reasons=["COLLECTOR_RECOVERING", "BACKLOG_WARNING", "PROCESSING_LAG", "STALE_REFERENCE"],
        status=dict(
            connected=True,
            clock_ok=True,
            exchange_open=True,
            live_signals=True,
            processing_lag=3.9,
            queue_depth=4215,
            recovery=dict(state="RECOVERING", reasons=["BACKLOG_NOT_DRAINED", "WAITING_FOR_FRESH_REFERENCE"]),
            research_logging=dict(error="OperationalError", dropped=500),
        ),
    )


@pytest.mark.parametrize("asset", ["BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI"])
def test_exception_is_btc_only(asset):
    assert backlog_entry.permitted(asset, report()) == (asset == "BTC")


@pytest.mark.parametrize(
    "reason",
    [
        "WAITING_FOR_FRESH_METADATA",
        "WAITING_FOR_CONNECTION",
        "WAITING_FOR_SEQUENCED_SNAPSHOT:BTC",
        "DATA_INTEGRITY_FAILURE",
        "BOOK_INVALID:BTC",
        "REFERENCE_STREAM_STALLED",
        "STOPPING",
    ],
)
def test_unrelated_recovery_cannot_be_bypassed(reason):
    r = report()
    r["status"]["recovery"]["reasons"].append(reason)
    assert not backlog_entry.permitted("BTC", r)


@pytest.mark.parametrize("field,value", [("status_age", 6), ("status_age", float("nan"))])
def test_current_status_required(field, value):
    r = report()
    r[field] = value
    assert not backlog_entry.permitted("BTC", r)


@pytest.mark.parametrize(
    "field,value",
    [
        ("connected", False),
        ("clock_ok", False),
        ("exchange_open", False),
        ("queue_depth", 0),
        ("processing_lag", 0.1),
        ("processing_lag", float("inf")),
        ("halted", True),
        ("settlement_recovery", {"pending": True}),
    ],
)
def test_logging_error_or_old_backlog_label_is_not_sufficient(field, value):
    r = report()
    r["status"][field] = value
    assert not backlog_entry.permitted("BTC", r)


def test_only_staleness_quality_penalty_is_removed():
    d = dict(
        reasons=[dict(code="STALE_BOOK"), dict(code="MODEL_QUALITY"), dict(code="MIN_PROBABILITY")],
        quality=dict(score=70, reasons=["STALE_BOOK"]),
    )
    assert backlog_entry.decision_reasons(d, SimpleNamespace(min_quality=85), True) == [
        dict(code="MIN_PROBABILITY")
    ]
    d["quality"]["score"] = 50
    assert dict(code="MODEL_QUALITY") in backlog_entry.decision_reasons(
        d, SimpleNamespace(min_quality=85), True
    )


@pytest.fixture
def backlog_live(live, monkeypatch):  # noqa: F811
    worker, state, manual, control, data, clock = live
    r = report()
    monkeypatch.setattr("btc15.live_automation.health", lambda *a: r)
    original = worker.stores[control["asset"]].read_market_display
    options = dict(valid=True, age=4.0, reason="COLLECTOR_RECOVERING")

    def read(key=None):
        result = original(key)
        if key:
            result["body"].update(
                timestamp=clock[0] - options["age"],
                model_evaluated_at=clock[0] - options["age"],
                reasons=[dict(code=options["reason"])],
            )
        else:
            result["published_at"] = clock[0] - options["age"]
            market = result["markets"][0]
            market.update(fresh=False, book_valid=options["valid"], backlog_book=market["book"], book={})
        return result

    monkeypatch.setattr(worker.stores[control["asset"]], "read_market_display", read)
    return (*live, r, options)


@pytest.mark.parametrize("live", ["BTC"], indirect=True)
def test_aged_btc_entry_passes_and_exit_quotes_stay_strict(backlog_live):
    worker, state, manual, control, data, clock, r, options = backlog_live
    side, limit, d = worker.entry(control, clock[0])
    assert side == "yes"
    assert d["backlog_entry_bypass"]["model_age"] == 4
    assert not r["healthy"]
    with pytest.raises(HTTPException, match="fresh connected"):
        worker.book(control, clock[0])


@pytest.mark.parametrize("live", ["BTC"], indirect=True)
@pytest.mark.parametrize(
    "reason",
    [
        "MIN_PROBABILITY",
        "REFERENCE_GAP",
        "SHOCK",
        "VENUE_PAUSED",
        "BOOK_INVALID",
        "FEED_UNHEALTHY",
        "MODEL_UNAVAILABLE",
        "LEAD_CONFIRMATION",
    ],
)
def test_strategy_and_integrity_failures_still_reject(backlog_live, reason):
    worker, state, manual, control, data, clock, r, options = backlog_live
    options["reason"] = reason
    with pytest.raises(HTTPException, match="Strategy entry filters"):
        worker.entry(control, clock[0])


@pytest.mark.parametrize("live", ["BTC"], indirect=True)
def test_invalid_book_and_future_decision_reject(backlog_live):
    worker, state, manual, control, data, clock, r, options = backlog_live
    options["valid"] = False
    with pytest.raises(HTTPException, match="fresh connected"):
        worker.entry(control, clock[0])
    options["valid"] = True
    options["age"] = -1
    with pytest.raises(HTTPException, match="fresh matching"):
        worker.entry(control, clock[0])


@pytest.mark.anyio
@pytest.mark.parametrize("live", ["BTC"], indirect=True)
async def test_real_submission_rechecks_bypass_and_journals_it(backlog_live):
    worker, state, manual, control, data, clock, r, options = backlog_live
    # Skip the subsequent position-management iteration; it still requires fresh books.
    state["fill_count"] = "0.00"
    await worker.step_market(control)
    assert len(state["posts"]) == 1
    policy = manual.rows()[0]["timing"]["backlog_entry_policy"]
    assert policy["initial"]["queue_depth"] == 4215
    assert policy["submission"]["model_age"] == 4


@pytest.mark.anyio
@pytest.mark.parametrize("live", ["BTC"], indirect=True)
async def test_recovery_during_preflight_restores_freshness_checks(backlog_live, monkeypatch):
    worker, state, manual, control, data, clock, r, options = backlog_live
    original = manual.holdings

    async def holdings(*a, **kw):
        result = await original(*a, **kw)
        r.update(healthy=True, reasons=[])
        r["status"]["queue_depth"] = 0
        return result

    monkeypatch.setattr(manual, "holdings", holdings)
    await worker.step_market(control)
    assert not state["posts"]
    assert "fresh matching" in manual.rows()[0]["message"]
