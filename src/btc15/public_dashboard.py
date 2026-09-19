"""Public snapshots and narrowly scoped owner controls with 60-second unlocks."""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse


def pick(source, names):
    return {name: source.get(name) for name in names.split()}


async def read_snapshot(client):
    response = await client.get("/api/fleet")
    response.raise_for_status()
    fleet = response.json()
    assets = []
    for row in fleet["assets"]:
        asset = row["asset"]
        if asset not in ("BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI"):
            continue
        public = pick(row, "asset healthy price open_positions realized_pnl wins losses completed_trades "
                      "win_rate current_streak longest_win_streak longest_loss_streak")
        public["live_policy"] = pick(fleet.get("live", {}).get("assets", {}).get(asset, {}), "enabled contracts revision loss_guard")
        public["state"] = row.get("operational", {}).get("state", "UNAVAILABLE")
        public["markets"] = [
            pick(market, "ticker fresh") for market in row.get("markets", [])
        ]
        trades = await client.get(
            f"/assets/{asset}/api/trades",
            params=dict(run_id=row["run_id"], mode="PAPER", include_open="true", limit=5),
        )
        trades.raise_for_status()
        data = trades.json()
        public["trades_stale"] = bool(data.get("stale", False))
        public["trades"] = [
            {
                **pick(trade, "market timestamp"),
                **pick(trade["body"], "status side bought quantity entry exit fees net_pnl opened market_result exit_timestamp exit_type"),
            }
            for trade in data["rows"][:5]
        ]
        assets.append(public)
    return dict(
        assets=assets,
        live_only=bool(fleet.get("live_only", False)),
        live_available=bool(fleet.get("live", {}).get("available") and fleet.get("live", {}).get("running")),
        realized_pnl=fleet.get("realized_pnl"),
        totals_complete=bool(fleet.get("totals_complete", False)),
        updated_at=time.time(),
    )


def create_public_app(admin_port=8000):
    if not 1 <= admin_port <= 65535:
        raise ValueError("Invalid private dashboard port")
    snapshot = None
    failed = True
    stop = asyncio.Event()
    secret = None
    secret_path = os.environ.get("PROJECT15_SHUTDOWN_SECRET_FILE")
    if secret_path and Path(secret_path).exists():
        secret = json.loads(Path(secret_path).read_text())
        # Fail startup on malformed configuration, never accept a default passcode.
        if len(bytes.fromhex(secret["salt"])) != 16 or len(bytes.fromhex(secret["digest"])) != 32:
            raise ValueError("Invalid shutdown passcode file")
    attempts = deque()
    action_lock = asyncio.Lock()
    sessions = {}
    shutdown_tasks = {}
    asset_shutdown_states = {}
    shutdown_state = dict(status="idle", message="")
    upstream = None

    def passcode_matches(passcode):
        digest = hashlib.pbkdf2_hmac(
            "sha256", passcode.encode(), bytes.fromhex(secret["salt"]), 600_000
        )
        return hmac.compare_digest(digest, bytes.fromhex(secret["digest"]))

    async def watch_shutdown(asset=None):
        state = shutdown_state if asset is None else asset_shutdown_states[asset]
        path = "/api/shutdown" if asset is None else f"/api/bots/{asset}/shutdown"
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                response = await upstream.get(path)
                response.raise_for_status()
                status = response.json()["status"]
                if status == "stopped":
                    state.update(status="stopped", message=("All bot collectors stopped; live entries disabled." if asset is None else f"{asset} stopped; other bots are unchanged."))
                    return
                if status == "failed":
                    state.update(status="failed", message="Shutdown did not complete. Check the private dashboard.")
                    return
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                # A disconnected dashboard does not establish successful shutdown.
                pass
            await asyncio.sleep(1)
        state.update(status="unknown", message="Shutdown could not be confirmed. Check the private dashboard or services before retrying.")

    async def refresh(client):
        nonlocal snapshot, failed
        try:
            snapshot = await read_snapshot(client)
            failed = False
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Keep the last good view, clearly stale. Never publish upstream errors.
            failed = True

    async def worker(client):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                await refresh(client)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal upstream
        stop.clear()
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{admin_port}",
            timeout=3,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            upstream = client
            await refresh(client)
            task = asyncio.create_task(worker(client))
            try:
                yield
            finally:
                stop.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                for pending in shutdown_tasks.values():
                    pending.cancel()
                await asyncio.gather(*shutdown_tasks.values(), return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    static = Path(__file__).with_name("public_static")

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method not in ("GET", "HEAD") and not (
            request.method == "POST" and request.url.path in ("/api/unlock", "/api/stop", "/api/control")
        ):
            response = JSONResponse({"detail": "View-only dashboard"}, status_code=405)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/api/stop")
    def stop_status():
        return dict(enabled=secret is not None, **shutdown_state, assets=asset_shutdown_states)

    def same_origin(request):
        if secret is None:
            raise HTTPException(503, "Owner passcode is not configured")
        if request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Use this dashboard's controls")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")

    def authorize(request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if monotonic() >= sessions.get(token, 0):
            sessions.pop(token, None)
            raise HTTPException(401, "Unlock expired. Enter the passcode again.")

    async def small_json(request):
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 2048:
                        raise HTTPException(413, "Request too large")
                    raw.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, "Request timed out") from None
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(422, "Invalid request") from None
        if not isinstance(body, dict):
            raise HTTPException(422, "Invalid request")
        return body

    @app.post("/api/unlock")
    async def unlock(request: Request):
        same_origin(request)
        if action_lock.locked():
            raise HTTPException(429, "Another attempt is in progress. Wait before retrying.")
        async with action_lock:
            now = monotonic()
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(429, "Too many attempts. Wait one minute.", headers={"Retry-After": "60"})
            attempts.append(now)
            body = await small_json(request)
            passcode = body.get("passcode")
            if not isinstance(passcode, str) or not 16 <= len(passcode) <= 256:
                raise HTTPException(403, "Incorrect passcode")
            if not await asyncio.to_thread(passcode_matches, passcode):
                raise HTTPException(403, "Incorrect passcode")
            now = monotonic()
            for token, expiry in list(sessions.items()):
                if expiry <= now:
                    del sessions[token]
            token = secrets.token_urlsafe(32)
            sessions[token] = now + 60
            return dict(token=token, expires_in=60)

    @app.post("/api/control")
    async def control(request: Request):
        same_origin(request)
        authorize(request)
        body = await small_json(request)
        if set(body) != {"asset", "ticker", "enabled", "contracts", "revision", "confirm"}:
            raise HTTPException(422, "Invalid live settings")
        if (type(body["enabled"]) is not bool or type(body["contracts"]) is not int
                or not 1 <= body["contracts"] <= 20 or type(body["revision"]) is not int
                or body["revision"] < 0):
            raise HTTPException(422, "Use 1–20 whole contracts and valid live settings")
        if body["enabled"] and body["confirm"] != "ENABLE_REAL_TRADING":
            raise HTTPException(422, "Confirm real-money trading")
        async with action_lock:
            authorize(request)
            if shutdown_state["status"] in ("stopping", "stopped", "unknown"):
                raise HTTPException(409, "Resolve shutdown before changing live settings")
            if failed or snapshot is None or time.time() - snapshot["updated_at"] > 20 or not snapshot.get("live_available"):
                raise HTTPException(409, "Live controls unavailable; wait for fresh data")
            if not isinstance(body["asset"], str):
                raise HTTPException(422, "Select a supported bot")
            if asset_shutdown_states.get(body["asset"], {}).get("status") in ("stopping", "stopped", "unknown"):
                raise HTTPException(409, "Resolve this bot shutdown before changing live settings")
            asset = next((row for row in snapshot["assets"] if row["asset"] == body["asset"]), None)
            if asset is None or not any(m["ticker"] == body["ticker"] and m["fresh"] for m in asset["markets"]):
                raise HTTPException(409, "Market changed; refresh before saving")
            payload = {key: value for key, value in body.items() if key != "asset"}
            try:
                response = await upstream.post("/api/live/control", json=payload,
                    headers={"Origin": f"http://127.0.0.1:{admin_port}"}, timeout=10)
                if response.status_code in (409, 422):
                    raise HTTPException(response.status_code, "Settings or market changed. Refresh and review before saving again.")
                response.raise_for_status()
                result = response.json()
                return pick(result, "enabled contracts revision")
            except (httpx.HTTPError, ValueError):
                raise HTTPException(502, "Save could not be confirmed. Refresh live settings before retrying.") from None

    @app.post("/api/stop", status_code=202)
    async def request_stop(request: Request):
        same_origin(request)
        authorize(request)
        body = await small_json(request)
        if set(body) - {"confirm", "asset"} or body.get("confirm") is not True:
            raise HTTPException(422, "Explicit shutdown confirmation required")
        asset = body.get("asset")
        if "asset" in body and (not isinstance(asset, str) or asset not in ("BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI")):
            raise HTTPException(422, "Select a supported bot")
        async with action_lock:
            authorize(request)
            if shutdown_state["status"] in ("stopping", "stopped", "unknown"):
                return dict(shutdown_state)
            state = shutdown_state if asset is None else asset_shutdown_states.setdefault(asset, dict(status="idle", message=""))
            if state["status"] in ("stopping", "stopped", "unknown"):
                return dict(state)
            path = "/api/shutdown" if asset is None else f"/api/bots/{asset}/shutdown"
            try:
                response = await upstream.post(
                    path, json={"confirm": True},
                    headers={"Origin": f"http://127.0.0.1:{admin_port}"}, timeout=10,
                )
                if response.status_code == 409:
                    raise HTTPException(409, "Shutdown blocked: a live position or order still needs management. Try again after it closes.")
                response.raise_for_status()
                if response.json().get("status") not in ("stopping", "stopped"):
                    raise ValueError("Unexpected shutdown acknowledgement")
            except (httpx.HTTPError, ValueError):
                state.update(status="unknown", message="Request outcome is uncertain. Check the private dashboard or services.")
                shutdown_tasks[asset or "all"] = asyncio.create_task(watch_shutdown(asset))
                raise HTTPException(502, state["message"]) from None
            state.update(status="stopping", message="Shutdown requested. Waiting for confirmation…")
            shutdown_tasks[asset or "all"] = asyncio.create_task(watch_shutdown(asset))
            return dict(state)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/viewer.js")
    def javascript():
        return FileResponse(static / "viewer.js", media_type="text/javascript")

    @app.get("/viewer.css")
    def stylesheet():
        return FileResponse(static / "viewer.css", media_type="text/css")

    @app.get("/api/view")
    def view():
        if snapshot is None:
            return JSONResponse({"detail": "Dashboard data unavailable", "stale": True}, status_code=503)
        return {**snapshot, "stale": failed or time.time() - snapshot["updated_at"] > 20}

    return app
