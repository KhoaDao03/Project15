from dataclasses import replace

import pytest
import test_position_management as management_tests

scenario = management_tests.scenario


@pytest.mark.parametrize(
    "probability,trigger", [(0.1, "PROBABILITY_BELOW_EXIT_THRESHOLD"), (0.95, "HOLD_VALUE_BELOW_THRESHOLD")]
)
def test_exit_trigger_snapshot(scenario, config, market, now, probability, trigger):
    s = scenario(chosen=replace(config, take_profit=None, min_hold_ev=0.2))
    s.refresh(now + 1, ".90")
    e = s.e.executor
    e.audit_input = (now + 0.9, now + 0.8)
    e.monitor(market, s.e.books[market.ticker], {"conservative_yes": probability}, now + 1, "exit")
    records = e.store.list(kind="exit_intent", run_id=e.run_id)
    assert len(records) == 1
    d = records[0]["body"]["decision"]
    assert d["trigger"] == trigger
    assert d["conservative_probability"] == probability
    assert d["min_hold_ev"] == 0.2
    assert d["observation"]["processing_lag_seconds"] == pytest.approx(0.1)
    assert d["observation"]["reference_age_seconds"] == pytest.approx(0.2)
    e.monitor(market, s.e.books[market.ticker], {"conservative_yes": probability}, now + 1.01, "next")
    assert len(e.store.list(kind="exit_intent", run_id=e.run_id)) == 1


def test_monitoring_gap_is_edge_triggered(scenario, market, now):
    e = scenario().e
    for i in range(100):
        e.monitoring_state(market.ticker, now + i, False, ["STALE_BOOK"])
    e.monitoring_state(market.ticker, now + 100, True)
    e.monitoring_state(market.ticker, now + 101, True)
    records = e.store.list(kind="position_monitoring", run_id=e.run_id)
    assert [r["body"]["status"] for r in records] == ["interrupted", "resumed"]
    assert records[-1]["body"]["duration_seconds"] == 100


def test_rejection_summaries_flush_at_most_once_per_minute(scenario, now):
    e = scenario().e
    now = e._rejection_flush or now
    e.flush_rejections(now)
    e._rejection_summary[("market", "MIN_EV")] = dict(market="market", reason="MIN_EV", count=300)
    e.flush_rejections(now + 59)
    assert not e.store.list(kind="entry_rejection_summary", run_id=e.run_id)
    e.flush_rejections(now + 60)
    records = e.store.list(kind="entry_rejection_summary", run_id=e.run_id)
    assert len(records) == 1
    assert any(g["market"] == "market" and g["count"] == 300 for g in records[0]["body"]["groups"])
    assert not e._rejection_summary


def test_hold_comparison_does_not_change_realized_pnl(scenario, market, series, now):
    from test_settlement_recovery import proof

    s = scenario()
    e = s.e.executor
    s.refresh(now + 1, ".50")
    e.monitor(market, s.e.books[market.ticker], s.p, now + 1, "stop")
    s.refresh(now + 2, ".50")
    e.monitor(market, s.e.books[market.ticker], s.p, now + 2, "sold")
    assert not e.positions
    realized = e.risk.realized
    assert e.settle(market, "yes", market.close_time + 1, evidence=proof(market.raw, series)) == "SETTLED"
    comparison = e.store.list(kind="hold_to_settlement_comparison", run_id=e.run_id)
    assert len(comparison) == 1 and comparison[0]["body"]["hypothetical"] is True
    assert comparison[0]["body"]["hypothetical_net_pnl"] > comparison[0]["body"]["actual_net_pnl"]
    assert e.risk.realized == realized
    assert (
        e.settle(market, "yes", market.close_time + 2, evidence=proof(market.raw, series))
        == "ALREADY_SETTLED"
    )
    assert len(e.store.list(kind="hold_to_settlement_comparison", run_id=e.run_id)) == 1
