import asyncio
import json
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import websockets

from .api import KalshiClient, subscriptions
from .domain import dumps
from .engine import Engine
from .models import guard_archived_exposure, require_single_run
from .storage import CompactRecorder, RawRecorder


def stop_entries(engine, now):
    """Stop new entries and cancel pending remainders without liquidating positions."""
    engine.execute = False
    engine.entries_active = False
    for ticker in list(engine.executor.orders):
        if engine.executor.orders[ticker].active:
            engine.executor.cancel(ticker, now, "safe_shutdown")


async def collect(
    settings,
    config,
    store,
    paper=False,
    duration=None,
    resume=None,
    *,
    stop_event=None,
    managed_run=None,
    min_free_bytes=0,
    record_all=None,
):
    """Independent receipt/metadata tasks; one ordered durable analysis worker."""
    settings.guard()
    record_all = not paper if record_all is None else record_all
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    if settings.mode != "PAPER":
        raise ValueError("Collector requires PAPER mode")
    if duration is not None and duration <= 0:
        raise ValueError("Positive capture duration required")
    client = KalshiClient(settings)
    owner = str(uuid.uuid4())
    acquired = False
    recorder = None
    clean_shutdown = False
    entries_stopped = False
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="btc15-recorder")
    loop = asyncio.get_running_loop()

    async def work(fn, *args):
        return await loop.run_in_executor(pool, fn, *args)

    try:
        client.headers("GET", "/trade-api/ws/v2")
        store.acquire("collector", owner)
        acquired = True
        if paper:
            require_single_run(store, resume or managed_run)
            guard_archived_exposure(store)
        if managed_run:
            if not paper or resume:
                raise ValueError("Managed runs require paper execution without explicit resume")
            previous = store.list(kind="run", run_id=managed_run, limit=1)
            if previous:
                if previous[0]["mode"] != "PAPER" or not previous[0]["body"]["execute"]:
                    raise ValueError("Managed run must refer to an executing PAPER run")
                resume = managed_run
        engine = Engine(
            store,
            config,
            "PAPER",
            run_id=resume or managed_run,
            execute=paper,
            clock=time.time,
            resume=bool(resume),
            record_evaluations=record_all,
        )
        if paper and not resume:
            closed = {r["opportunity_id"] for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
            buys = {
                r["opportunity_id"]
                for r in store.list(kind="fill", mode="PAPER", limit=None)
                if r["body"]["action"] == "buy"
            }
            if buys - closed:
                raise RuntimeError("Unresolved paper positions: use paper --resume RUN_ID")
            engine.executor.restore_daily_history()
        if paper:
            # Even a run with no fills must be resumable after a clean service restart.
            store.checkpoint(engine.run_id, engine.executor.snapshot())
        recorder = (
            RawRecorder(Path(settings.data_dir) / "raw", chunk_size=2000)
            if record_all
            else CompactRecorder(Path(settings.data_dir) / "raw")
        )
        journal = recorder.directory / (recorder.session + ".jsonl") if record_all else recorder.path
        engine.raw_archive = True
        store.add(
            "raw_source",
            {
                "journal": str(journal),
                "format": "jsonl" if record_all else "jsonl.gz",
                "parent_run": engine.run_id,
            },
            engine.run_id,
            "PAPER",
            time.time(),
        )
        queue = asyncio.Queue(maxsize=20000)
        overflow_rows = []
        reconnect = asyncio.Event()
        stop = stop_event if stop_event is not None else asyncio.Event()
        metadata_ready = asyncio.Event()
        connection = str(uuid.uuid4())
        tickers = []
        tracked = {
            r["market"]: r["body"]["raw"] for r in store.list(kind="market", run_id=engine.run_id, limit=None)
        }
        connected = False
        started = time.monotonic()
        maximum_queue = 0
        last_status = 0
        last_display = 0
        display_reference = None
        receipt_reference = None
        display_tickers = {}

        def emit(payload):
            nonlocal maximum_queue, receipt_reference
            row = dict(
                id=str(uuid.uuid4()),
                received=time.time(),
                monotonic_ns=time.monotonic_ns(),
                connection_id=connection,
                payload=dumps(payload),
            )
            if overflow_rows:
                overflow_rows.append(row)
                raise RuntimeError("Recorder queue overflow; capture stopped")
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull as e:
                overflow_rows.append(row)
                stop.set()
                raise RuntimeError("Recorder queue capacity exceeded; capture stopped") from e
            maximum_queue = max(maximum_queue, queue.qsize())
            if payload.get("type") == "cfbenchmarks_value_5hz":
                msg = payload.get("msg", {})
                if msg.get("index_id") == "BRTI":
                    receipt_reference = dict(
                        value=msg.get("value_usd"),
                        received=row["received"],
                        source_ts_ms=msg.get("source_ts_ms"),
                    )

        def process_batch(rows):
            nonlocal last_status, last_display, display_reference, entries_stopped
            if recorder:
                recorder.append_rows(rows)  # Input capture is durable before analysis.
            valid = True
            for row in rows:
                if stop.is_set() and not entries_stopped:
                    stop_entries(engine, row["received"])
                    entries_stopped = True
                if not engine.executor.risk.halted and (Path(settings.data_dir) / "HALT").exists():
                    engine.executor.halt(row["received"])
                valid = engine.ingest(row) and valid
                payload = json.loads(row["payload"])
                if payload.get("type") == "cfbenchmarks_value_5hz":
                    msg = payload.get("msg", {})
                    display_reference = dict(
                        value=msg.get("value_usd"),
                        received=row["received"],
                        source_ts_ms=msg.get("source_ts_ms"),
                    )
                elif payload.get("type") == "ticker":
                    msg = payload.get("msg", {})
                    display_tickers[msg.get("market_ticker")] = msg
            now = time.time()
            if time.monotonic() - last_display >= 0.05 or not connected:
                markets = []
                for ticker, market in engine.markets.items():
                    if not market.tradable(now):
                        continue
                    book = engine.books[ticker]
                    fresh = connected and book.valid and 0 <= now - book.received <= config.book_max_age
                    markets.append(
                        dict(
                            ticker=ticker,
                            title=market.title,
                            close_time=market.raw["close_time"],
                            status=market.status,
                            floor_strike=market.spec.strike,
                            book=book.summary() if fresh else {},
                            book_received=book.received,
                            fresh=fresh,
                            volume_fp=display_tickers.get(ticker, {}).get(
                                "volume_fp", market.raw.get("volume_fp")
                            ),
                        )
                    )
                store.publish_market_display(
                    dict(
                        run_id=engine.run_id,
                        published_at=now,
                        connected=connected,
                        clock_ok=engine.clock_ok,
                        markets=markets,
                        reference_5hz=display_reference,
                        processing_lag=(time.monotonic_ns() - rows[-1]["monotonic_ns"]) / 1e9,
                    )
                )
                last_display = time.monotonic()
            if now - last_status >= 1 or not connected:
                save_status = store.add if record_all else store.publish_record
                save_status(
                    "status",
                    dict(
                        connected=connected,
                        run_id=engine.run_id,
                        mode="PAPER",
                        paper_execution=paper,
                        recording="full" if record_all else "trades_with_compact_inputs",
                        live_enabled=False,
                        models=[
                            dict(
                                run_id=e.run_id,
                                model=e.executor.model_identity,
                                entries_active=e.entries_active,
                                halted=e.executor.risk.halted,
                                open_positions=len(e.executor.positions),
                                realized_pnl=e.executor.risk.realized,
                            )
                            for e in ([engine])
                        ],
                        clock_ok=engine.clock_ok,
                        exchange_open=engine.exchange_open,
                        reference_age=now - engine.ticks[-1].received if engine.ticks else None,
                        processing_lag=(time.monotonic_ns() - rows[-1]["monotonic_ns"]) / 1e9,
                        maximum_queue=maximum_queue,
                        markets=list(engine.markets),
                        positions={k: vars(v) for k, v in engine.executor.positions.items()},
                        exposure=sum(engine.executor.risk.reserved.values()),
                        daily=engine.executor.risk.day(now),
                        halted=engine.executor.risk.halted,
                    ),
                    engine.run_id,
                    "PAPER",
                    now,
                )
                if recorder:
                    recorder.flush()
                if not record_all:
                    if engine.latest:
                        latest = max(engine.latest.values(), key=lambda b: b["timestamp"])
                        store.publish_record(
                            "evaluation",
                            latest,
                            engine.run_id,
                            engine.mode,
                            latest["timestamp"],
                            latest["ticker"],
                            engine._last_op[latest["ticker"]],
                        )
                last_status = now
            return valid

        async def consume():
            while True:
                first = await queue.get()
                if first is None:
                    break
                rows = [first]
                finished = False
                while len(rows) < 256:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        finished = True
                        break
                    rows.append(item)
                if not await work(process_batch, rows):
                    reconnect.set()
                if finished:
                    break

        async def refresh():
            nonlocal tickers
            while not stop.is_set():
                try:
                    series, markets = await client.discover()
                    changes = {}
                    for event in sorted({m["event_ticker"] for m in markets}):
                        changes[event] = [
                            r
                            async for r in client.pages(
                                "events/fee_changes", "event_fee_changes", {"event_ticker": event}
                            )
                        ]
                    series_changes = (
                        await client.get(
                            "series/fee_changes", {"series_ticker": "KXBTC15M", "show_historical": True}
                        )
                    ).get("series_fee_change_arr", [])
                    status = await client.get("exchange/status")
                    emit(
                        dict(
                            type="metadata",
                            msg=dict(
                                series=series,
                                markets=markets,
                                fee_changes=changes,
                                series_fee_changes=series_changes,
                                exchange_status=status,
                                clock_skew=client.last_clock_skew,
                            ),
                        )
                    )
                    current = sorted(m["ticker"] for m in markets)
                    tickers = current
                    for m in markets:
                        tracked[m["ticker"]] = m
                    for ticker, m in list(tracked.items()):
                        from .domain import timestamp

                        if time.time() >= timestamp(m["close_time"]):
                            raw = (
                                await client.get("markets/" + ticker, {"exchange_index": m["exchange_index"]})
                            )["market"]
                            if raw.get("status") == "finalized" and raw.get("result") in ("yes", "no"):
                                emit(
                                    dict(
                                        type="settlement",
                                        msg=dict(market_ticker=ticker, result=raw["result"]),
                                    )
                                )
                                del tracked[ticker]
                    metadata_ready.set()
                except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
                    emit(
                        dict(
                            type="stale",
                            msg={"reason": "metadata_refresh_failed", "error": type(exc).__name__},
                        )
                    )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=15)
                except TimeoutError:
                    pass

        async def receive():
            nonlocal connection, connected
            await metadata_ready.wait()
            backoff = 1
            while not stop.is_set():
                connection = str(uuid.uuid4())
                reconnect.clear()
                try:
                    async with websockets.connect(
                        settings.ws_url,
                        additional_headers=client.headers("GET", "/trade-api/ws/v2"),
                        ping_interval=20,
                        ping_timeout=20,
                        max_queue=2048,
                    ) as ws:
                        connected = True
                        emit(dict(type="connected", msg={"tickers": tickers}))
                        for subscription in subscriptions(tickers):
                            await ws.send(json.dumps(subscription))
                        backoff = 1
                        market_sids = {}
                        subscribed_markets = set(tickers)
                        request_id = 10
                        while not stop.is_set() and not reconnect.is_set():
                            if len(market_sids) == 3 and set(tickers) != subscribed_markets:
                                desired = set(tickers)
                                for action, changed in (
                                    ("add_markets", desired - subscribed_markets),
                                    ("delete_markets", subscribed_markets - desired),
                                ):
                                    if changed:
                                        for sid in market_sids.values():
                                            command = dict(
                                                id=request_id,
                                                cmd="update_subscription",
                                                params=dict(
                                                    sid=sid, market_tickers=sorted(changed), action=action
                                                ),
                                            )
                                            emit(dict(type="subscription_update_requested", msg=command))
                                            await ws.send(json.dumps(command))
                                            request_id += 1
                                subscribed_markets = desired
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                            except TimeoutError:
                                continue
                            try:
                                payload = json.loads(raw)
                            except (ValueError, UnicodeError):
                                emit(dict(type="error", msg=dict(reason="invalid_json", raw=str(raw))))
                                raise ValueError("Malformed WebSocket frame; raw error retained")
                            emit(payload)
                            if payload.get("type") == "subscribed":
                                msg = payload.get("msg", {})
                                if msg.get("channel") in ("orderbook_delta", "trade", "ticker"):
                                    market_sids[msg["channel"]] = msg["sid"]
                            if payload.get("type") == "error":
                                # No retry loop concealing denied entitlements/subscriptions.
                                raise PermissionError(
                                    "Kalshi rejected a WebSocket subscription; see raw error record"
                                )
                except (httpx.HTTPError, OSError, websockets.WebSocketException) as exc:
                    if isinstance(exc, PermissionError):
                        raise
                    if getattr(getattr(exc, "response", None), "status_code", None) in (401, 403):
                        raise PermissionError("Kalshi rejected WebSocket authentication") from exc
                    emit(dict(type="disconnect", msg={"error": type(exc).__name__}))
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=backoff)
                    except TimeoutError:
                        pass
                    backoff = min(30, backoff * 2)
                finally:
                    connected = False
                    emit(dict(type="disconnect", msg={"reason": "reconnect_or_shutdown"}))

        async def reference_display():
            # UI-only receipt projection. Execution continues to use the ordered,
            # durable 1 Hz stream and its processing-time freshness guards.
            async def publish(body):
                task = asyncio.create_task(asyncio.to_thread(store.publish_market_display, body, "reference"))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise

            try:
                while not stop.is_set():
                    await publish(
                        dict(
                            run_id=engine.run_id,
                            connected=connected,
                            published_at=time.time(),
                            reference_5hz=receipt_reference,
                        ),
                    )
                    await asyncio.sleep(0.2)
            finally:
                await publish(
                    dict(
                        run_id=engine.run_id,
                        connected=False,
                        published_at=time.time(),
                        reference_5hz=receipt_reference,
                    ),
                )

        async def clock():
            while not stop.is_set():
                if store.list(kind="shutdown_request", run_id=owner, mode="PAPER", limit=1):
                    stop.set()
                    break
                if min_free_bytes and shutil.disk_usage(settings.data_dir).free < min_free_bytes:
                    raise OSError("Free disk space below service reserve; capture stopped")
                emit(dict(type="heartbeat", msg={}))
                if duration is not None and time.monotonic() - started >= duration:
                    stop.set()
                    break
                await asyncio.sleep(0.5)

        async with asyncio.TaskGroup() as group:
            worker = group.create_task(consume())
            producers = [
                group.create_task(refresh()),
                group.create_task(receive()),
                group.create_task(clock()),
                group.create_task(reference_display()),
            ]
            await stop.wait()
            # Bounded cancellation also interrupts slow HTTP refresh/socket waits.
            for task in producers:
                task.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            emit(dict(type="disconnect", msg={"reason": "shutdown"}))
            await queue.put(None)
            await worker
        if paper:
            with store.transaction():
                store.checkpoint(engine.run_id, engine.executor.snapshot())
        clean_shutdown = True
        return engine.run_id
    finally:
        try:
            try:
                if acquired and "engine" in locals() and not clean_shutdown:
                    await work(stop_entries, engine, time.time())
            finally:
                if recorder:
                    # Preserve frames already received even when analysis or a producer fails.
                    pending = []
                    if "queue" in locals():
                        while not queue.empty():
                            row = queue.get_nowait()
                            if row is not None:
                                pending.append(row)
                    pending.extend(overflow_rows if "overflow_rows" in locals() else [])
                    try:
                        if pending:
                            await work(recorder.append_rows, pending)
                    finally:
                        await work(recorder.close)
        except BaseException:
            clean_shutdown = False
            raise
        finally:
            try:
                await client.close()
            except BaseException:
                clean_shutdown = False
                raise
            finally:
                try:
                    if acquired:
                        with store.transaction():
                            if clean_shutdown:
                                store.add(
                                    "shutdown_complete",
                                    dict(
                                        run_id=engine.run_id,
                                        open_positions=len(engine.executor.positions),
                                        runs=[engine.run_id],
                                    ),
                                    owner,
                                    "PAPER",
                                    time.time(),
                                )
                            store.release("collector", owner)
                finally:
                    pool.shutdown(wait=True)


def backtest(path, config, store, parent_run=None):
    from .research import replay_files

    return replay_files([path], config, store, parent_run)
