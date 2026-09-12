import copy
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from btc15 import lease_identity
from btc15.storage import Store, leases


def test_dead_process_lease_recovered_without_changing_portfolio(store):
    saved = dict(positions={"held": {"quantity": 7}}, risk={"realized": -3, "reserved": {"held": 6}})
    store.checkpoint("paper-run", saved)
    record = store.add("trade_result", dict(net_pnl=-3), "paper-run", "PAPER", 100)
    # SIGKILL cannot run finally/release. This is a real separate process and DB.
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, signal, sys
from btc15.storage import Store
s = Store(sys.argv[1])
s.acquire('collector', 'killed-owner')
os.kill(os.getpid(), signal.SIGKILL)
""",
            str(store.engine.url),
        ],
        timeout=15,
        capture_output=True,
    )
    assert child.returncode == -9, child.stderr.decode()
    assert store.writer_owner() == "killed-owner"
    store.acquire("collector", "replacement")
    assert store.writer_owner() == "replacement"
    assert store.load_checkpoint("paper-run") == saved
    assert store.list(kind="trade_result")[0]["id"] == record
    assert len(store.list(kind="writer_recovered")) == 1
    # Delayed cleanup from the previous owner cannot release a replacement.
    store.release("collector", "killed-owner")
    assert store.writer_owner() == "replacement"
    assert store.read_market_display("lease:collector")["owner"] == "replacement"
    store.release("collector", "replacement")
    assert store.writer_owner() is None
    assert store.read_market_display("lease:collector") is None


def test_legacy_and_live_owner_are_never_reclaimed(store):
    with store.transaction() as c:
        c.execute(leases.insert().values(key="collector", owner="legacy"))
    with pytest.raises(RuntimeError, match="writer owns"):
        store.acquire("collector", "contender")
    store.release("collector", "legacy")
    store.acquire("collector", "live")
    with pytest.raises(RuntimeError, match="writer owns"):
        store.acquire("collector", "contender")
    assert not store.list(kind="writer_recovered")

    # Malformed identity is not proof of death, including a corrupted boot field.
    saved = store.read_market_display("lease:collector")
    for field, value in (("boot", 1), ("pid", True), ("start", "invalid")):
        store.publish_market_display(dict(saved, **{field: value}), "lease:collector")
        with pytest.raises(RuntimeError, match="writer owns"):
            store.acquire("collector", "contender")
        assert store.writer_owner() == "live"


def test_competing_recovery_allows_only_one_writer(store):
    identity = lease_identity.current_process()
    assert identity
    store.acquire("collector", "previous-boot")
    store.publish_market_display(
        dict(identity, owner="previous-boot", boot="different-boot"), "lease:collector"
    )
    # Construct stores sequentially; only the competing acquisitions are concurrent.
    contenders = [Store(str(store.engine.url)), Store(str(store.engine.url))]
    barrier = Barrier(2)

    def attempt(i):
        barrier.wait(timeout=5)
        try:
            contenders[i].acquire("collector", str(i))
            return True
        except RuntimeError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        assert sum(outcomes) == 1
        assert store.writer_owner() == str(outcomes.index(True))
        assert len(store.list(kind="writer_recovered")) == 1
    finally:
        for contender in contenders:
            contender.engine.dispose()


def test_failed_takeover_rolls_back_ownership_and_audit(store, monkeypatch):
    store.acquire("collector", "old")
    saved = dict(store.read_market_display("lease:collector"), boot="previous-boot")
    store.publish_market_display(saved, "lease:collector")
    publish = store.publish_market_display

    def fail(*args):
        publish(*args)
        raise OSError("injected metadata write failure")

    monkeypatch.setattr(store, "publish_market_display", fail)
    with pytest.raises(OSError, match="injected"):
        store.acquire("collector", "replacement")
    assert store.writer_owner() == "old"
    assert store.read_market_display("lease:collector") == saved
    assert not store.list(kind="writer_recovered")


@pytest.mark.parametrize(
    "case,dead",
    [
        ("live", False),
        ("boot", True),
        ("reuse", True),
        ("missing", True),
        ("permission", False),
        ("host", False),
        ("machine", False),
        ("namespace", False),
        ("legacy", False),
        ("unknown", False),
    ],
)
def test_process_identity_proof_is_fail_closed(case, dead, monkeypatch):
    current = dict(host="host", machine="machine", boot="boot", namespace="ns", pid=os.getpid(), start="1")
    saved = copy.deepcopy(current)
    monkeypatch.setattr(lease_identity, "process_start", lambda pid: "1")
    if case in ("host", "machine", "boot", "namespace"):
        saved[case] = "other"
    elif case == "reuse":
        saved["start"] = "0"
    elif case in ("missing", "permission"):

        def fail(pid):
            raise FileNotFoundError() if case == "missing" else PermissionError()

        monkeypatch.setattr(lease_identity, "process_start", fail)
    elif case == "legacy":
        saved = {}
    elif case == "unknown":
        current = None
    assert lease_identity.owner_is_dead(saved, current) is dead
