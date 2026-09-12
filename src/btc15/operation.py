"""Local PAPER service lifecycle and a read-only monitoring probe."""

import asyncio
import logging
import signal
import time

from .lease_identity import WriterOwnedError
from .operational_state import operational_state
from .runner import collect


def record_collector_failure(store, run_id, exc):
    code = "WRITER_LEASE_BLOCKED" if isinstance(exc, WriterOwnedError) else "COLLECTOR_FAILED"
    body = dict(run_id=run_id, timestamp=time.time(), code=code, error_type=type(exc).__name__)
    try:
        with store.transaction():
            store.add("collector_failure", body, run_id, "PAPER", body["timestamp"])
            store.publish_market_display(body, "collector_failure")
    except Exception:
        # A failed recorder/database must not hide the original startup exception.
        logging.getLogger("btc15").exception("Could not publish collector failure")


async def serve(
    settings, config, store, run_id, min_free_bytes, duration=None, *, stop_confirmation_shadow=False
):
    if not run_id or min_free_bytes <= 0:
        raise ValueError("Service requires a run ID and positive disk reserve")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
        return await collect(
            settings,
            config,
            store,
            paper=True,
            duration=duration,
            managed_run=run_id,
            stop_event=stop,
            min_free_bytes=min_free_bytes,
            **({"stop_confirmation_shadow": True} if stop_confirmation_shadow else {}),
        )
    except Exception as exc:
        record_collector_failure(store, run_id, exc)
        raise
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


def health(store, run_id, now=None):
    now = time.time() if now is None else now
    failure = store.read_market_display("collector_failure")
    if failure and failure.get("run_id") != run_id:
        failure = None
    rows = store.list(kind="status", mode="PAPER", run_id=run_id, limit=1, newest_first=True)
    if not rows:
        return dict(
            healthy=False,
            run_id=run_id,
            reasons=["NO_STATUS"],
            operational=operational_state(None, None, now, failure=failure),
        )
    row = rows[0]
    body = row["body"]
    age = now - row["timestamp"]
    reasons = []
    if not 0 <= age <= 5:
        reasons.append("STALE_STATUS")
    for key in ("connected", "clock_ok", "paper_execution", "exchange_open"):
        if not body.get(key):
            reasons.append(key.upper())
    if body.get("halted"):
        reasons.append("HALTED")
    if body.get("settlement_recovery"):
        reasons.append("SETTLEMENT_RECOVERY_REQUIRED")
    if body.get("reference_age") is None or not 0 <= body["reference_age"] <= 2:
        reasons.append("STALE_REFERENCE")
    if not 0 <= body.get("processing_lag", float("inf")) <= 1:
        reasons.append("PROCESSING_LAG")
    recovery = body.get("recovery", {})
    if recovery.get("entries_blocked"):
        reasons.append("COLLECTOR_RECOVERING")
    if recovery.get("warning"):
        reasons.append("BACKLOG_WARNING")
    return dict(
        healthy=not reasons,
        run_id=run_id,
        status_age=age,
        reasons=reasons,
        status=body,
        operational=operational_state(
            row,
            store.latest_evaluations([run_id], "PAPER").get(run_id),
            now,
            reference=store.read_market_display("reference"),
            failure=failure,
        ),
    )
