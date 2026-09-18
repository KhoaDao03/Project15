"""GET-only standalone dashboard; never mounts the existing trading application."""

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from .recording import metadata, read_connection


def create_app(path):
    path = Path(path).resolve()
    meta = metadata(path)
    app = FastAPI(title="BTC probability research", docs_url=None, redoc_url=None, openapi_url=None)
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def read_only(request, call_next):
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "Read-only probability dashboard"}, status_code=405)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(static / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    def style():
        return FileResponse(static / "style.css", media_type="text/css")

    @app.get("/health")
    def health():
        with read_connection(path) as db:
            last = db.execute("SELECT MAX(received_time) FROM events").fetchone()[0]
        return dict(
            read_only=True,
            recording_available=True,
            last_event_time=last,
            mode=meta["mode"],
            note="Recording health does not imply available or fresh model inputs",
        )

    @app.get("/api/markets")
    def markets():
        with read_connection(path) as db:
            rows = db.execute(
                "SELECT market,provider,MAX(tick_seq) FROM forecasts GROUP BY market,provider "
                "ORDER BY MAX(tick_seq) DESC,market,provider"
            ).fetchall()
        return dict(
            mode=meta["mode"],
            default_provider=json.loads(meta["config"])["provider"],
            markets=[dict(market=r[0], provider=r[1]) for r in rows],
        )

    @app.get("/api/forecasts")
    def forecasts(market: str, provider: str, after: int = 0, limit: int = Query(2000, ge=1, le=5000)):
        with read_connection(path) as db:
            rows = db.execute(
                "SELECT tick_seq,data FROM forecasts WHERE market=? AND provider=? AND tick_seq>? "
                "ORDER BY tick_seq LIMIT ?",
                (market, provider, after, limit),
            ).fetchall()
        return [dict(seq=r[0], **json.loads(r[1])) for r in rows]

    return app
