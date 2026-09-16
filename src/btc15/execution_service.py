"""Real-order ownership in one process, accessed through a private Unix socket."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from .live_automation import install_live_automation
from .manual_trading import install_manual_trading, local_request


def execution_socket(manifest):
    return Path(manifest).resolve().parent / "live-execution" / "api.sock"


def create_execution_app(manifest, *, settings=None, client_factory=None):
    from .fleet import load_members, open_fleet_stores

    members = load_members(manifest)
    stores = open_fleet_stores(members)
    worker = None
    stopping = False

    @asynccontextmanager
    async def lifespan(app):
        nonlocal worker, stopping
        stopping = False
        worker = asyncio.create_task(live.run())

        def finished(task):
            if not stopping and not task.cancelled():
                app.state.worker_failed = True
                callback = getattr(app.state, "shutdown_server", None)
                if callback:
                    callback()

        worker.add_done_callback(finished)
        try:
            await asyncio.sleep(0)
            if worker.done() or not live.running:
                raise RuntimeError("Live execution journal is already owned or worker startup failed")
            yield
        finally:
            stopping = True
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await manual.close()
            for store in stores.values():
                store.engine.dispose()

    app = FastAPI(title="Project15 live execution", lifespan=lifespan)
    options = dict(settings=settings)
    if client_factory is not None:
        options["client_factory"] = client_factory
    manual = install_manual_trading(app, Path(manifest).resolve().parent / "manual-orders.sqlite", **options)
    live = install_live_automation(app, manual, members, stores)
    app.state.worker_failed = False
    app.state.live = live
    app.state.manual = manual

    @app.middleware("http")
    async def require_owner(request, call_next):
        if not worker or worker.done() or not live.running:
            return Response(
                '{"detail":"Execution worker is unavailable; no action accepted"}',
                status_code=503,
                media_type="application/json",
            )
        return await call_next(request)

    @app.get("/api/execution/overview", dependencies=[Depends(local_request)])
    async def overview():
        return dict(
            live=dict(**live.state(), available=True, process_id=os.getpid()), purchases=manual.purchases()
        )

    @app.post("/api/execution/prepare-shutdown", dependencies=[Depends(local_request)])
    async def prepare_shutdown(request: Request):
        if await request.json() != {"confirm": True}:
            raise HTTPException(422, "Explicit shutdown confirmation required")
        async with manual.order_lock:
            live.prepare_shutdown()
        return dict(prepared=True)

    return app


class ExecutionClient:
    def __init__(self, manifest):
        self.http = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(execution_socket(manifest)), retries=0),
            base_url="http://localhost",
            timeout=30,
        )

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, *, content=None, query="", timeout=30):
        try:
            return await self.http.request(
                method,
                path + ("?" + query if query else ""),
                content=content,
                headers={"Origin": "http://localhost", "Content-Type": "application/json"},
                timeout=timeout,
            )
        except httpx.HTTPError:
            raise HTTPException(
                503,
                "Execution service unavailable. An interrupted order request may have "
                "been accepted; check its original order ID before retrying.",
            ) from None

    async def overview(self):
        try:
            response = await self.request("GET", "/api/execution/overview", timeout=2)
            response.raise_for_status()
            return response.json()
        except (HTTPException, httpx.HTTPError, ValueError):
            return dict(
                live=dict(
                    running=False,
                    available=False,
                    controls={},
                    assets={},
                    messages={},
                    error="Execution service unavailable; trading state cannot be confirmed",
                ),
                purchases=None,
            )

    async def prepare_shutdown(self):
        response = await self.request("POST", "/api/execution/prepare-shutdown", content=b'{"confirm":true}')
        if response.status_code != 200:
            try:
                detail = response.json().get("detail", "Execution service could not confirm safe shutdown")
            except ValueError:
                detail = "Execution service could not confirm safe shutdown"
            raise HTTPException(response.status_code, detail)


def install_execution_proxy(app, execution):
    async def forward(request: Request):
        response = await execution.request(
            request.method, request.url.path, content=await request.body(), query=request.url.query
        )
        return Response(
            response.content,
            status_code=response.status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    for prefix in ("manual", "live"):
        app.add_api_route(
            "/api/" + prefix + "/{path:path}",
            forward,
            methods=["GET", "POST"],
            dependencies=[Depends(local_request)],
        )
