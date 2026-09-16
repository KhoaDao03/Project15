import asyncio
import json
import time
from dataclasses import replace

import pytest
import test_position_management as management_tests
from test_collection import fake_client, fake_socket

from btc15 import runner
from btc15.config import Settings

scenario = management_tests.scenario


def ingest_reference(s, now, event_id, price):
    s.e.connection = "test"
    s.clock[0] = now
    return s.e.ingest(
        dict(
            id=event_id,
            received=now,
            connection_id="test",
            payload=dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(dict(type="value", id="BRTI", time=now * 1000, value=price)),
                ),
            ),
        )
    )


@pytest.mark.parametrize("probability,filled", [(0.75, False), (0.99, True)])
def test_queued_reference_defers_ioc_then_revalidates(scenario, config, market, now, probability, filled):
    s = scenario(buy=False, chosen=replace(config, passive=False, resting_limit_recheck=True))
    s.e.process(now, "submit", "heartbeat", {})
    e = s.e.executor
    order = e.orders[market.ticker]
    # New reference has arrived, but worker is still on an earlier book event.
    e.latest_reference_receipt = ("new-reference", now + 0.2)
    s.clock[0] = now + 0.3
    s.e.process(now + 0.1, "old-book", "orderbook_delta", dict(market_ticker=market.ticker))
    assert order.active and not e.positions
    assert len(e.store.list(kind="entry_revalidation_wait", run_id=e.run_id)) == 1
    e.aggressive(market, s.e.books[market.ticker], now + 0.31)
    assert len(e.store.list(kind="entry_revalidation_wait", run_id=e.run_id)) == 1
    # Less than a second since calculation: processing this tick must refresh the model.
    s.p["conservative_yes"] = probability
    assert ingest_reference(s, now + 0.4, "new-reference", market.spec.strike + 50)
    assert bool(e.positions) == filled
    assert not e.orders[market.ticker].active
    assert s.e._model_cache[market.ticker][0] == now + 0.4
    if not filled:
        cancelled = e.store.list(kind="order", run_id=e.run_id)[-1]["body"]
        assert cancelled["status"] == "cancelled"


def test_new_reference_blocks_submission_without_consuming_budget(scenario, market, now):
    s = scenario(buy=False)
    e = s.e.executor
    e.latest_reference_receipt = ("pending", now)
    s.e.process(now, "candidate", "heartbeat", {})
    assert not e.orders and not e.risk.reserved
    assert e.risk.day(now)["trades"] == 0
    reasons = e.store.list(kind="execution_rejection", run_id=e.run_id)
    assert reasons[-1]["body"]["reason"] == "REFERENCE_UPDATE_PENDING"


def test_processed_reference_refreshes_cache_without_receipt_marker(scenario, market, now):
    s = scenario(buy=False)
    s.e.execute = False
    s.e.process(now, "initial", "heartbeat", {})
    s.p["conservative_yes"] = 0.75
    assert ingest_reference(s, now + 0.2, "replay-reference", market.spec.strike + 50)
    assert s.e.latest[market.ticker]["probability"]["conservative_yes"] == 0.75
    assert s.e.latest[market.ticker]["model_age_seconds"] == 0


def test_collector_marks_receipt_before_worker_applies_reference(
    store, config, tmp_path, raw, series, monkeypatch
):
    fake_client(monkeypatch, raw, series)
    now = time.time()
    fake_socket(
        monkeypatch,
        [
            dict(type="ticker", msg={}),
            dict(
                type="cfbenchmarks_value",
                msg=dict(
                    index_id="BRTI",
                    data=json.dumps(dict(type="value", id="BRTI", time=now * 1000, value=78000)),
                ),
            ),
        ],
    )
    original = runner.Engine.ingest
    observed = []

    def slow(self, row):
        payload = json.loads(row["payload"])
        if payload.get("type") == "ticker":
            time.sleep(0.1)
            observed.append(self.executor.pending_reference() is not None)
        result = original(self, row)
        if payload.get("type") == "cfbenchmarks_value":
            observed.append(self.executor.processed_reference_id == row["id"])
        return result

    monkeypatch.setattr(runner.Engine, "ingest", slow)
    asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store, duration=0.2))
    assert observed == [True, True]


def test_committed_order_fills_ten_after_probability_drops(scenario, config, market, now):
    c = replace(
        config, passive=False, resting_limit_recheck=True, revalidate_entry_signal=False, fixed_contracts=10
    )
    s = scenario(buy=False, chosen=c)
    s.e.process(now, "submit", "heartbeat", {})
    order = s.e.executor.orders[market.ticker]
    assert order.quantity == 10
    s.e.executor.latest_reference_receipt = ("new-reference", now + 0.2)
    s.p["conservative_yes"] = 0.60
    assert ingest_reference(s, now + 0.4, "new-reference", market.spec.strike - 50)
    assert s.e.executor.positions[market.ticker].quantity == 10
    assert s.e.executor.positions[market.ticker].side == "yes"
    assert not order.active


def test_committed_order_still_cancels_on_stale_reference(scenario, config, market, now):
    s = scenario(buy=False, chosen=replace(config, passive=False, revalidate_entry_signal=False))
    s.e.process(now, "submit", "heartbeat", {})
    order = s.e.executor.orders[market.ticker]
    s.e.executor.revalidate(
        market,
        dict(decision="NO_TRADE", side="yes", reasons=[]),
        now + 0.3,
        False,
        [dict(code="REFERENCE_RECEIVE_AGE")],
    )
    assert not order.active and not s.e.executor.positions
