import copy

import pytest

from btc15.operational_state import operational_state


def status(now=100):
    return dict(
        run_id="current",
        timestamp=now,
        body=dict(
            run_id="current",
            connected=True,
            clock_ok=True,
            exchange_open=True,
            paper_execution=True,
            reference_age=0.2,
            processing_lag=0.01,
            queue_depth=2,
            queue_capacity=20000,
            models=[dict(run_id="current", entries_active=True, strategy_enabled=True)],
            recovery=dict(state="READY", entries_blocked=False, reasons=[]),
        ),
    )


def evaluation(run="current", stamp=100, decision="NO_TRADE", reasons=None):
    return dict(
        run_id=run,
        timestamp=stamp,
        body=dict(
            ticker="market",
            decision=decision,
            reasons=reasons or [dict(code="MIN_EDGE", message="Edge is below the configured minimum.")],
        ),
    )


@pytest.mark.parametrize(
    "case,expected,entry,reason_code",
    [
        ("missing", "BLOCKED", "UNKNOWN", "NO_STATUS"),
        ("stale", "BLOCKED", "UNKNOWN", "STALE_STATUS"),
        ("future", "BLOCKED", "UNKNOWN", "STALE_STATUS"),
        ("halt", "BLOCKED", "BLOCKED", "HALTED"),
        ("disabled", "BLOCKED", "BLOCKED", "STRATEGY_DISABLED"),
        ("paused", "BLOCKED", "BLOCKED", "MODEL_INACTIVE"),
        ("observe", "BLOCKED", "BLOCKED", "OBSERVE_ONLY"),
        ("startup", "BLOCKED", "UNKNOWN", "STARTUP_FAILED"),
        ("recovering", "RECOVERING", "BLOCKED", "WAITING_FOR_SEQUENCED_SNAPSHOT"),
        ("collecting", "COLLECTING", "CHECKING", "NO_FRESH_EVALUATION"),
        ("old_evaluation", "COLLECTING", "CHECKING", "NO_FRESH_EVALUATION"),
        ("other_run", "COLLECTING", "CHECKING", "NO_FRESH_EVALUATION"),
        ("warmup", "COLLECTING", "CHECKING", "WARMUP"),
        ("disconnected", "BLOCKED", "BLOCKED", "DISCONNECTED"),
        ("lag", "RECOVERING", "BLOCKED", "BACKLOG_NOT_DRAINED"),
    ],
)
def test_operational_states(case, expected, entry, reason_code):
    s, e, error = status(), evaluation(), None
    if case == "missing":
        s = None
    elif case == "stale":
        s["timestamp"] = 90
    elif case == "future":
        s["timestamp"] = 101
    elif case == "halt":
        s["body"]["halted"] = True
    elif case == "disabled":
        s["body"]["models"][0]["strategy_enabled"] = False
    elif case == "paused":
        s["body"]["models"][0]["entries_active"] = False
    elif case == "observe":
        s["body"]["paper_execution"] = False
    elif case == "startup":
        error = "failed"
    elif case == "recovering":
        s["body"]["recovery"] = dict(
            state="RECOVERING", entries_blocked=True, reasons=["WAITING_FOR_SEQUENCED_SNAPSHOT:market"]
        )
    elif case == "collecting":
        e = None
    elif case == "old_evaluation":
        e["timestamp"] = 80
    elif case == "other_run":
        e["run_id"] = "other"
    elif case == "warmup":
        e["body"]["reasons"] = [dict(code="WARMUP")]
    elif case == "disconnected":
        s["body"]["connected"] = False
    elif case == "lag":
        s["body"]["processing_lag"] = 3
        s["body"]["recovery"] = dict(
            state="RECOVERING", entries_blocked=True, reasons=["BACKLOG_NOT_DRAINED"]
        )
    before = copy.deepcopy((s, e))
    result = operational_state(s, e, 100, startup_error=error)
    assert result["state"] == expected and result["entry_status"] == entry
    assert reason_code in [r["code"] for r in result["reasons"]]
    assert (s, e) == before


def test_normal_rejection_is_evaluating_not_failed():
    result = operational_state(status(), evaluation(), 100)
    assert result["state"] == "EVALUATING" and result["entry_status"] == "WAITING"
    assert not result["reasons"]
    assert result["entry_reasons"][0]["code"] == "MIN_EDGE"
    candidate = evaluation(decision="TRADE_CANDIDATE")
    candidate["body"]["reasons"] = []
    assert operational_state(status(), candidate, 100)["entry_status"] == "CANDIDATE"


def test_receipt_recovery_overrides_worker_ready_only_for_same_fresh_run():
    receipt = dict(
        run_id="current",
        published_at=100,
        recovery=dict(entries_blocked=True, state="DRAINING", reasons=["PROCESSING_OVERLOAD"], warning=True),
    )
    result = operational_state(status(), evaluation(), 100, reference=receipt)
    assert result["state"] == "RECOVERING" and result["warnings"]
    receipt["run_id"] = "other"
    assert operational_state(status(), evaluation(), 100, reference=receipt)["state"] == "EVALUATING"
    receipt["run_id"] = "current"
    receipt["published_at"] = 90
    assert operational_state(status(), evaluation(), 100, reference=receipt)["state"] == "EVALUATING"


def test_expired_market_does_not_appear_ready():
    e = evaluation(decision="TRADE_CANDIDATE")
    e["body"]["market_close_timestamp"] = 99
    assert operational_state(status(), e, 100)["state"] == "COLLECTING"


def test_health_scopes_operational_decision_to_current_paper_run(store, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import btc15.dashboard as module
    from btc15.config import Settings
    from btc15.dashboard import create_app

    monkeypatch.setattr(module.time, "time", lambda: 100)
    s = status()
    store.add("status", s["body"], "current", "PAPER", 100)
    store.add("opportunity", evaluation()["body"], "current", "PAPER", 99)
    store.add(
        "opportunity",
        dict(decision="TRADE_CANDIDATE", ticker="unrelated", reasons=[]),
        "other",
        "BACKTEST",
        100,
    )
    store.add("status", dict(run_id="other", connected=True), "other", "BACKTEST", 101)
    before = store.list(limit=None)
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        response = client.get("/api/health").json()
        assert response["collector"]["run_id"] == "current"
        assert response["operational"]["entry_status"] == "WAITING"
        page = client.get("/").text
        assert "operational-state" in page and "operational-entry-reasons" in page
    assert store.list(limit=None) == before


def test_dashboard_rendering_and_stale_update_guard():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the dashboard status renderer")
    source = Path("src/btc15/static/app.js").read_text()
    block = source[source.index("let lastOperationalReceipt=") : source.index("function renderBotVersion(")]
    setup = """
const assert=require('node:assert/strict');
let now=100, watchdog;
const nodes=new Map();
const $=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},textContent:'',children:[],replaceChildren(...rows){this.children=rows;}});return nodes.get(id);};
const performance={now:()=>now};
const setInterval=fn=>watchdog=fn;
const text=(tag,value)=>({tag,textContent:value});
const metrics=(id,values)=>$(id).replaceChildren(...values);
const fmt=v=>String(v);
"""
    checks = """
renderOperational({state:'RECOVERING',summary:'Rebuilding fresh data.',run_id:'current',entry_status:'BLOCKED',reasons:[{message:'Waiting for snapshot',market:'<market>'}],warnings:[],entry_reasons:[],status_age:0,processing_lag:.1,queue_depth:2,queue_capacity:20000,reference_age:0,connected:true,recovery_phase:'RECOVERING'});
assert.equal($('operational-state').textContent,'RECOVERING');
assert.equal($('operational-reasons').children[0].textContent,'Waiting for snapshot · <market>');
assert.equal($('operational-entry').textContent,'Entries blocked');
now=6201;watchdog();
assert.equal($('operational-state').textContent,'UNKNOWN');
assert.equal($('operational-entry').textContent,'Entry readiness unknown');
assert.equal($('operational-metrics').children.length,0);
assert.equal($('bot-version').textContent,'Status unavailable');
"""
    subprocess.run([node, "-e", setup + block + checks], check=True, capture_output=True, text=True)


def test_health_warning_does_not_invent_an_execution_block():
    s = status()
    s["body"]["reference_age"] = 2.5
    result = operational_state(s, evaluation(), 100)
    assert result["state"] == "EVALUATING"
    assert result["entry_status"] == "WAITING"
    assert result["warnings"][0]["code"] == "REFERENCE_AGE_WARNING"


def test_current_receipt_disconnect_is_not_hidden_by_worker_status():
    result = operational_state(
        status(), evaluation(), 100, reference=dict(run_id="current", published_at=100, connected=False)
    )
    assert not result["connected"]
    assert result["state"] == "BLOCKED"


def test_persisted_failure_replaces_stale_state_but_not_live_status():
    failure = dict(run_id="replacement", timestamp=110, code="WRITER_LEASE_BLOCKED")
    result = operational_state(status(), evaluation(), 120, failure=failure)
    assert result["run_id"] == "replacement"
    assert result["reasons"][0]["code"] == "WRITER_LEASE_BLOCKED"
    assert result["entry_status"] == "UNKNOWN"
    assert result["entry_reasons"] == []
    for key in ("queue_depth", "queue_capacity", "processing_lag", "reference_age", "recovery_phase"):
        assert result[key] is None
    assert result["warnings"] == []
    # A duplicate start failure cannot override a live writer's recent heartbeat.
    assert operational_state(status(119), None, 120, failure=failure)["failure"] is None
    assert operational_state(status(), None, 105, failure=failure)["failure"] is None


def test_standalone_startup_failure_reaches_dashboard_then_clears(
    store, config, tmp_path, raw, series, monkeypatch
):
    import asyncio
    import time

    from fastapi.testclient import TestClient
    from test_collection import fake_client, fake_socket

    from btc15.config import Settings
    from btc15.dashboard import create_app
    from btc15.operation import health, serve

    fake_client(monkeypatch, raw, series)
    fake_socket(monkeypatch, [])
    settings = Settings(data_dir=str(tmp_path))
    store.acquire("collector", "active-owner")
    with pytest.raises(RuntimeError, match="writer owns"):
        asyncio.run(serve(settings, config, store, "service", 1, duration=0.1))
    assert store.writer_owner() == "active-owner"
    assert not store.list(kind="run")
    client = TestClient(create_app(store, settings=settings, config=config, run_id="service"))
    result = client.get("/api/health").json()
    assert result["operational"]["reasons"][0]["code"] == "WRITER_LEASE_BLOCKED"
    assert "database ownership" in result["collector_startup_error"]
    assert health(store, "service", time.time())["operational"]["failure"]
    assert health(store, "other", time.time())["operational"]["failure"] is None
    store.release("collector", "active-owner")
    asyncio.run(serve(settings, config, store, "service", 1, duration=0.1))
    assert client.get("/api/health").json()["operational"]["failure"] is None
    assert client.get("/api/health").json()["collector_startup_error"] is None
    assert len(store.list(kind="collector_failure")) == 1
