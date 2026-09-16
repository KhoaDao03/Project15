"""One local dashboard over independently owned crypto paper ledgers."""

import asyncio
import json
import math
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from .analytics import lifetime_performance
from .assets import asset_spec
from .config import Settings, Strategy
from .dashboard import create_app
from .execution_service import ExecutionClient, install_execution_proxy
from .live_fallback import LiveFallbackStore
from .operation import health
from .shutdown import finish_shutdown
from .storage import Store


def load_members(path):
    path = Path(path).resolve()
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        raise ValueError("Dashboard manifest requires one to four asset runs")
    members, directories, databases = {}, set(), set()
    for row in rows:
        asset = asset_spec(row["asset"])
        directory = (path.parent / row["data_dir"]).resolve()
        config_path = (path.parent / row["config"]).resolve()
        config = Strategy.load(config_path)
        database = row.get("database_url", "sqlite:///" + str(directory / "paper.db"))
        if not database.startswith("sqlite:///") or database.endswith(":memory:"):
            raise ValueError("Shared dashboard requires file-backed SQLite paper ledgers")
        db_path = (path.parent / database.removeprefix("sqlite:///")).resolve()
        if asset.symbol in members or directory in directories or db_path in databases:
            raise ValueError("Assets, data directories and databases must be distinct")
        if config.asset != asset.symbol or not row.get("run_id"):
            raise ValueError("Manifest asset/configuration mismatch or missing run ID")
        directories.add(directory)
        databases.add(db_path)
        members[asset.symbol] = dict(
            config=config,
            data_dir=str(directory),
            database_url="sqlite:///" + str(db_path),
            run_id=row["run_id"],
            live_only=row.get("live_only", False),
        )
    return members


def open_fleet_stores(members):
    stores = {}
    try:
        for asset, member in members.items():
            store = stores[asset] = Store(member["database_url"])
            for run in store.run_summaries("PAPER"):
                model = run["body"].get("model") or {}
                if model.get("asset", "BTC") != asset:
                    raise ValueError("Manifest database contains a different asset")
                if run["run_id"] == member["run_id"]:
                    # Audited configuration migrations update the checkpoint while
                    # preserving the immutable original run record.
                    checkpoint = store.load_checkpoint(member["run_id"])
                    version = (
                        checkpoint["config_version"] if checkpoint else run["body"]["versions"]["config"]
                    )
                    if version != member["config"].version or (checkpoint and checkpoint["mode"] != "PAPER"):
                        raise ValueError("Manifest configuration does not match the retained run")
    except BaseException:
        for store in stores.values():
            store.engine.dispose()
        raise
    return stores


def create_fleet_app(manifest):
    members = load_members(manifest)
    stores = open_fleet_stores(members)
    history_stores = {
        asset: LiveFallbackStore(
            stores[asset], Path(manifest).resolve().parent / "manual-orders.sqlite", member["run_id"], asset
        )
        for asset, member in members.items()
    }
    execution = ExecutionClient(manifest)
    shutdown_state = dict(status="idle", message="")
    shutdown_task = None

    children = {}
    performance_cache = {}
    performance_revisions = {}
    performance_stop = asyncio.Event()

    def calculate_performance(asset, member):
        try:
            revision = history_stores[asset].history_revision("PAPER", member["run_id"])
            if (
                performance_cache.get(asset, {}).get("performance_updated_at") is not None
                and performance_revisions.get(asset) == revision
            ):
                return performance_cache[asset]
            with history_stores[asset].history_snapshot():
                results = history_stores[asset].list(
                    "trade_result", run_id=member["run_id"], mode="PAPER", limit=None
                )
                fills = [r for r in history_stores[asset].fallback() if r["kind"] == "fill"]
            result = dict(
                **lifetime_performance(results, fills),
                realized_pnl=math.fsum(r["body"]["net_pnl"] for r in results),
                performance_updated_at=time.time(),
            )
            performance_revisions[asset] = revision
            return result
        except (SQLAlchemyError, sqlite3.Error, OSError):
            return dict(realized_pnl=None, performance_updated_at=None)

    async def refresh_performance():
        values = await asyncio.gather(
            *(asyncio.to_thread(calculate_performance, asset, member) for asset, member in members.items())
        )
        performance_cache.update(zip(members, values))
        await asyncio.gather(
            *(asyncio.to_thread(child.state.refresh_recent_trades) for child in children.values())
        )

    async def performance_worker():
        while not performance_stop.is_set():
            try:
                await asyncio.wait_for(performance_stop.wait(), timeout=5)
            except TimeoutError:
                await refresh_performance()

    @asynccontextmanager
    async def lifespan(app):
        performance_stop.clear()
        await refresh_performance()
        performance_task = asyncio.create_task(performance_worker())
        try:
            yield
        finally:
            if shutdown_task and not shutdown_task.done():
                shutdown_task.cancel()
                await asyncio.gather(shutdown_task, return_exceptions=True)
            performance_stop.set()
            await performance_task
            await execution.close()
            for history in history_stores.values():
                history.close_history()
            for store in stores.values():
                store.engine.dispose()

    app = FastAPI(title="Project15 crypto paper dashboard", lifespan=lifespan)
    app.state.shutdown_server = None
    install_execution_proxy(app, execution)

    def summary(asset, member):
        base = dict(
            asset=asset,
            run_id=member["run_id"],
            url=f"/assets/{asset}/",
            reference_index=member["config"].asset_spec.index,
            reference_digits=member["config"].asset_spec.round_digits,
        )
        try:
            report = health(stores[asset], member["run_id"], time.time())
            status = report.get("status", {})
            reference = stores[asset].read_market_display("reference") or {}
            snapshot = stores[asset].read_market_display() or {}
            # A snapshot may be published while this request is being processed.
            # Compare with a clock sampled after both reads, never request-start time.
            now = time.time()
            tick = reference.get("reference_5hz") or {}
            price = None
            try:
                value = float(tick["value"])
                if (
                    reference.get("run_id") == member["run_id"]
                    and reference.get("connected")
                    and 0 <= now - reference["published_at"] < 2
                    and 0 <= now - tick["received"] < 2
                    and -2 <= now - float(tick["source_ts_ms"]) / 1000 < 2
                    and math.isfinite(value)
                    and value > 0
                ):
                    price = value
            except (KeyError, TypeError, ValueError):
                pass
            performance = performance_cache.get(asset, {})
            markets = []
            if snapshot.get("run_id") == member["run_id"]:
                fresh = bool(snapshot.get("connected") and 0 <= now - snapshot.get("published_at", 0) < 2)
                markets = [
                    {
                        **market,
                        "fresh": fresh and market.get("fresh", False),
                        "book": market.get("book", {}) if fresh and market.get("fresh") else {},
                    }
                    for market in snapshot.get("markets", [])
                ]
            return dict(
                **base,
                recent_trade_version=children[asset].state.recent_trade_version,
                recent_trades_stale=children[asset].state.recent_trades_stale(),
                healthy=report["healthy"],
                operational=report["operational"],
                paper_worker=status.get("paper_worker"),
                price=price,
                open_positions=len(status.get("positions", {})) + performance.get("open_trades", 0),
                realized_pnl=performance.get("realized_pnl"),
                performance_updated_at=performance.get("performance_updated_at"),
                completed_trades=performance.get("completed_trades"),
                wins=performance.get("wins"),
                losses=performance.get("losses"),
                breakeven_trades=performance.get("breakeven_trades"),
                win_rate=performance.get("win_rate"),
                current_streak=performance.get("current_streak"),
                longest_win_streak=performance.get("longest_win_streak"),
                longest_loss_streak=performance.get("longest_loss_streak"),
                markets=markets,
                daily_pnl=status.get("daily", {}).get("pnl"),
                exposure=status.get("exposure"),
                reasons=report["reasons"],
            )
        except (SQLAlchemyError, OSError):
            return dict(
                **base,
                healthy=False,
                price=None,
                realized_pnl=None,
                open_positions=None,
                operational=dict(state="UNAVAILABLE", summary="Ledger temporarily unavailable."),
                reasons=["LEDGER_UNAVAILABLE"],
            )

    @app.get("/api/fleet")
    async def overview():
        rows = await asyncio.gather(*(asyncio.to_thread(summary, a, m) for a, m in members.items()))
        execution_status = await execution.overview()
        purchases = execution_status["purchases"]
        for row in rows:
            for market in row.get("markets", []):
                market["manual_purchases"] = (
                    purchases.get(market["ticker"], dict(yes="0", no="0", pending=0))
                    if purchases is not None
                    else None
                )
        known = [r["realized_pnl"] for r in rows if r.get("realized_pnl") is not None]
        return dict(
            assets=rows,
            live_only=all(m.get("live_only") for m in members.values()),
            live=execution_status["live"],
            server_time=time.time(),
            default_asset=next(iter(members)),
            realized_pnl=sum(known) if known else None,
            totals_complete=len(known) == len(rows),
        )

    @app.get("/api/shutdown")
    def shutdown_status():
        return dict(shutdown_state)

    async def stop_all():
        states = {}

        async def stop_one(asset, store):
            state = states[asset] = dict(status="stopping")
            try:

                def request_stop():
                    with store.transaction():
                        owner = store.writer_owner()
                        if owner:
                            store.add(
                                "shutdown_request",
                                dict(source="shared_dashboard"),
                                owner,
                                "PAPER",
                                time.time(),
                            )
                        return owner

                owner = await asyncio.to_thread(request_stop)
                await finish_shutdown(store, owner, state, lambda: None, close_delay=0)
            except Exception as exc:
                state.update(status="failed", message=type(exc).__name__)

        await asyncio.gather(*(stop_one(a, s) for a, s in stores.items()))
        failed = [a for a, s in states.items() if s["status"] != "stopped"]
        shutdown_state.update(assets=states)
        if failed:
            shutdown_state.update(
                status="failed",
                message="Shutdown not confirmed for " + ", ".join(failed) + ". Dashboard remains open.",
            )
        else:
            shutdown_state.update(
                status="stopped",
                message="All paper collectors stopped safely. Dashboard is closing.",
                open_positions=sum(s.get("open_positions") or 0 for s in states.values()),
            )
            await asyncio.sleep(2)
            app.state.shutdown_server()

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
        if shutdown_state["status"] not in ("stopping", "stopped"):
            await execution.prepare_shutdown()
            shutdown_state.update(
                status="stopping", message="Stopping all paper collectors and saving positions…"
            )
            shutdown_task = asyncio.create_task(stop_all())
        return dict(shutdown_state)

    for asset, member in members.items():
        child = create_app(
            history_stores[asset],
            settings=Settings(data_dir=member["data_dir"], asset=asset),
            config=member["config"],
            run_id=member["run_id"],
            cache_recent_trades=True,
        )
        children[asset] = child
        app.mount(f"/assets/{asset}", child)
        if asset == next(iter(members)):
            default = child
    # Keep the existing root dashboard and API bookmarks working for the first asset.
    app.mount("/", default)
    return app
