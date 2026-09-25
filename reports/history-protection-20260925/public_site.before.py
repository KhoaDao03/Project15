"""Read-only public website. Reads published files; never calls a private service."""

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

ASSETS = ("BTC", "ETH", "SOL", "XRP", "GOLD", "SILVER", "WTI")


def create_app(data_dir=None):
    directory = Path(data_dir or os.environ["PROJECT15_PUBLIC_DATA_DIR"])
    if not directory.is_dir():
        raise ValueError("Public data directory is unavailable")
    static = Path(__file__).with_name("public_static")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method not in ("GET", "HEAD"):
            response = JSONResponse({"detail": "Read-only website"}, status_code=405)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    def read(name, max_age):
        try:
            data = json.loads((directory / name).read_text())
            age = time.time() - data["updated_at"]
            data["stale"] = bool(data.get("stale")) or not 0 <= age <= max_age
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "Public data unavailable; try again later") from None

    @app.get("/api/view")
    def view():
        return read("view.json", 20)

    @app.get("/api/history/{asset}")
    def history(asset: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        if asset not in ASSETS:
            raise HTTPException(404, "Unknown market")
        data = read(f"history-{asset}.json", 120)
        return dict(rows=data["rows"][offset:offset + limit], total=data["total"],
                    offset=offset, limit=limit, stale=data["stale"])

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/viewer.js")
    def javascript():
        return FileResponse(static / "viewer.js", media_type="text/javascript")

    @app.get("/viewer.css")
    def stylesheet():
        return FileResponse(static / "viewer.css", media_type="text/css")

    @app.get("/logo.svg")
    def logo():
        return FileResponse(static / "logo.svg", media_type="image/svg+xml")

    return app
