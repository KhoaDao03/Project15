import json
from dataclasses import replace

import pytest
from test_strategy_reverification import make_book

from btc15.analytics import metrics
from btc15.config import Strategy
from btc15.domain import SettlementSpecification
from btc15.engine import Engine
from btc15.strategies.settlement_edge.model import Tick, probability
from btc15.strategies.settlement_edge.rules import evaluate


def setup_engine(store, market, config, start, side="yes"):
    c = replace(
        config,
        sustained_lead_enabled=True,
        late_entry_enabled=True,
        min_probability=0.85,
        min_edge=0.01,
        min_ev=0.01,
        passive=False,
        resting_limit_recheck=True,
    )
    e = Engine(store, c, "BACKTEST", record_evaluations=False)
    e.markets[market.ticker] = market
    e.books[market.ticker] = make_book(side, ".84", ".85", start)
    e.connection = "test"
    e.healthy = e.clock_ok = e.exchange_open = True
    e.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
    e.series_fee_changes = []
    e.fee_changes[market.event_ticker] = []
    for state in ("DISCOVER_MARKET", "VALIDATE_MARKET", "WARMUP"):
        e.state(market.ticker, state, start)
    above = (side == "yes") == (market.spec.comparison_operator in (">=", ">"))
    price = market.spec.strike + (100 if above else -100)
    e.ticks = [Tick(start - i, start - i, price) for i in range(400, 0, -1)]
    return e, price


def reference(e, market, when, price):
    e.books[market.ticker].received = when
    e.books[market.ticker].source_time = when
    assert e.ingest(
        dict(
            id=str(when),
            received=when,
            connection_id="test",
            payload=dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(dict(type="value", id="BRTI", time=when * 1000, value=price)),
                ),
            ),
        )
    )


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("remaining", [300, 40])
def test_five_samples_then_one_ioc_entry(store, market, config, side, remaining):
    start = market.close_time - remaining
    e, price = setup_engine(store, market, config, start, side)
    for i in range(4):
        reference(e, market, start + i, price)
        assert not e.executor.orders
    reference(e, market, start + 4, price)
    order = e.executor.orders[market.ticker]
    assert order.active
    record = e.store.list(kind="order", run_id=e.run_id)[0]["body"]
    assert record["entry_checks"]["entry_path"] == ("late_settlement" if remaining == 40 else "standard")
    e.process(start + 4.3, "eligible", "heartbeat", {})
    assert e.executor.positions[market.ticker].quantity == 5
    reference(e, market, start + 5, price)
    assert len(e.store.list(kind="order", run_id=e.run_id)) == 1
    e.settle(market.ticker, side, market.close_time + 1)
    result = metrics(store, "BACKTEST", e.run_id)
    path = "late_settlement" if remaining == 40 else "standard"
    assert result["calibration"]["n"] == 1
    assert result["pnl_groups"][f"entry_path:{path}"]["n"] == 1


def test_quotes_and_duplicate_seconds_do_not_confirm(store, market, config, now):
    e, price = setup_engine(store, market, config, now)
    reference(e, market, now, price)
    for i in range(1, 5):
        reference(e, market, now + i / 10, price)
        e.process(now + i / 10, str(i), "orderbook_delta", {"market_ticker": market.ticker})
    assert e.latest[market.ticker]["lead"]["confirmation_samples"] == 1
    assert not e.executor.orders


def test_gap_and_side_change_reset_confirmation(store, market, config, now):
    e, price = setup_engine(store, market, config, now)
    for i in range(3):
        reference(e, market, now + i, price)
    reference(e, market, now + 4, price)
    assert e.latest[market.ticker]["lead"]["confirmation_samples"] == 1
    reference(e, market, now + 5, market.spec.strike - 100)
    assert e.latest[market.ticker]["lead"]["confirmation_samples"] == 1
    assert not e.executor.orders


@pytest.mark.parametrize(
    "remaining,allowed,path",
    [
        (121, True, "standard"),
        (120, True, "late_settlement"),
        (16, True, "late_settlement"),
        (15, False, "late_settlement"),
        (14, False, "late_settlement"),
    ],
)
def test_entry_window_boundaries(market, config, remaining, allowed, path):
    c = replace(config, sustained_lead_enabled=True, late_entry_enabled=True)
    now = market.close_time - remaining
    b = make_book("yes", ".84", ".85", now)
    p = dict(
        conservative_yes=0.99,
        lead=dict(
            side="yes",
            lead_sigma=3,
            stressed_probability=0.99,
            confirmed_normal=True,
            confirmed_late=True,
            confirmation_samples=5,
        ),
    )
    d = evaluate(
        market,
        b,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == allowed
    assert d["entry_path"] == path


def test_latency_cannot_fill_after_late_cutoff(store, market, config):
    start = market.close_time - 19.1
    e, price = setup_engine(store, market, config, start)
    for i in range(5):
        reference(e, market, start + i, price)
    assert e.executor.orders[market.ticker].active
    e.process(market.close_time - 14.8, "too-late", "heartbeat", {})
    assert not e.executor.positions
    assert not e.executor.orders[market.ticker].active


def test_stress_preserves_known_samples_and_required_average(config):
    spec = SettlementSpecification("CF Benchmarks", "BRTI", 78000, 1000, 1060, ">=")
    ticks = [Tick(t, t, 78200) for t in range(1001, 1041)]
    normal = probability(spec, ticks, 1040, 0, config)
    stress = probability(spec, ticks, 1040, 0, config, price_shift=-100)
    assert normal["required_remaining_average"] == 77600
    assert normal["known_samples"] == stress["known_samples"] == 40
    assert normal["settlement_mean"] - stress["settlement_mean"] == pytest.approx(100 / 3)
    with pytest.raises(ValueError, match="Missing past"):
        probability(spec, ticks[:-1], 1040, 0, config)


def test_stress_blocks_otherwise_valid_entry(market, config, now):
    c = replace(config, sustained_lead_enabled=True)
    b = make_book("yes", ".84", ".85", now)
    p = dict(
        conservative_yes=0.99,
        lead=dict(
            side="yes", lead_sigma=3, stressed_probability=0.8, confirmed_normal=True, confirmation_samples=5
        ),
    )
    d = evaluate(
        market,
        b,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert d["decision"] == "NO_TRADE"
    assert d["conservative_probability"] == 0.8


def test_compatibility_and_validation():
    assert Strategy.load("config/settlement-edge-fill-taker-paper.json").version == "1b7ffae5ec144248"
    for changes in [
        dict(late_entry_enabled=True),
        dict(lead_confirmation_samples=1),
        dict(late_no_new_entry=120),
        dict(min_lead_sigma=3),
    ]:
        with pytest.raises(ValueError):
            replace(Strategy(), **changes)


def test_late_side_uses_settlement_average_not_last_price(config):
    from btc15.strategies.settlement_edge.model import lead_evidence

    spec = SettlementSpecification("CF Benchmarks", "BRTI", 78000, 1000, 1060, ">=")
    ticks = [Tick(t, t, 78200) for t in range(1001, 1040)] + [Tick(1040, 1040, 77990)]
    c = replace(config, sustained_lead_enabled=True, late_entry_enabled=True)
    p = probability(spec, ticks, 1040, c.volatility_floor, c)
    assert spec.favored(ticks[-1].price) == "no"
    evidence = lead_evidence(spec, ticks, 1040, dict(sigma=c.volatility_floor), p, c)
    assert evidence["side"] == "yes"
    assert evidence["adverse_move"] == 210


@pytest.mark.parametrize(
    "operator,side,direction", [(">=", "yes", 1), (">=", "no", -1), ("<=", "yes", -1), ("<=", "no", 1)]
)
def test_stress_direction_respects_comparator(config, operator, side, direction):
    from btc15.strategies.settlement_edge.model import lead_evidence

    spec = SettlementSpecification("CF Benchmarks", "BRTI", 78000, 1000, 1060, operator)
    ticks = [Tick(t, t, 78000 + direction * 100) for t in range(500, 599)]
    ticks.append(Tick(599, 599, 78000 + direction * 90))
    c = replace(config, sustained_lead_enabled=True)
    p = probability(spec, ticks, 599, c.volatility_floor, c)
    evidence = lead_evidence(spec, ticks, 599, dict(sigma=c.volatility_floor), p, c)
    assert evidence["side"] == side
    assert evidence["adverse_move"] == 10
    assert evidence["stressed_probability"] <= p["conservative_" + side]


@pytest.mark.parametrize("remaining,late_count,required", [(450, 5, 3), (40, 5, 5), (450, 2, 3), (40, 2, 2)])
def test_standard_and_late_confirmation_counts(store, market, config, remaining, late_count, required):
    c = replace(
        config, entry_window_start=480, lead_confirmation_samples=3, late_lead_confirmation_samples=late_count
    )
    start = market.close_time - remaining
    e, price = setup_engine(store, market, c, start)
    for i in range(required - 1):
        reference(e, market, start + i, price)
        assert not e.executor.orders
    reference(e, market, start + required - 1, price)
    assert e.executor.orders[market.ticker].active


@pytest.mark.parametrize("remaining,accepted", [(601, False), (600, True), (121, True), (120, False)])
def test_relaxed_standard_probability_and_price(market, config, remaining, accepted):
    c = replace(
        config,
        entry_window_start=600,
        min_entry_price=0.70,
        min_probability=0.80,
        late_min_probability=0.85,
        sustained_lead_enabled=True,
        late_entry_enabled=True,
    )
    now = market.close_time - remaining
    p = dict(
        conservative_yes=0.82,
        lead=dict(
            side="yes",
            lead_sigma=3,
            stressed_probability=0.82,
            confirmed_normal=True,
            confirmed_late=True,
            confirmation_samples=5,
        ),
    )
    d = evaluate(
        market,
        make_book("yes", ".70", ".71", now),
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
        c,
    )
    assert (d["decision"] == "TRADE_CANDIDATE") == accepted
    if remaining == 120:
        assert any(r["code"] == "MIN_PROBABILITY" and r["required"] == 0.85 for r in d["reasons"])
        assert d["effective_max_entry_price"] is None


def test_late_overrides_validate_and_preserve_old_hash():
    assert Strategy.load("config/settlement-edge-fill-taker-paper.json").version == "1b7ffae5ec144248"
    for kwargs in [
        dict(late_min_probability=1.1),
        dict(late_lead_confirmation_samples=1),
        dict(late_lead_confirmation_samples=2.5),
    ]:
        with pytest.raises(ValueError):
            replace(Strategy(), **kwargs)


@pytest.mark.parametrize("remaining", [300, 40])
def test_raw_entry_ignores_probability_deductions_but_keeps_costs(market, config, remaining):
    c = replace(
        config,
        sustained_lead_enabled=True,
        late_entry_enabled=True,
        entry_probability_deductions=False,
        min_edge=0.01,
        min_ev=0.01,
    )
    now = market.close_time - remaining
    p = dict(
        p_yes=0.96,
        conservative_yes=0.89,
        lead=dict(
            side="yes",
            lead_sigma=3,
            stressed_probability=0.70,
            confirmed_normal=True,
            confirmed_late=True,
            confirmation_samples=5,
        ),
    )
    args = (
        market,
        make_book("yes", ".92", ".93", now),
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=1, regime="NORMAL"),
        p,
        dict(score=100, reasons=[]),
        now,
    )
    d = evaluate(*args, c)
    assert d["decision"] == "TRADE_CANDIDATE"
    assert d["conservative_probability"] == 0.96
    assert d["entry_probability_basis"] == "raw"
    assert d["model_disagreement_penalty"] == 0
    assert 0 < d["net_ev"] < 0.03
    assert evaluate(*args, replace(c, entry_probability_deductions=True))["decision"] == "NO_TRADE"
    p["p_yes"] = 0.94
    assert evaluate(*args, c)["decision"] == "NO_TRADE"


@pytest.mark.parametrize("remaining", [300, 40])
def test_raw_probability_entry_can_fill_despite_low_stress(store, market, config, monkeypatch, remaining):
    import btc15.engine as module

    original = module.lead_evidence

    def weak_stress(*args, **kwargs):
        return {**original(*args, **kwargs), "stressed_probability": 0.1}

    monkeypatch.setattr(module, "lead_evidence", weak_stress)
    start = market.close_time - remaining
    e, price = setup_engine(store, market, replace(config, entry_probability_deductions=False), start)
    for i in range(5):
        reference(e, market, start + i, price)
    assert e.executor.orders[market.ticker].active
    e.process(start + 4.3, "eligible", "heartbeat", {})
    assert e.executor.positions[market.ticker].quantity == 5
    evidence = store.list(kind="order", run_id=e.run_id)[0]["body"]["entry_checks"]
    assert evidence["entry_probability_basis"] == "raw"
    assert evidence["lead"]["stressed_probability"] == 0.1
