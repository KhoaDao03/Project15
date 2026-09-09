import asyncio
import json
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analytics import lifetime_performance, metrics
from .api import KalshiClient
from .config import Settings, Strategy
from .domain import dumps
from .models import definitions, identity
from .storage import Store
from .strategies.momentum import Momentum


def create_app(store=None, *, collect_live=False, settings=None, config=None):
    store = store or Store(Settings.env().database_url)
    strategy_path = Path((settings or Settings.env()).data_dir) / "strategy.json"
    session_config = config or Strategy.load(strategy_path if strategy_path.exists() else None)
    feed_error = None
    shutdown_task = None
    shutdown_state = dict(status="idle", message="")

    @asynccontextmanager
    async def lifespan(app):
        nonlocal feed_error
        task = None
        stop = asyncio.Event()
        if collect_live:
            actual_settings = settings or Settings.env()
            if actual_settings.api_key_id and actual_settings.private_key_path:
                from .runner import collect

                async def run_feed():
                    nonlocal feed_error
                    try:
                        await collect(
                            actual_settings, session_config, store, stop_event=stop, multi_model=True
                        )
                    except Exception as exc:
                        feed_error = (
                            "Dashboard could not start collection. Check the collector log and writer lease."
                        )
                        logging.getLogger("btc15").error("Dashboard collection stopped: %s", exc)

                task = asyncio.create_task(run_feed())
            else:
                feed_error = "Configure Kalshi API credentials to enable streaming prices."
        try:
            yield
        finally:
            if shutdown_task and not shutdown_task.done():
                shutdown_task.cancel()
                await asyncio.gather(shutdown_task, return_exceptions=True)
            if task:
                stop.set()
                await task

    app = FastAPI(title="BTC15 Research", version="0.1.0", lifespan=lifespan)
    app.state.shutdown_server = None

    @app.get("/api/shutdown")
    def shutdown_status():
        return dict(shutdown_state)

    @app.post("/api/shutdown", status_code=202)
    async def shutdown(request: Request):
        nonlocal shutdown_task
        if request.url.hostname not in ("localhost", "127.0.0.1", "::1") or request.headers.get(
            "origin"
        ) != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Shutdown must come from this local dashboard")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc
        if body != {"confirm": True} or type(body.get("confirm")) is not bool:
            raise HTTPException(422, "Explicit shutdown confirmation required")
        if app.state.shutdown_server is None:
            raise HTTPException(503, "This server does not support dashboard shutdown")
        if shutdown_state["status"] in ("stopping", "stopped"):
            return dict(shutdown_state)
        from .shutdown import finish_shutdown

        with store.transaction():
            owner = store.writer_owner()
            if owner:
                store.add("shutdown_request", dict(source="dashboard"), owner, "PAPER", time.time())
        shutdown_state.update(
            status="stopping", message="Stopping entries, saving positions and flushing data…"
        )
        shutdown_task = asyncio.create_task(
            finish_shutdown(store, owner, shutdown_state, app.state.shutdown_server)
        )
        return dict(shutdown_state)

    official = dict(markets=[], fetched_at=None, error=None)
    official_lock = asyncio.Lock()
    next_refresh = 0.0
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/strategy")
    def strategy_settings():
        saved = Strategy.load(strategy_path) if strategy_path.exists() else session_config
        return dict(
            id="settlement_edge",
            name="BTC15 Settlement Edge",
            config=asdict(saved),
            version=saved.version,
            session_version=session_config.version,
        )

    @app.put("/api/strategy")
    async def save_strategy(request: Request):
        # A cross-origin web page must not change local trading settings.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Strategy changes require the dashboard's own origin")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected strategy settings")
            saved = Strategy(**body)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        strategy_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=strategy_path.parent, delete=False) as f:
                temporary = f.name
                json.dump(asdict(saved), f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, strategy_path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return dict(version=saved.version, message="Saved for new sessions. Running sessions are unchanged.")

    @app.get("/api/market-stream")
    async def market_stream(request: Request):
        async def events():
            previous = None
            last_send = 0
            while shutdown_state["status"] != "stopped" and not await request.is_disconnected():
                snapshot = await asyncio.to_thread(store.read_market_display)
                reference = await asyncio.to_thread(store.read_market_display, "reference")
                now = time.time()
                fresh = bool(snapshot and snapshot["connected"] and 0 <= now - snapshot["published_at"] < 2)
                key = (
                    snapshot.get("published_at") if snapshot else None,
                    fresh,
                    reference.get("published_at") if reference else None,
                )
                if key != previous or time.monotonic() - last_send >= 1:
                    yield (
                        "data: "
                        + dumps(dict(snapshot=snapshot, reference=reference, fresh=fresh, server_time=now))
                        + "\n\n"
                    )
                    previous = key
                    last_send = time.monotonic()
                await asyncio.sleep(0.05)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/official-markets")
    async def official_markets():
        nonlocal next_refresh
        async with official_lock:
            if time.monotonic() >= next_refresh:
                # Public discovery needs no credentials or collector writer lease.
                client = KalshiClient(Settings())
                try:
                    async with asyncio.timeout(8):
                        _, markets = await client.discover()
                    official.update(
                        markets=[
                            {
                                k: m.get(k)
                                for k in (
                                    "ticker",
                                    "title",
                                    "status",
                                    "close_time",
                                    "floor_strike",
                                    "yes_bid_dollars",
                                    "yes_ask_dollars",
                                    "no_bid_dollars",
                                    "no_ask_dollars",
                                    "volume_fp",
                                )
                            }
                            for m in markets
                        ],
                        fetched_at=time.time(),
                        error=None,
                    )
                except (httpx.HTTPError, OSError, TimeoutError, ValueError, KeyError):
                    official["error"] = "Official market data is temporarily unavailable."
                finally:
                    await client.close()
                    next_refresh = time.monotonic() + 15
        return {**official, "stale": bool(official["error"]), "source": "Kalshi public REST"}

    @app.get("/api/health")
    def health():
        status = store.list(kind="status", limit=1, newest_first=True)
        latest = status[0] if status else None
        age = time.time() - latest["timestamp"] if latest else None
        return dict(
            database="ok",
            server_time=time.time(),
            live_enabled=False,
            collector_startup_error=feed_error,
            collector=latest["body"] if latest else None,
            status_age=age,
            collector_fresh=age is not None and 0 <= age < 5,
        )

    @app.get("/api/runs")
    def runs(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$")):
        return store.list(kind="run", mode=mode, limit=100, newest_first=True)

    @app.get("/api/strategies")
    def strategies(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$")):
        saved = Strategy.load(strategy_path) if strategy_path.exists() else session_config
        entries = {}

        def include(model, enabled=None):
            key = (model["model_id"], model["model_version"], model["config_hash"])
            return entries.setdefault(key, dict(model=model, entries_enabled=enabled, runs=[], record=None))

        if saved.enabled:
            include(identity(saved), True)
        for definition in definitions(store).values():
            if definition["active"]:
                include(identity(Momentum(**definition["config"])), True)
        # Show active configurations only; historical records remain available by run.
        for run in store.list(kind="run", mode=mode, limit=None, newest_first=True):
            body = run["body"]
            model = body.get("model") or {
                **identity(session_config),
                "config_hash": body.get("versions", {}).get("config", "legacy"),
            }
            key = (model["model_id"], model["model_version"], model["config_hash"])
            entry = entries.get(key)
            if entry is None:
                continue
            entry["runs"].append(dict(run_id=run["run_id"], timestamp=run["timestamp"]))
            rows = store.list(kind="opportunity", mode=mode, run_id=run["run_id"], limit=1, newest_first=True)
            if rows and (entry["record"] is None or rows[0]["timestamp"] > entry["record"]["timestamp"]):
                entry["record"] = rows[0]
        results = store.list(kind="trade_result", mode=mode, limit=None)

        fills = store.list(kind="fill", mode=mode, limit=None)
        by_run = {}
        fills_by_run = {}
        for result in results:
            by_run.setdefault(result["run_id"], []).append(result)
        for fill in fills:
            fills_by_run.setdefault(fill["run_id"], []).append(fill)
        for entry in entries.values():
            entry["lifetime"] = lifetime_performance(
                [result for run in entry["runs"] for result in by_run.get(run["run_id"], [])],
                [fill for run in entry["runs"] for fill in fills_by_run.get(run["run_id"], [])],
            )
        now = time.time()
        statuses = store.list(kind="status", mode=mode, newest_first=True, limit=1)
        status = statuses[0] if statuses else None
        body = status["body"] if status else {}
        connected = bool(status and 0 <= now - status["timestamp"] < 5 and body.get("connected"))
        members = body.get("models", [])
        for entry in entries.values():
            member = next((m for m in members if m.get("model") == entry["model"]), None)
            record = entry["record"]
            age = now - record["timestamp"] if record else None
            current = bool(member and record and record["run_id"] == member["run_id"])
            entry["evaluation_age"] = age
            entry["feed_status"] = (
                "stopped"
                if not connected or member is None
                else "current"
                if current and age is not None and 0 <= age < 5
                else "waiting_or_stale"
            )
            entry["paper_execution"] = bool(connected and member and body.get("paper_execution"))
        return dict(
            rows=list(entries.values()),
            mode=mode,
            server_time=now,
            lifetime=lifetime_performance(results, fills),
        )

    @app.get("/api/evaluation")
    def evaluation(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"), run_id: str | None = None):
        now = time.time()
        statuses = store.list(kind="status", mode=mode, run_id=run_id, limit=1, newest_first=True)
        status = statuses[0] if statuses else None
        if run_id:
            latest = store.list(kind="status", mode=mode, limit=1, newest_first=True)
            if latest and any(m["run_id"] == run_id for m in latest[0]["body"].get("models", [])):
                status = latest[0]
        selected = run_id or (status["run_id"] if status and mode == "PAPER" else None)
        rows = store.list(kind="opportunity", mode=mode, run_id=selected, limit=1, newest_first=True)
        row = rows[0] if rows else None
        fresh = bool(status and 0 <= now - status["timestamp"] < 5 and status["body"].get("connected"))
        age = now - row["timestamp"] if row else None
        if mode == "LIVE":
            message = "LIVE execution is disabled. Select PAPER for current research evaluations."
        elif mode == "BACKTEST":
            message = (
                "Recorded backtest evaluation." if row else "No backtest evaluations for this selection."
            )
        elif not fresh:
            message = "Collector stopped or stale. Start btc15 collect to generate new evaluations."
        elif not row:
            message = "Collector connected; waiting for a valid market and reference samples."
        elif age is not None and age > 10:
            message = "Collector connected; last evaluation is older than 10 seconds. Awaiting the next eligible market update."
        else:
            message = "Evaluations updating from the authenticated collector."
        return dict(record=row, message=message, evaluation_age=age, collector_fresh=fresh, server_time=now)

    @app.get("/api/records")
    def records(
        kind: str = "opportunity",
        mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),
        run_id: str | None = None,
        search: str = "",
        decision: str = "",
        market: str | None = None,
        group_by_market: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        if kind not in (
            "opportunity",
            "trade_result",
            "order",
            "fill",
            "health",
            "market",
            "status",
            "experiment",
        ):
            raise HTTPException(400, "Unsupported record kind")
        rows = store.list(kind=kind, mode=mode, run_id=run_id, market=market, limit=None)[::-1]
        if search:
            rows = [r for r in rows if search.lower() in (r["id"] + " " + r["market"]).lower()]
        if decision:
            rows = [r for r in rows if r["body"].get("decision") == decision]
        if group_by_market:
            groups = {}
            for row in rows:
                group = groups.setdefault(
                    row["market"],
                    dict(
                        market=row["market"],
                        total=0,
                        skipped=0,
                        candidates=0,
                        latest_at=row["timestamp"],
                        runs=set(),
                    ),
                )
                group["total"] += 1
                group["skipped"] += row["body"].get("decision") == "NO_TRADE"
                group["candidates"] += row["body"].get("decision") == "TRADE_CANDIDATE"
                group["runs"].add(row["run_id"])
            summaries = [{**g, "runs": len(g["runs"])} for g in groups.values()]
            return dict(total=len(summaries), evaluations=len(rows), rows=summaries[offset : offset + limit])
        return dict(total=len(rows), rows=rows[offset : offset + limit])

    @app.get("/api/trades")
    def trades(
        mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),
        run_id: str | None = None,
        search: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        opportunities = {
            r["id"]: r["body"] for r in store.list(kind="opportunity", mode=mode, run_id=run_id, limit=None)
        }
        rows = []
        for r in reversed(store.list(kind="trade_result", mode=mode, run_id=run_id, limit=None)):
            if search.lower() not in (r["opportunity_id"] + " " + r["market"]).lower():
                continue
            b = r["body"]
            op = opportunities.get(r["opportunity_id"], {})
            rows.append(
                {
                    **r,
                    "body": {
                        **b,
                        "entry": b["cost"] / b["bought"],
                        "exit": b["proceeds"] / b["bought"],
                        "probability": op.get("probability", {}),
                        "conservative_probability": op.get("conservative_probability"),
                        "expected_fill_price": op.get("expected_fill_price"),
                        "net_ev": op.get("net_ev"),
                        "versions": op.get("versions", {}),
                    },
                }
            )
        return {"total": len(rows), "rows": rows[offset : offset + limit]}

    @app.get("/api/replay/{opportunity_id}")
    def replay(opportunity_id: str):
        rows = store.list(opportunity_id=opportunity_id, limit=None)
        op = next((r for r in rows if r["kind"] == "opportunity"), None)
        if op is None:
            raise HTTPException(404, "Unknown opportunity")
        path = store.list(kind="opportunity", run_id=op["run_id"], market=op["market"], limit=None)
        timeline = store.list(run_id=op["run_id"], market=op["market"], limit=None)
        return dict(
            opportunity=op,
            timeline=[
                r
                for r in timeline
                if r["kind"]
                in (
                    "transition",
                    "order",
                    "fill",
                    "trade_result",
                    "settlement",
                    "exit_intent",
                    "execution_rejection",
                )
            ],
            path=path,
        )

    @app.get("/api/analytics")
    def analytics(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"), run_id: str | None = None):
        return metrics(store, mode, run_id)

    return app
