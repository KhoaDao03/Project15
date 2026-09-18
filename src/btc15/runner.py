import asyncio
import json
import shutil
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import websockets

from .api import KalshiClient, subscriptions
from .collector_recovery import CollectorRecovery
from .decision_notifications import notify_decision
from .domain import Book, dumps, parse_market
from .engine import Engine
from .models import guard_archived_exposure, require_single_run
from .reference_history import load_history, save_history
from .storage import CompactRecorder, RawRecorder

QUEUE_CAPACITY = 20000


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
    stop_confirmation_shadow=False,
    separate_paper=False,
    live_signals=False,
):
    """Independent receipt/metadata tasks; one ordered durable analysis worker."""
    if live_signals and (paper or resume or separate_paper or stop_confirmation_shadow or record_all):
        raise ValueError("Live signals require a separate non-executing collector")
    if live_signals and not managed_run:
        raise ValueError("Live signals require a named run")
    settings = replace(settings, asset=config.asset)
    settings.guard()
    record_all = not (paper or live_signals) if record_all is None else record_all
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
    shadow = None
    paper_worker = None
    signal_store = None
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
        if live_signals:
            existing = store.run_summaries("PAPER")
            for run in existing:
                if run["run_id"] != managed_run or run["body"]["versions"]["config"] != config.version:
                    raise ValueError("Live signals require their own database and unchanged configuration")
            previous = store.list(kind="run", run_id=managed_run, limit=1, newest_first=True)
            if previous and previous[0]["body"].get("live_signals") is not True:
                raise ValueError("Live signals require their own database, not a paper or observation run")
        if managed_run and not live_signals:
            if not paper or resume:
                raise ValueError("Managed runs require paper execution without explicit resume")
            previous = store.list(kind="run", run_id=managed_run, limit=1)
            if previous:
                if previous[0]["mode"] != "PAPER" or not previous[0]["body"]["execute"]:
                    raise ValueError("Managed run must refer to an executing PAPER run")
                resume = managed_run
        if separate_paper:
            if not paper or stop_confirmation_shadow:
                raise ValueError("Separate paper simulation requires paper mode without shadow comparison")
            from .paper_worker import PaperWorker, SignalStore

            paper_worker = PaperWorker(
                str(store.engine.url),
                config,
                resume or managed_run or str(uuid.uuid4()),
                bool(resume),
                record_all,
                settings.data_dir,
            )
            initial_paper = await work(paper_worker.start)
            signal_store = SignalStore()
            engine = Engine(
                signal_store,
                config,
                "PAPER",
                run_id=initial_paper["run_id"],
                execute=False,
                clock=time.time,
                record_evaluations=False,
                signal_only=True,
            )
        else:
            engine = Engine(
                store,
                config,
                "PAPER",
                run_id=resume or managed_run,
                execute=paper,
                clock=time.time,
                resume=bool(resume),
                record_evaluations=record_all,
                signal_only=live_signals,
                signal_settlements=live_signals,
            )
        if paper and not resume and not separate_paper:
            closed = {r["opportunity_id"] for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
            buys = {
                r["opportunity_id"]
                for r in store.list(kind="fill", mode="PAPER", limit=None)
                if r["body"]["action"] == "buy"
            }
            if buys - closed:
                raise RuntimeError("Unresolved paper positions: use paper --resume RUN_ID")
            engine.executor.restore_daily_history()
        if paper and not separate_paper:
            # Even a run with no fills must be resumable after a clean service restart.
            store.checkpoint(engine.run_id, engine.executor.snapshot())
        if stop_confirmation_shadow:
            if not paper:
                raise ValueError("Stop comparison requires paper purchases")
            from .stop_shadow import StopShadow

            try:
                shadow = StopShadow(engine, Path(settings.data_dir) / "stop-confirmation-shadow.db")
            except Exception as exc:
                store.add(
                    "stop_shadow_status",
                    dict(status="FAILED", error=str(exc)),
                    engine.run_id,
                    engine.mode,
                    time.time(),
                )
        journal = None
        if not live_signals:
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
        preload = dict(status="DISABLED", samples=0)
        if paper or live_signals:
            recordings = [
                r
                for r in store.list(kind="raw_source", mode="PAPER", limit=20)
                if r["body"]["journal"] != str(journal)
            ]
            body, preload = await work(load_history, settings.data_dir, recordings, time.time(), config)
            # Write exactly the inputs restored, before any new live event. Do not
            # replay old market events through the executing engine.
            history_row = dict(
                id=str(uuid.uuid4()),
                received=time.time(),
                monotonic_ns=time.monotonic_ns(),
                connection_id="reference-preload",
                payload=dumps(dict(type="reference_history", msg=body)),
            )
            if recorder:
                await work(recorder.append_rows, [history_row])
            await work(engine.ingest, history_row)
            if paper_worker:
                paper_worker.submit([history_row])
            store.add("reference_preload", preload, engine.run_id, "PAPER", history_row["received"])
        queue = asyncio.Queue(maxsize=QUEUE_CAPACITY)
        pending_receipts = deque()
        recovery = CollectorRecovery(config, time.time())
        engine.collector_pause = recovery.paused
        engine.executor.collector_pause = recovery.paused
        overflow_rows = []
        reconnect = asyncio.Event()
        drained = asyncio.Event()
        refresh_requested = asyncio.Event()
        stop = stop_event if stop_event is not None else asyncio.Event()
        metadata_ready = asyncio.Event()
        connection = str(uuid.uuid4())
        tickers = []
        retained_markets = {
            r["market"]: r["body"] for r in store.list(kind="market", run_id=engine.run_id, limit=None)
        }
        tracked = {ticker: body["raw"] for ticker, body in retained_markets.items()}
        if paper_worker or live_signals:
            # Completed history does not need another REST settlement request at startup.
            with store.transaction():
                tracked = {
                    ticker: raw
                    for ticker, raw in tracked.items()
                    if store.state(engine.run_id, ticker) != "CLOSED"
                }
        if live_signals:
            # Old live fills still need official results after a collector restart.
            # Restore validated identities, never a stale execution book/reference.
            for ticker in tracked:
                body = retained_markets[ticker]
                engine.markets[ticker] = parse_market(body["raw"], body["series"])
                engine.books[ticker] = Book()
        for ticker, snapshot in engine.executor.contracts.items():
            if ticker in engine.executor.positions:
                tracked[ticker] = snapshot["raw"]
        if paper_worker:
            for ticker, snapshot in initial_paper.get("contracts", {}).items():
                tracked[ticker] = snapshot["raw"]
        settled_tickers = set()
        connected = False
        started = time.monotonic()
        maximum_queue = 0
        last_status = 0
        last_display = 0
        last_live_decision = None
        live_decision_eligible = False
        display_reference = None
        receipt_reference = None
        display_tickers = {}
        last_recovery_status = None
        last_history_save = time.monotonic()

        def request_recovery(reason):
            recovery.request(reason, time.time())
            reconnect.set()
            drained.clear()

        def check_pressure():
            lag = (time.monotonic_ns() - pending_receipts[0]) / 1e9 if pending_receipts else 0
            recovery.pressure(lag, len(pending_receipts), queue.maxsize, time.time())
            if recovery.drain.is_set():
                reconnect.set()
            return lag

        def emit(payload):
            nonlocal maximum_queue, receipt_reference
            row = dict(
                id=str(uuid.uuid4()),
                received=time.time(),
                monotonic_ns=time.monotonic_ns(),
                connection_id=connection,
                payload=dumps(payload),
            )
            if (
                payload.get("type") in ("cfbenchmarks_value", "pyth_value")
                and payload.get("msg", {}).get("index_id", payload.get("msg", {}).get("underlying_ticker"))
                == config.asset_spec.index
            ):
                # Immutable marker only: the worker still validates/applies the
                # durable event in order. Receipt cannot supply future model data.
                engine.executor.latest_reference_receipt = (row["id"], row["received"])
                if paper_worker:
                    paper_worker.reference(row)
            if overflow_rows:
                overflow_rows.append(row)
                raise RuntimeError("Recorder queue overflow; capture stopped")
            try:
                queue.put_nowait(row)
                pending_receipts.append(row["monotonic_ns"])
            except asyncio.QueueFull as e:
                overflow_rows.append(row)
                stop.set()
                raise RuntimeError("Recorder queue capacity exceeded; capture stopped") from e
            maximum_queue = max(maximum_queue, queue.qsize())
            check_pressure()
            if payload.get("type") in ("cfbenchmarks_value_5hz", "pyth_value"):
                msg = payload.get("msg", {})
                if msg.get("index_id", msg.get("underlying_ticker")) == config.asset_spec.index:
                    receipt_reference = dict(
                        value=msg.get("value_usd"),
                        received=row["received"],
                        source_ts_ms=msg.get("source_ts_ms"),
                    )

        def process_batch(rows):
            nonlocal last_status, last_display, display_reference, entries_stopped
            nonlocal last_live_decision, live_decision_eligible
            nonlocal last_recovery_status
            nonlocal last_history_save
            # Persist the analysis decision alongside every source frame so replay
            # also applies the complete sequence without trading on the old backlog.
            for row in rows:
                row["analysis_suspended"] = recovery.drain.is_set()
                row["collector_entries_blocked"] = recovery.paused.is_set()
                if paper_worker:
                    row["paper_stopping"] = stop.is_set()
            if recorder:
                recorder.append_rows(rows)  # Input capture is durable before analysis.
            if paper_worker:
                paper_worker.submit(rows)
            valid = True
            for row in rows:
                if stop.is_set() and not entries_stopped:
                    stop_entries(engine, row["received"])
                    entries_stopped = True
                if not engine.executor.risk.halted and (Path(settings.data_dir) / "HALT").exists():
                    engine.executor.halt(row["received"])
                row_valid = engine.ingest(row)
                valid = row_valid and valid
                payload = json.loads(row["payload"])
                if not stop.is_set():
                    if not row_valid:
                        recovery.request("DATA_INTEGRITY_FAILURE", time.time())
                    elif payload.get("type") in ("disconnect", "stale", "error"):
                        recovery.request("FEED_INTERRUPTED", time.time())
                recovery.observe(engine, row, payload, row_valid)
                if shadow is not None:
                    shadow.process(row, payload)
                if payload.get("type") == "settlement":
                    ticker = payload["msg"]["market_ticker"]
                    if store.state(engine.run_id, ticker) == "CLOSED":
                        loop.call_soon_threadsafe(settled_tickers.add, ticker)
                if (
                    payload.get("type") in ("cfbenchmarks_value_5hz", "pyth_value")
                    and payload.get("msg", {}).get(
                        "index_id", payload.get("msg", {}).get("underlying_ticker")
                    )
                    == config.asset_spec.index
                ):
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
            if (paper or live_signals) and time.monotonic() - last_history_save >= 30:
                save_history(settings.data_dir, engine.ticks, now, config)
                last_history_save = time.monotonic()
            lag = (time.monotonic_ns() - rows[-1]["monotonic_ns"]) / 1e9
            recovery.check(engine, now, lag, queue.qsize(), queue.maxsize, connected, stop.is_set())
            recovery_status = recovery.status()
            signature = (recovery_status["state"], tuple(recovery_status["reasons"]), recovery.warning)
            if signature != last_recovery_status:
                store.add("collector_recovery", recovery_status, engine.run_id, "PAPER", now)
                last_recovery_status = signature
            latest_live = max(engine.latest.values(), key=lambda b: b["timestamp"]) if engine.latest else None
            eligible_live = bool(
                latest_live
                and latest_live.get("decision") in ("NO_TRADE", "TRADE_CANDIDATE")
                and not [
                    r
                    for r in latest_live.get("reasons", [])
                    if r["code"] not in ("EXISTING_ENTRY", "POST_CLOSE_COOLDOWN")
                ]
            )
            live_version = (latest_live["ticker"], latest_live["timestamp"]) if latest_live else None
            publish_live = bool(
                (paper or live_signals)
                and latest_live
                and live_version != last_live_decision
                and (eligible_live or live_decision_eligible)
            )
            if time.monotonic() - last_display >= 0.05 or not connected or publish_live:
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
                        recovery=recovery_status,
                    )
                )
                last_display = time.monotonic()
            if now - last_status >= 1 or not connected:
                engine.flush_rejections(now)
                simulation = paper_worker.poll() if paper_worker else None
                save_status = store.add if record_all else store.publish_record
                save_status(
                    "status",
                    dict(
                        connected=connected,
                        run_id=engine.run_id,
                        mode="PAPER",
                        paper_execution=paper,
                        live_signals=live_signals,
                        paper_worker=simulation,
                        recording="signals_only"
                        if live_signals
                        else "full"
                        if record_all
                        else "trades_with_compact_inputs",
                        reference_preload=preload,
                        live_enabled=False,
                        models=[
                            dict(
                                run_id=e.run_id,
                                model=e.executor.model_identity,
                                entries_active=e.entries_active,
                                strategy_enabled=e.config.enabled,
                                halted=e.executor.risk.halted,
                                open_positions=len(simulation.get("positions", {}))
                                if simulation
                                else len(e.executor.positions),
                                realized_pnl=simulation.get("realized_pnl")
                                if simulation
                                else e.executor.risk.realized,
                            )
                            for e in ([engine])
                        ],
                        clock_ok=engine.clock_ok,
                        exchange_open=engine.exchange_open,
                        reference_age=now - engine.ticks[-1].received if engine.ticks else None,
                        processing_lag=(time.monotonic_ns() - rows[-1]["monotonic_ns"]) / 1e9,
                        maximum_queue=maximum_queue,
                        queue_depth=queue.qsize(),
                        queue_capacity=queue.maxsize,
                        recovery=recovery_status,
                        markets=list(engine.markets),
                        positions=simulation.get("positions", {})
                        if simulation
                        else {k: vars(v) for k, v in engine.executor.positions.items()},
                        venue_pauses=dict(engine.executor.venue_pauses),
                        settlement_recovery=simulation.get("settlement_recovery", {})
                        if simulation
                        else {
                            k: v
                            for k, v in engine.executor.quarantines.items()
                            if k in engine.executor.positions
                        },
                        exposure=simulation.get("exposure", 0)
                        if simulation
                        else sum(engine.executor.risk.reserved.values()),
                        daily=simulation.get("daily", {}) if simulation else engine.executor.risk.day(now),
                        halted=simulation.get("halted", False) if simulation else engine.executor.risk.halted,
                    ),
                    engine.run_id,
                    "PAPER",
                    now,
                )
                if recorder:
                    recorder.flush()
                if not record_all and not publish_live:
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
            if publish_live:
                store.publish_record(
                    "evaluation",
                    latest_live,
                    engine.run_id,
                    engine.mode,
                    latest_live["timestamp"],
                    latest_live["ticker"],
                    engine._last_op[latest_live["ticker"]],
                )
                last_live_decision = live_version
                live_decision_eligible = eligible_live
                if eligible_live and store.engine.dialect.name == "sqlite":
                    notify_decision(store.engine.url.database)
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
                valid = await work(process_batch, rows)
                for _ in rows:
                    pending_receipts.popleft()
                if not valid:
                    request_recovery("DATA_INTEGRITY_FAILURE")
                if recovery.drain.is_set() and not connected and queue.empty():
                    drained.set()
                if finished:
                    break

        async def refresh():
            nonlocal tickers
            while not stop.is_set():
                try:
                    # Fence the request before any network await. An in-flight active
                    # response started before a lifecycle pause cannot release it.
                    metadata_request_started_at = time.time()
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
                            "series/fee_changes",
                            {"series_ticker": config.asset_spec.series, "show_historical": True},
                        )
                    ).get("series_fee_change_arr", [])
                    status = await client.get("exchange/status")
                    emit(
                        dict(
                            type="metadata",
                            msg=dict(
                                series=series,
                                markets=markets,
                                discovery_resolutions=getattr(client, "discovery_resolutions", []),
                                source="kalshi_rest",
                                request_started_at=metadata_request_started_at,
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
                        # Later malformed/changed close times must not postpone held-contract polling.
                        tracked.setdefault(m["ticker"], m)
                    for ticker, m in list(tracked.items()):
                        if ticker in settled_tickers:
                            del tracked[ticker]
                            continue
                        from .domain import timestamp

                        if time.time() >= timestamp(m["close_time"]):
                            raw = (
                                await client.get("markets/" + ticker, {"exchange_index": m["exchange_index"]})
                            )["market"]
                            if raw.get("status") == "finalized" and raw.get("result") in ("yes", "no"):
                                emit(
                                    dict(
                                        type="settlement",
                                        msg=dict(
                                            market_ticker=ticker,
                                            result=raw["result"],
                                            evidence=dict(source="kalshi_rest", market=raw, series=series),
                                        ),
                                    )
                                )
                    metadata_ready.set()
                except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
                    emit(
                        dict(
                            type="stale",
                            msg={"reason": "metadata_refresh_failed", "error": type(exc).__name__},
                        )
                    )
                try:
                    await asyncio.wait_for(refresh_requested.wait(), timeout=15)
                except TimeoutError:
                    pass
                refresh_requested.clear()

        async def receive():
            nonlocal connection, connected
            await metadata_ready.wait()
            backoff = 1
            while not stop.is_set():
                if recovery.drain.is_set():
                    await drained.wait()
                    if stop.is_set():
                        break
                    await work(recovery.reconnect)
                    metadata_ready.clear()
                    refresh_requested.set()
                    await metadata_ready.wait()
                    if recovery.drain.is_set():
                        continue
                reconnect.clear()
                try:
                    async with websockets.connect(
                        settings.ws_url,
                        additional_headers=client.headers("GET", "/trade-api/ws/v2"),
                        ping_interval=20,
                        ping_timeout=20,
                        max_queue=2048,
                    ) as ws:
                        # Publish the new identity and its boundary without an
                        # await; heartbeat/metadata producers keep the old identity
                        # throughout the handshake, including failed attempts.
                        connection = str(uuid.uuid4())
                        connected = True
                        emit(dict(type="connected", msg={"tickers": tickers}))
                        for subscription in subscriptions(tickers, config.asset):
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
                    if not stop.is_set():
                        request_recovery("CONNECTION_LOST")
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
                            recovery=recovery.status(),
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

        async def seed_bleep():
            if not ((paper or live_signals) and config.bleep_exchange_seed_enabled):
                return
            from .bleep_seed import fetch_pyth_seed, fetch_seed

            if config.asset_spec.commodity and not settings.pyth_pro_api_key:
                store.add(
                    "bleep_seed_status",
                    dict(status="UNAVAILABLE", error="PYTH_PRO_API_KEY not configured"),
                    engine.run_id,
                    "PAPER",
                    time.time(),
                )
                return

            async with httpx.AsyncClient() as seed_client:
                while not stop.is_set():
                    try:
                        if config.asset_spec.commodity:
                            body = await fetch_pyth_seed(
                                seed_client, time.time(), config.asset, settings.pyth_pro_api_key
                            )
                        else:
                            body = await fetch_seed(seed_client, time.time(), config.asset)
                    except ValueError as exc:
                        store.add(
                            "bleep_seed_status",
                            dict(status="RETRYING", error=str(exc)),
                            engine.run_id,
                            "PAPER",
                            time.time(),
                        )
                        await asyncio.sleep(20)
                        continue
                    emit(dict(type="bleep_seed", msg=body))
                    store.add(
                        "bleep_seed_status",
                        dict(status="LOADED", provider=body["provider"], candles=len(body["candles"])),
                        engine.run_id,
                        "PAPER",
                        time.time(),
                    )
                    return

        async with asyncio.TaskGroup() as group:
            worker = group.create_task(consume())
            producers = [
                group.create_task(refresh()),
                group.create_task(receive()),
                group.create_task(clock()),
                group.create_task(reference_display()),
                group.create_task(seed_bleep()),
            ]
            await stop.wait()
            # Persist shutdown draining just like overload draining. Queued inputs
            # still update books/settlements, without expensive strategy evaluation.
            recovery.request("STOPPING", time.time())
            # Bounded cancellation also interrupts slow HTTP refresh/socket waits.
            for task in producers:
                task.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            emit(dict(type="disconnect", msg={"reason": "shutdown"}))
            await queue.put(None)
            await worker
        if paper and not separate_paper:
            with store.transaction():
                store.checkpoint(engine.run_id, engine.executor.snapshot())
        if paper_worker:
            await work(paper_worker.close)
        clean_shutdown = not paper_worker or not paper_worker.failed.is_set()
        return engine.run_id
    finally:
        try:
            try:
                if acquired and "engine" in locals():

                    def finish_audit():
                        now = time.time()
                        if paper or live_signals:
                            save_history(settings.data_dir, engine.ticks, now, config)
                        engine.flush_rejections(now, force=True)
                        for ticker in engine.executor.positions:
                            engine.monitoring_state(ticker, now, False, ["COLLECTOR_STOPPED"])

                    try:
                        await work(finish_audit)
                    finally:
                        if not clean_shutdown:
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
                                        open_positions=len(paper_worker.status.get("positions", {}))
                                        if paper_worker
                                        else len(engine.executor.positions),
                                        runs=[engine.run_id],
                                    ),
                                    owner,
                                    "PAPER",
                                    time.time(),
                                )
                            store.release("collector", owner)
                finally:
                    if paper_worker:
                        await work(paper_worker.close)
                    if signal_store:
                        signal_store.close()
                    pool.shutdown(wait=True)
                    if shadow is not None:
                        shadow.store.engine.dispose()


def backtest(path, config, store, parent_run=None):
    from .research import replay_files

    return replay_files([path], config, store, parent_run)
