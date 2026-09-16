"""Conditional entry veto; synthetic prices are not profitability evidence."""

import math
from dataclasses import replace

import pytest
import test_position_management as management_tests
from test_strategy_reverification import make_book

from btc15.strategies.settlement_edge.model import Tick, features
from btc15.strategies.settlement_edge.rules import evaluate

scenario = management_tests.scenario


@pytest.fixture
def entry(market, config, now):
    def decide(side="yes", offset=0, *, enabled=True, fresh=True, missing=False, late=False, quality=None):
        c = replace(
            config,
            bollinger_entry_filter_enabled=enabled,
            sustained_lead_enabled=late,
            late_entry_enabled=late,
        )
        when = market.close_time - 40 if late else now
        center = market.spec.strike + (100 if side == "yes" else -100)
        f = dict(
            reference=center + offset,
            volatility_disagreement=0,
            regime="NORMAL",
            bollinger=None if missing else dict(lower=center - 10, upper=center + 10),
            bollinger_fresh=fresh,
        )
        p = dict(
            conservative_yes=0.99,
            conservative_no=0.99,
            lead=dict(
                side=side,
                lead_sigma=3,
                stressed_probability=0.99,
                confirmed_late=True,
                confirmation_samples=5,
            ),
        )
        # A late settlement-average side can differ from the latest tick's side.
        tick_price = market.spec.strike - (center - market.spec.strike) if late else center
        return evaluate(
            market,
            make_book(side, ".88", ".90", when),
            Tick(when, when, tick_price),
            f,
            p,
            quality or dict(score=100, reasons=[]),
            when,
            c,
        )

    return decide


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("offset", [-11, -10, 0, 10, 11])
def test_directional_boundaries_and_causal_reference(entry, side, offset, late):
    result = entry(side, offset, late=late)
    blocked = (side == "yes" and offset > 10) or (side == "no" and offset < -10)
    assert result["side"] == side
    assert result["decision"] == ("NO_TRADE" if blocked else "TRADE_CANDIDATE")
    assert result["bollinger_entry_filter"]["status"] == ("rejected" if blocked else "allowed")
    assert [r["code"] for r in result["reasons"]] == (["BOLLINGER_EXTENSION"] if blocked else [])


@pytest.mark.parametrize(
    "options,status",
    [({"enabled": False}, "disabled"), ({"missing": True}, "unavailable"), ({"fresh": False}, "unavailable")],
)
def test_unavailable_or_disabled_keeps_original_entry_checks(entry, options, status):
    allowed = entry(offset=11, **options)
    assert allowed["decision"] == "TRADE_CANDIDATE"
    assert allowed["bollinger_entry_filter"]["status"] == status
    unhealthy = entry(offset=11, quality=dict(score=75, reasons=["STALE_REFERENCE"]), **options)
    assert unhealthy["decision"] == "NO_TRADE"
    assert {r["code"] for r in unhealthy["reasons"]} == {"STALE_REFERENCE", "MODEL_QUALITY"}


def test_boolean_opt_in_and_historical_hash(config):
    assert not config.bollinger_entry_filter_enabled
    assert replace(config, bollinger_entry_filter_enabled=False).version == config.version
    assert replace(config, bollinger_entry_filter_enabled=True).version != config.version
    for value in ("true", 1, None):
        with pytest.raises(ValueError, match="requires a boolean"):
            replace(config, bollinger_entry_filter_enabled=value)


def test_closed_minute_population_bands_ignore_current_candle_and_future_receipts(config):
    ticks = [Tick(t, t, 100 + t // 60) for t in range(1200)] + [Tick(1200, 1200, 150)]
    f = features(ticks, 1200.5, config)
    assert f["bollinger_fresh"] is True
    assert f["reference"] == 150
    assert f["bollinger"]["middle"] == 109.5
    assert f["bollinger"]["upper"] == pytest.approx(109.5 + 2 * math.sqrt(33.25))
    assert f["bollinger"]["lower"] == pytest.approx(109.5 - 2 * math.sqrt(33.25))
    future = [Tick(1201, 1200.4, 10000), Tick(1200.25, 1201, 10000)]
    assert features(ticks + future, 1200.5, config) == f


@pytest.mark.parametrize("gap", [range(1200, 1260), range(900, 960), range(60)])
def test_missing_latest_minute_internal_gap_and_insufficient_history_are_unavailable(config, gap):
    ticks = [Tick(t, t, 100 + t // 60) for t in range(1261) if t not in gap]
    when = 1200 if gap == range(60) else 1260
    f = features(ticks, when, config)
    assert f["bollinger_fresh"] is False
    if gap == range(1200, 1260):
        assert f["bollinger"] is not None  # Old diagnostic survives, entry veto must not use it.
    else:
        assert f["bollinger"] is None


def test_paper_rejection_audit_and_later_same_market_entry(scenario, config, market, now, store):
    s = scenario(buy=False, chosen=replace(config, bollinger_entry_filter_enabled=True))
    reference = s.f["reference"]
    s.f.update(bollinger_fresh=True, bollinger=dict(lower=reference - 20, upper=reference - 10))
    risk_before = s.e.executor.snapshot()["risk"]
    for i, bid in enumerate((".80", ".88", ".88")):
        s.refresh(now + i, bid)
        s.e.process(now + i, f"blocked-{i}", "orderbook_snapshot", dict(market_ticker=market.ticker))
    assert not s.e.executor.orders
    assert s.e.executor.snapshot()["risk"] == risk_before
    s.e.flush_rejections(now + 60)
    rows = store.list(kind="entry_rejection_summary")
    assert len(rows) == 1
    assert rows[0]["body"]["config_version"] == s.e.config.version
    group = next(g for g in rows[0]["body"]["groups"] if g["reason"] == "BOLLINGER_EXTENSION")
    assert group["count"] == 3 and group["otherwise_eligible_count"] == 2
    evidence = group["otherwise_eligible_sample"]
    assert evidence["snapshot_id"] == "blocked-1"
    assert evidence["bollinger_entry_filter"]["reference"] == reference
    assert evidence["bollinger_entry_filter"]["upper"] == reference - 10
    s.f["bollinger"]["upper"] = reference
    s.refresh(now + 61)
    s.e.process(now + 61, "inside", "orderbook_snapshot", dict(market_ticker=market.ticker))
    assert s.e.executor.orders[market.ticker].active


def test_risk_blocked_evaluation_is_not_counted_as_otherwise_eligible(scenario, config, market, now):
    s = scenario(buy=False, chosen=replace(config, bollinger_entry_filter_enabled=True))
    s.e.executor.risk.day(now)["pnl"] = -config.max_daily_loss
    s.f.update(bollinger_fresh=True, bollinger=dict(lower=0, upper=1))
    s.e.process(now, "risk-blocked", "orderbook_snapshot", dict(market_ticker=market.ticker))
    group = s.e._rejection_summary[(market.ticker, "BOLLINGER_EXTENSION")]
    assert "otherwise_eligible_count" not in group
    assert not s.e.executor.orders


def test_committed_ioc_keeps_its_entry_decision(scenario, config, market, now, store):
    c = replace(config, bollinger_entry_filter_enabled=True, passive=False, revalidate_entry_signal=False)
    s = scenario(buy=False, chosen=c)
    s.f.update(bollinger_fresh=True, bollinger=dict(lower=0, upper=s.f["reference"]))
    s.e.process(now, "submit", "orderbook_snapshot", dict(market_ticker=market.ticker))
    order = s.e.executor.orders[market.ticker]
    s.f["bollinger"]["upper"] -= 1
    s.refresh(now + 0.5)
    s.e.process(now + 0.5, "fill", "orderbook_snapshot", dict(market_ticker=market.ticker))
    assert s.e.latest[market.ticker]["bollinger_entry_filter"]["status"] == "rejected"
    assert s.e.executor.positions[market.ticker].quantity == order.quantity
    assert store.list(kind="opportunity")[0]["body"]["bollinger_entry_filter"]["status"] == "allowed"


@pytest.mark.parametrize(
    "bid,probability,reason",
    [(".50", 0.99, "HARD_STOP"), (".88", 0.1, "INVALIDATION"), (".995", 0.99, "TAKE_PROFIT")],
)
def test_entry_filter_does_not_disable_position_exits(
    scenario, config, market, now, store, bid, probability, reason
):
    s = scenario(chosen=replace(config, bollinger_entry_filter_enabled=True))
    s.f.update(bollinger_fresh=True, bollinger=dict(lower=0, upper=1))
    s.p["conservative_yes"] = probability
    for i in (1, 2):
        s.refresh(now + i, bid)
        s.e.process(now + i, str(i), "orderbook_snapshot", dict(market_ticker=market.ticker))
    assert s.e.latest[market.ticker]["bollinger_entry_filter"]["status"] == "rejected"
    assert not s.e.executor.positions
    assert store.list(kind="trade_result")[0]["body"]["reason"] == reason
