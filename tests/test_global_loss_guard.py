# ruff: noqa: F811
from contextlib import nullcontext

import pytest
from fastapi import HTTPException
from test_live_automation import live, venue  # noqa: F401

from btc15.global_loss_guard import GlobalLossGuard
from btc15.live_automation import LiveControl


@pytest.mark.parametrize("pnl,trips", [(-49.9999, False), (-50, True), (-50.01, True)])
def test_global_cutoff_is_atomic_and_survives_restart(live, pnl, trips):
    worker, _, manual, control, _, clock = live
    worker.write_asset(dict(asset="BTC", enabled=True, revision=1, contracts=10))
    guard = GlobalLossGuard(manual, worker.members, worker.stores)
    worker.global_loss_guard = guard
    guard.apply(pnl, clock[0])
    assert bool(guard.latch) == trips
    assert all(p["enabled"] is not trips for p in worker.assets().values())
    assert worker.control(control["ticker"])["paused"] is False
    if trips:
        with pytest.raises(HTTPException, match="all new live buys disabled"):
            worker._entry(control, clock[0])
        with pytest.raises(HTTPException, match="all new live buys disabled"):
            worker.configure(
                LiveControl(
                    ticker=control["ticker"],
                    enabled=True,
                    contracts=10,
                    revision=worker.assets()[control["asset"]]["revision"],
                    confirm="ENABLE_REAL_TRADING",
                )
            )
        restarted = GlobalLossGuard(manual, worker.members, worker.stores)
        restarted.apply(100, clock[0] + 86400)
        with pytest.raises(HTTPException):
            restarted.check(clock[0] + 86400)
    else:
        guard.check(clock[0])


def test_missing_stale_or_invalid_accounting_blocks_buys(live):
    worker, _, manual, _, _, clock = live
    guard = GlobalLossGuard(manual, worker.members, worker.stores)
    with pytest.raises(HTTPException):
        guard.check(clock[0])
    guard.apply(0, clock[0])
    with pytest.raises(HTTPException):
        guard.check(clock[0] + guard.max_age + 1)
    with pytest.raises(ValueError):
        guard.apply(float("nan"), clock[0])
    guard.error = "Accounting failure"
    with pytest.raises(HTTPException):
        guard.check(clock[0])


def test_background_calculation_matches_dashboard_results_and_caches(live):
    worker, _, manual, _, _, _ = live
    guard = GlobalLossGuard(manual, worker.members, worker.stores)

    class History:
        revision = 1
        calls = 0

        def history_revision(self, *args):
            return self.revision

        def history_snapshot(self):
            return nullcontext()

        def list(self, *args, **kwargs):
            self.calls += 1
            return [{"body": {"net_pnl": -30.1}}, {"body": {"net_pnl": 5.1}}]

    history = History()
    guard.histories = {a: history for a in worker.members}
    assert guard.calculate() == -25
    assert guard.calculate() == -25
    assert history.calls == 1
    history.revision = 2
    assert guard.calculate() == -25
    assert history.calls == 2


@pytest.mark.anyio
async def test_checks_immediately_then_waits_fifteen_minutes(live, monkeypatch):
    worker, _, manual, _, _, _ = live
    guard = GlobalLossGuard(manual, worker.members, worker.stores)
    calls = []
    monkeypatch.setattr(guard, "calculate", lambda: calls.append("calculate") or 0)

    async def wait(awaitable, *, timeout):
        assert calls == ["calculate"]
        assert guard.pnl == 0
        assert timeout == 900
        guard.stop.set()
        await awaitable

    monkeypatch.setattr("btc15.global_loss_guard.asyncio.wait_for", wait)
    await guard.run()
    assert calls == ["calculate"]
