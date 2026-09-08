import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

import httpx
import websockets

from .api import KalshiClient, subscriptions
from .engine import Engine
from .storage import RawRecorder

log = logging.getLogger("btc15")


async def collect(settings, config, store, paper=False, duration=None):
    settings.guard()
    if settings.mode != "PAPER":
        raise ValueError("Collector requires PAPER mode")
    # This release refuses to reset risk by restarting over unresolved paper positions.
    if paper:
        closed = {r["opportunity_id"] for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
        buys = {
            r["opportunity_id"]
            for r in store.list(kind="fill", mode="PAPER", limit=None)
            if r["body"]["action"] == "buy"
        }
        if buys - closed:
            raise RuntimeError(
                "Unresolved prior paper positions: replay their journal in an isolated database before recovery"
            )
    client = KalshiClient(settings)
    # Validate credentials before creating a run or recorder.
    client.headers("GET", "/trade-api/ws/v2")
    owner = str(uuid.uuid4())
    store.acquire("collector", owner)
    recorder = RawRecorder(Path(settings.data_dir) / "raw")
    engine = Engine(store, config, "PAPER", execute=paper, clock=time.time)
    if paper:
        for r in store.list(kind="order", mode="PAPER", limit=None):
            if r["body"].get("status") == "submitted":
                day = engine.executor.risk.day(r["timestamp"])
                day["trades"] += 1
                day["exposure"] += r["body"].get("risk_reserved", r["body"]["quantity"])
        for r in store.list(kind="trade_result", mode="PAPER", limit=None):
            pnl = r["body"]["net_pnl"]
            engine.executor.risk.realized += pnl
            engine.executor.risk.day(r["timestamp"])["pnl"] += pnl
    store.add(
        "raw_source",
        {"journal": str(recorder.directory / (recorder.session + ".jsonl"))},
        engine.run_id,
        "PAPER",
        time.time(),
    )
    started = time.monotonic()
    connection = str(uuid.uuid4())
    last_metadata = 0.0

    def record(payload):
        row = recorder.append(payload, time.time(), time.monotonic_ns(), connection)
        return engine.ingest(row)

    async def metadata():
        series, markets = await client.discover()
        fee_changes = {}
        for event_ticker in sorted({m["event_ticker"] for m in markets}):
            fee_changes[event_ticker] = [
                r
                async for r in client.pages(
                    "events/fee_changes", "event_fee_changes", {"event_ticker": event_ticker}
                )
            ]
        status = await client.get("exchange/status")
        record(
            dict(
                type="metadata",
                msg=dict(
                    series=series,
                    markets=markets,
                    exchange_status=status,
                    clock_skew=client.last_clock_skew,
                    fee_changes=fee_changes,
                ),
            )
        )
        # Closed markets disappear from discovery; retrieve their authoritative results.
        for ticker, market in list(engine.markets.items()):
            if time.time() >= market.close_time and store.state(engine.run_id, ticker) != "CLOSED":
                raw = (await client.get("markets/" + ticker, {"exchange_index": market.exchange_index}))[
                    "market"
                ]
                if raw.get("status") == "finalized" and raw.get("result") in ("yes", "no"):
                    record(dict(type="settlement", msg=dict(market_ticker=ticker, result=raw["result"])))
        return [m["ticker"] for m in markets if m.get("ticker") in engine.markets]

    try:
        backoff = 1
        while duration is None or time.monotonic() - started < duration:
            connection = str(uuid.uuid4())
            try:
                tickers = await metadata()
                last_metadata = time.monotonic()
                if not tickers:
                    record(dict(type="stale", msg={"reason": "no_valid_market"}))
                    await asyncio.sleep(5)
                    continue
                async with websockets.connect(
                    settings.ws_url,
                    additional_headers=client.headers("GET", "/trade-api/ws/v2"),
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=4096,
                ) as ws:
                    record(dict(type="connected", msg={"tickers": tickers}))
                    for subscription in subscriptions(tickers):
                        await ws.send(json.dumps(subscription))
                    backoff = 1
                    heartbeat = time.monotonic()
                    while duration is None or time.monotonic() - started < duration:
                        if (Path(settings.data_dir) / "HALT").exists():
                            engine.executor.risk.halted = True
                            record(dict(type="stale", msg={"reason": "kill_switch"}))
                        if time.monotonic() - last_metadata >= 30:
                            current = await metadata()
                            last_metadata = time.monotonic()
                            if current != tickers:
                                break  # New connection provides fresh book snapshots for rollover.
                        if time.monotonic() - heartbeat >= 1:
                            record(dict(type="heartbeat", msg={}))
                            recorder.flush()
                            heartbeat = time.monotonic()
                            status = dict(
                                connected=True,
                                run_id=engine.run_id,
                                mode="PAPER",
                                paper_execution=paper,
                                live_enabled=False,
                                clock_ok=engine.clock_ok,
                                exchange_open=engine.exchange_open,
                                reference_age=time.time() - engine.ticks[-1].received
                                if engine.ticks
                                else None,
                                markets=list(engine.markets),
                                positions={k: vars(v) for k, v in engine.executor.positions.items()},
                                exposure=sum(engine.executor.risk.reserved.values()),
                                daily=engine.executor.risk.day(time.time()),
                                halted=engine.executor.risk.halted,
                            )
                            store.add("status", status, engine.run_id, "PAPER", time.time())
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        except TimeoutError:
                            continue
                        if not record(json.loads(raw)):
                            break  # Any sequence/schema failure forces a fresh snapshot.
                record(dict(type="disconnect", msg={"reason": "rollover_or_recovery"}))
            except (OSError, httpx.HTTPError, websockets.WebSocketException, ValueError, RuntimeError) as exc:
                record(dict(type="disconnect", msg={"error": type(exc).__name__, "detail": str(exc)}))
                await asyncio.sleep(backoff)
                backoff = min(30, backoff * 2)
    finally:
        record(dict(type="disconnect", msg={"reason": "shutdown"}))
        store.add(
            "status",
            dict(connected=False, mode="PAPER", run_id=engine.run_id, live_enabled=False),
            engine.run_id,
            "PAPER",
            time.time(),
        )
        recorder.close()
        store.release("collector", owner)
        await client.close()
    return engine.run_id


def backtest(path, config, store, parent_run=None):
    from .research import replay_files

    return replay_files([path], config, store, parent_run)
