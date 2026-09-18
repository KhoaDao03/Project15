"""Bounded read-only collection. API failures are records, never substitute IVs."""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import Clock, DeribitReader, KalshiReader, history_references, normalize_reference
from .contracts import discover_spec
from .engine import Engine
from .recording import Recording
from .volatility import normalize_option

log = logging.getLogger(__name__)


async def record_live(path, config, *, key_id="", key_path="", duration=0, reviews_path=None):
    clock = Clock()
    recording = Recording(path, "live", config)
    engine = Engine(config, "live")
    recording.restore(engine)
    kalshi, deribit = KalshiReader(key_id, key_path, clock=clock), DeribitReader(clock=clock)
    reviews = json.loads(Path(reviews_path).read_text()) if reviews_path else {}
    queue = asyncio.Queue(maxsize=256)
    start = clock.monotonic()
    stop = asyncio.Event()

    # Receipt is assigned at arrival, processing time at reduction. One queue preserves arrival order.
    async def emit(kind, data, received=None, source=None):
        await queue.put((kind, data, clock.time() if received is None else received, source))

    async def failure(component, exc):
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if hasattr(exc, "response"):
            reason += ":" + str(exc.response.status_code)
        await emit("failure", dict(component=component, reason=reason))
        log.warning(json.dumps(dict(component=component, reason=reason)))

    async def metadata_loop():
        observed = set(engine.contracts)
        documents = {}
        documents_at = float("-inf")
        while not stop.is_set():
            try:
                if clock.time() - documents_at > 300:
                    try:
                        captured = await kalshi.rule_documents()
                        await emit("rule_documents", captured)
                        documents = {
                            k: {a: b for a, b in v.items() if a != "content_base64"}
                            for k, v in captured.items()
                        }
                        documents_at = clock.time()
                    except Exception as exc:
                        documents = {}
                        await failure("rule_documents", exc)
                series_response = await kalshi.get("series/" + config.series)
                response = await kalshi.get(
                    "markets", {"series_ticker": config.series, "status": "open", "limit": 100}
                )
                await emit("metadata_raw", dict(series=series_response, markets=response))
                markets = list(response["markets"])
                # Retain outcome/lifecycle evidence for previously discovered contracts.
                active = {m["ticker"] for m in markets}
                for ticker in sorted(observed - active):
                    if ticker in engine.contracts and engine.contracts[ticker].lifecycle not in (
                        "finalized",
                        "settled",
                    ):
                        markets.append((await kalshi.get("markets/" + ticker))["market"])
                for market in markets:
                    now = clock.time()
                    spec = discover_spec(
                        series_response["series"],
                        market,
                        now,
                        reviews.get(market["ticker"]),
                        documents=documents,
                    )
                    await emit(
                        "contract",
                        dict(
                            normalized=asdict(spec),
                            raw=dict(series=series_response, market=market, documents=documents),
                        ),
                        now,
                    )
                    observed.add(market["ticker"])
                    if market.get("result") in ("yes", "no"):
                        await emit(
                            "outcome",
                            dict(
                                market_ticker=market["ticker"],
                                yes=market["result"] == "yes",
                                source="kalshi-official-result",
                                raw=market,
                            ),
                        )
                await emit("recovery", dict(component="metadata"))
            except Exception as exc:
                await failure("metadata", exc)
            await asyncio.sleep(15)

    async def books_loop():
        while not stop.is_set():
            for ticker, spec in list(engine.contracts.items()):
                if spec.open_time <= clock.time() <= spec.trading_close_time:
                    try:
                        raw = await kalshi.get("markets/" + ticker + "/orderbook", {"depth": 10})
                        await emit(
                            "book",
                            dict(
                                market_ticker=ticker,
                                raw=raw,
                                normalized={"type": "rest_snapshot", "msg": raw},
                            ),
                        )
                    except Exception as exc:
                        await failure("book", exc)
            await asyncio.sleep(2)

    async def options_loop():
        while not stop.is_set():
            try:
                raw = await deribit.get(
                    "get_instruments", {"currency": "BTC", "kind": "option", "expired": "false"}
                )
                now = clock.time()
                instruments = [
                    i for i in raw["result"] if i.get("is_active") and i["expiration_timestamp"] / 1000 > now
                ]
                expiries = sorted(
                    {i["expiration_timestamp"] for i in instruments},
                    key=lambda t: abs(t / 1000 - now - config.preferred_expiry_hours * 3600),
                )[:2]
                await emit("instrument_discovery", dict(raw=raw))
                normalized, books = [], []
                refs = engine.history.causal(now)
                spot = float(refs[-1].value) if refs else None
                # When reference is absent, obtain a public option book's independent index as a selection aid only.
                if spot is None and instruments:
                    seed = await deribit.get(
                        "get_order_book", {"instrument_name": instruments[0]["instrument_name"], "depth": 1}
                    )
                    spot = seed["result"].get("index_price")
                    await emit("option_selection_reference", dict(raw=seed))
                if spot:
                    for expiry in expiries:
                        candidates = [
                            i
                            for i in instruments
                            if i["expiration_timestamp"] == expiry and i["option_type"] == "call"
                        ]
                        candidates.sort(key=lambda i: abs(i["strike"] - spot))
                        for instrument in candidates[:7]:
                            raw_book = await deribit.get(
                                "get_order_book",
                                {"instrument_name": instrument["instrument_name"], "depth": 1},
                            )
                            received = clock.time()
                            await emit("option_book_raw", dict(instrument=instrument, raw=raw_book), received)
                            normalized.append(normalize_option(instrument, raw_book["result"], received))
                            books.append(
                                dict(instrument=instrument, response=raw_book, received_time=received)
                            )
                await emit("options", dict(normalized=normalized, raw=books))
                await emit("recovery", dict(component="options"))
            except Exception as exc:
                await failure("options", exc)
            await asyncio.sleep(20)

    async def references_loop():
        if not kalshi.authenticated:
            await failure("reference", ValueError("REFERENCE_CREDENTIALS_MISSING"))
            return
        attempts = 0
        while not stop.is_set():
            try:
                await emit("connection", dict(reason="REFERENCE_RECONNECT"))
                async for frame, received in kalshi.reference_frames():
                    await emit("reference_raw", dict(raw=frame), received)
                    if frame.get("type") == "error":
                        raise ValueError("REFERENCE_ACCESS_OR_SUBSCRIPTION_ERROR")
                    if frame.get("type") == "cfbenchmarks_value":
                        ref = normalize_reference(frame, received)
                        await emit(
                            "reference", dict(normalized=asdict(ref), raw=frame), received, ref.source_time
                        )
                        for field in ("avg_60s_data", "last_60s_windowed_average_15min"):
                            aggregate = frame["msg"].get(field)
                            if aggregate:
                                await emit(
                                    "aggregate",
                                    dict(raw=aggregate, field=field, source_time=ref.source_time),
                                    received,
                                )
                        attempts = 0
                    else:
                        await emit("reference_control", dict(raw=frame), received)
            except Exception as exc:
                await failure("reference", exc)
                attempts += 1
                if attempts >= 5:
                    await failure("reference", ValueError("REFERENCE_RECONNECT_BUDGET_EXHAUSTED"))
                    return
                await asyncio.sleep(min(30, 2**attempts))

    async def repair_loop():
        if not kalshi.authenticated:
            return
        while not stop.is_set():
            needs = any(
                engine.history.snapshot(s, clock.time())[2]
                for s in engine.contracts.values()
                if s.sample_times and s.observation_end_time + 60 > clock.time()
            )
            if needs:
                try:
                    raw = await kalshi.history(datetime.now(timezone.utc).isoformat())
                    received = clock.time()
                    await emit("reference_history_raw", dict(raw=raw), received)
                    refs = history_references(raw, received)
                    if not refs:
                        await failure("history", ValueError("HISTORY_SCHEMA_UNVERIFIED_OR_EMPTY"))
                    for ref in refs:
                        # Each original receipt time remains fixed; delayed recovery cannot improve old forecasts.
                        await emit(
                            "reference", dict(normalized=asdict(ref), raw=None), received, ref.source_time
                        )
                except Exception as exc:
                    await failure("history", exc)
            await asyncio.sleep(30)

    async def ticks():
        while not stop.is_set():
            await emit("forecast_tick", {})
            if duration and clock.monotonic() - start >= duration:
                stop.set()
                return
            await asyncio.sleep(1)

    tasks = [
        asyncio.create_task(f())
        for f in (metadata_loop, books_loop, options_loop, references_loop, repair_loop, ticks)
    ]
    try:
        while not stop.is_set() or not queue.empty():
            try:
                kind, data, received, source = await asyncio.wait_for(queue.get(), timeout=1)
            except TimeoutError:
                continue
            # A blocked producer may enqueue an older receipt after a newer one. Preserve raw receipt,
            # but use a monotone availability timestamp for causal replay of the ordered queue.
            available = max(received, recording.last_received)
            data = dict(data, transport_received_time=received)
            if kind == "reference":
                data["normalized"]["received_time"] = available
            event = recording.append(kind, data, available, clock.time(), source)
            try:
                recording.forecasts(event["seq"], engine.apply(event))
                if kind == "outcome" and engine.last_reconciliation:
                    await emit("settlement_reconciliation", engine.last_reconciliation)
            except ValueError as exc:
                await failure("normalization", exc)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await kalshi.close()
        await deribit.close()
        recording.close()
