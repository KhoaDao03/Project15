import pytest
from fastapi import HTTPException
from research_helpers import records
from test_live_automation import live, venue  # noqa: F401


@pytest.fixture
def attach(research_recorder):
    def create(worker, root, **kwargs):
        log = research_recorder(
            root,
            asset="ETH",
            run_id="eth-paper",
            config=worker.members["ETH"]["config"],
            producer="live_executor",
            **kwargs,
        )
        worker.research_logs["ETH"] = log
        return log

    return create


@pytest.mark.anyio
async def test_entry_authorization_and_preflight_capture_exact_inputs(attach, live, tmp_path, monkeypatch):  # noqa: F811
    worker, state, manual, control, data, clock = live
    original = worker.stores["ETH"].read_market_display

    def read(key=None):
        result = original(key)
        if key:
            result["body"]["decision_id"] = "collector-decision"
        else:
            result["markets"][0]["book_version"] = {"input_id": "book-delta"}
        return result

    monkeypatch.setattr(worker.stores["ETH"], "read_market_display", read)
    log = attach(worker, tmp_path)
    try:
        await worker.step_market(control)
    finally:
        log.close()
    checks = [r["body"] for r in records(tmp_path) if r["kind"] == "decision_check"]
    by_stage = {c["stage"]: c for c in checks}
    assert {"live_entry", "live_entry_recheck", "live_authorization", "live_preflight"} <= by_stage.keys()
    assert all(c["accepted"] for c in checks)
    assert by_stage["live_entry"]["collector_decision_id"] == "collector-decision"
    assert by_stage["live_entry_recheck"]["book_snapshot"]["market"]["book_version"] == {
        "input_id": "book-delta",
    }
    assert by_stage["live_authorization"]["entry_check_id"] == by_stage["live_entry_recheck"]["decision_id"]
    assert by_stage["live_preflight"]["balance"] == {"balance": 10000}
    assert by_stage["live_preflight"]["holdings"] == {"yes": "0.00", "no": "0.00"}
    assert by_stage["live_preflight"]["market_response"]["status"] == "active"
    assert len(state["posts"]) == 1
    assert "test-signature" not in str(checks)


def test_rejected_entry_is_retained(attach, live, tmp_path):  # noqa: F811
    worker, state, manual, control, data, clock = live
    data["healthy"] = False
    log = attach(worker, tmp_path)
    try:
        with pytest.raises(HTTPException, match="Collector health"):
            worker.entry(control, clock[0])
    finally:
        log.close()
    check = records(tmp_path)[0]["body"]
    assert not check["accepted"]
    assert check["health"] == {"healthy": False}
    assert not state["posts"]


@pytest.mark.anyio
async def test_preflight_rejection_is_retained_without_submission(attach, live, tmp_path):  # noqa: F811
    worker, state, manual, control, data, clock = live
    state["balance_response"] = {"balance": 0}
    log = attach(worker, tmp_path)
    try:
        await worker.step_market(control)
    finally:
        log.close()
    check = next(r["body"] for r in records(tmp_path) if r["body"].get("stage") == "live_preflight")
    assert not check["accepted"] and check["balance"] == {"balance": 0}
    assert "Insufficient real cash" in check["reason"]
    assert not state["posts"]


@pytest.mark.anyio
async def test_recording_overflow_does_not_prevent_existing_execution(attach, live, tmp_path):  # noqa: F811
    worker, state, manual, control, data, clock = live
    log = attach(worker, tmp_path, max_queue_bytes=1)
    try:
        await worker.step_market(control)
    finally:
        log.close()
    assert len(state["posts"]) == 1
    assert log.status()["dropped"] >= 4
    assert not log.status()["capture_complete"]


@pytest.mark.anyio
async def test_execution_events_and_checkpoint_preserve_intermediate_states(attach, live, tmp_path):  # noqa: F811
    from btc15.execution_journal import read_events

    worker, state, manual, control, data, clock = live
    log = attach(worker, tmp_path)
    try:
        await worker.step_market(control)
        row = manual.rows()[0]
        worker.capture_execution_checkpoint()
        fill = dict(
            type="fill",
            msg=dict(
                client_order_id=row["id"],
                order_id=row["order_id"],
                trade_id="individual",
                market_ticker=row["request"]["ticker"],
                exchange_index=row["exchange_index"],
                subaccount=0,
                side="yes",
                action="buy",
                count_fp="1.00",
                yes_price_dollars=".9",
                fee_cost=".01",
                ts=10,
            ),
        )
        assert manual.receive_fill(fill)
        assert not manual.receive_fill(fill)
    finally:
        log.close()
    with manual.db() as db:
        events = list(read_events(db, limit=1000))
    kinds = [e["kind"] for e in events]
    assert kinds.index("order_requested") < kinds.index("submission_attempt") < kinds.index("acknowledgment")
    fills = [e for e in events if e["kind"] == "fill"]
    assert len(fills) == 1 and fills[0]["body"]["quantity"] == "1.00"
    assert fills[0]["body"]["fee_dollars"] == ".01"
    checkpoints = [e for e in events if e["kind"] == "execution_checkpoint_order"]
    assert checkpoints[0]["order"]["id"] == row["id"]
    assert "execution_checkpoint_complete" in kinds
    captured = [r["body"] for r in records(tmp_path) if r["kind"] == "execution_event"]
    assert fills[0]["event_id"] in {e["event_id"] for e in captured}
    assert len(state["posts"]) == 1
