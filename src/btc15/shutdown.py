"""Cooperative shutdown of the collector that owns this dashboard's database."""

import asyncio
import time


async def finish_shutdown(store, owner, state, exit_server, timeout=60, close_delay=2):
    deadline = time.monotonic() + timeout
    try:
        while True:
            current = await asyncio.to_thread(store.writer_owner)
            completed = (
                await asyncio.to_thread(store.list, "shutdown_complete", owner, "PAPER") if owner else []
            )
            if current is None and (owner is None or completed):
                state.update(
                    status="stopped",
                    message="Bot stopped safely. Saved positions can be resumed; dashboard is closing.",
                    open_positions=completed[-1]["body"]["open_positions"] if completed else None,
                )
                # Allow the browser to display the acknowledgement before stopping HTTP/SSE.
                await asyncio.sleep(close_delay)
                exit_server()
                return
            if current not in (None, owner):
                raise RuntimeError(
                    "Another collector started. Dashboard remains open; request shutdown again."
                )
            if current is None and owner and not completed:
                raise RuntimeError(
                    "Collector stopped without a clean-shutdown acknowledgement. Review its log."
                )
            if time.monotonic() >= deadline:
                raise RuntimeError("Bot has not confirmed shutdown. Dashboard remains open; inspect its log.")
            await asyncio.sleep(0.25)
    except Exception as exc:
        state.update(status="failed", message=str(exc))
