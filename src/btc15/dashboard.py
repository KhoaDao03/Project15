import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analytics import metrics
from .config import Settings
from .storage import Store


def create_app(store=None):
    store = store or Store(Settings.env().database_url)
    app = FastAPI(title="BTC15 Research", version="0.1.0")
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/health")
    def health():
        status = store.list(kind="status", limit=None)
        latest = status[-1] if status else None
        age = time.time() - latest["timestamp"] if latest else None
        return dict(
            database="ok",
            server_time=time.time(),
            live_enabled=False,
            collector=latest["body"] if latest else None,
            status_age=age,
            collector_fresh=age is not None and 0 <= age < 5,
        )

    @app.get("/api/runs")
    def runs(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$")):
        return store.list(kind="run", mode=mode, limit=None)[-100:][::-1]

    @app.get("/api/records")
    def records(
        kind: str = "opportunity",
        mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),
        run_id: str | None = None,
        search: str = "",
        decision: str = "",
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
        rows = store.list(kind=kind, mode=mode, run_id=run_id, limit=None)[::-1]
        if search:
            rows = [r for r in rows if search.lower() in (r["id"] + " " + r["market"]).lower()]
        if decision:
            rows = [r for r in rows if r["body"].get("decision") == decision]
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
