"""Local PAPER service lifecycle and a read-only monitoring probe."""

import asyncio
import signal
import time

from .runner import collect


async def serve(settings, config, store, run_id, min_free_bytes, duration=None):
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
        )
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


def health(store, run_id, now=None):
    now = time.time() if now is None else now
    rows = store.list(kind="status", mode="PAPER", run_id=run_id, limit=1, newest_first=True)
    if not rows:
        return dict(healthy=False, run_id=run_id, reasons=["NO_STATUS"])
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
    if body.get("reference_age") is None or not 0 <= body["reference_age"] <= 2:
        reasons.append("STALE_REFERENCE")
    if not 0 <= body.get("processing_lag", float("inf")) <= 1:
        reasons.append("PROCESSING_LAG")
    return dict(healthy=not reasons, run_id=run_id, status_age=age, reasons=reasons, status=body)
