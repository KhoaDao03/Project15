import httpx
from fastapi.testclient import TestClient

from btc15 import dashboard


def test_official_markets_without_collector_and_cached(store, raw, series, monkeypatch):
    calls = []

    class Client:
        def __init__(self, settings):
            assert not settings.api_key_id and not settings.private_key_path

        async def discover(self):
            calls.append(1)
            return series, [raw]

        async def close(self):
            pass

    monkeypatch.setattr(dashboard, "KalshiClient", Client)
    with TestClient(dashboard.create_app(store)) as client:
        first = client.get("/api/official-markets").json()
        second = client.get("/api/official-markets").json()
    assert first == second
    assert first["markets"][0]["ticker"] == raw["ticker"]
    assert first["markets"][0]["yes_ask_dollars"] == raw["yes_ask_dollars"]
    assert not first["stale"] and first["fetched_at"]
    assert len(calls) == 1
    assert not store.list(limit=None)


def test_official_failure_does_not_break_dashboard(store, monkeypatch):
    class Client:
        def __init__(self, settings):
            pass

        async def discover(self):
            raise httpx.ConnectError("unavailable")

        async def close(self):
            pass

    monkeypatch.setattr(dashboard, "KalshiClient", Client)
    with TestClient(dashboard.create_app(store)) as client:
        data = client.get("/api/official-markets").json()
        assert client.get("/").status_code == 200
        assert client.get("/api/health").status_code == 200
    assert data["stale"] and data["error"]
    assert data["markets"] == [] and data["fetched_at"] is None


def test_evaluation_follows_current_collector_and_explicit_run(store, monkeypatch):
    monkeypatch.setattr(dashboard.time, "time", lambda: 1000)
    store.add("opportunity", {"decision": "NO_TRADE"}, "old", "PAPER", 900)
    store.add("status", {"connected": True}, "current", "PAPER", 999)
    with TestClient(dashboard.create_app(store)) as client:
        waiting = client.get("/api/evaluation").json()
        assert waiting["record"] is None and waiting["collector_fresh"]
        assert "waiting" in waiting["message"]
        store.add("opportunity", {"decision": "NO_TRADE"}, "current", "PAPER", 1000)
        assert client.get("/api/evaluation").json()["record"]["run_id"] == "current"
        old = client.get("/api/evaluation?run_id=old").json()
        assert old["record"]["run_id"] == "old" and not old["collector_fresh"]
        live = client.get("/api/evaluation?mode=LIVE").json()
        assert live["record"] is None and "disabled" in live["message"]


def test_early_active_market_records_warmup_evaluation(store, config, market, book):
    from btc15.engine import Engine

    engine = Engine(store, config, "PAPER", execute=True)
    engine.markets[market.ticker] = market
    engine.books[market.ticker] = book
    now = market.open_time + 10
    assert market.close_time - now > config.entry_window_start
    engine.process(now, "early-heartbeat", "heartbeat", {})
    rows = store.list(kind="opportunity", run_id=engine.run_id)
    assert len(rows) == 1
    assert rows[0]["body"]["decision"] == "NO_TRADE"
    assert rows[0]["body"]["reasons"][0]["code"] == "MODEL_UNAVAILABLE"
    assert not store.list(kind="order")


def test_market_display_is_replaceable_without_research_writes(store):
    assert store.read_market_display() is None
    store.publish_market_display({"published_at": 1, "markets": []})
    store.publish_market_display({"published_at": 2, "markets": [{"ticker": "current"}]})
    assert store.read_market_display()["published_at"] == 2
    assert not store.list(limit=None)


def test_market_stream_marks_stopped_feed_stale(store):
    import asyncio
    import json
    import time

    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        app = dashboard.create_app(store)
        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/market-stream")
        store.publish_market_display({"published_at": time.time(), "connected": True, "markets": []})
        response = await endpoint(Request())
        stream = response.body_iterator
        first = json.loads((await anext(stream)).removeprefix("data: "))
        assert first["fresh"]
        store.publish_market_display({"published_at": time.time() - 5, "connected": True, "markets": []})
        second = json.loads((await anext(stream)).removeprefix("data: "))
        assert not second["fresh"]
        await stream.aclose()

    asyncio.run(run())


def test_dashboard_owns_collection_lifecycle(store, monkeypatch):
    from btc15 import runner
    from btc15.config import Settings

    calls = []

    async def collect(settings, config, actual_store, **kwargs):
        assert actual_store is store
        assert not kwargs.get("paper", False)
        assert kwargs["multi_model"] is True
        calls.append("started")
        await kwargs["stop_event"].wait()
        calls.append("stopped")

    monkeypatch.setattr(runner, "collect", collect)
    settings = Settings(api_key_id="test", private_key_path="unused")
    with TestClient(dashboard.create_app(store, collect_live=True, settings=settings)) as client:
        assert client.get("/api/health").status_code == 200
        assert calls == ["started"]
    assert calls == ["started", "stopped"]


def test_dashboard_reports_collection_failure_without_losing_ui(store, monkeypatch):
    from btc15 import runner
    from btc15.config import Settings

    async def collect(*args, **kwargs):
        raise RuntimeError("A writer owns this database")

    monkeypatch.setattr(runner, "collect", collect)
    settings = Settings(api_key_id="test", private_key_path="unused")
    with TestClient(dashboard.create_app(store, collect_live=True, settings=settings)) as client:
        assert client.get("/api/health").json()["collector_startup_error"]
        assert client.get("/").status_code == 200


def test_market_groups_count_before_pagination_and_filter_exact_market(store):
    for i in range(105):
        store.add('opportunity', {'decision': 'NO_TRADE'}, 'r1', 'PAPER', i, market='BTC-A')
    store.add('opportunity', {'decision': 'TRADE_CANDIDATE'}, 'r2', 'PAPER', 110, market='BTC-A')
    store.add('opportunity', {'decision': 'NO_TRADE'}, 'r1', 'PAPER', 111, market='BTC-AB')
    store.add('opportunity', {'decision': 'NO_TRADE'}, 'r1', 'BACKTEST', 112, market='BTC-C')
    with TestClient(dashboard.create_app(store)) as client:
        data = client.get('/api/records?group_by_market=true&limit=1').json()
        assert data['total'] == 2 and data['evaluations'] == 107
        assert data['rows'][0]['market'] == 'BTC-AB'
        group = client.get('/api/records?group_by_market=true&offset=1').json()['rows'][0]
        assert group['total'] == 106 and group['skipped'] == 105
        assert group['candidates'] == 1 and group['runs'] == 2
        exact = client.get('/api/records?market=BTC-A&offset=100').json()
        assert exact['total'] == 106 and len(exact['rows']) == 6
        assert all(r['market'] == 'BTC-A' for r in exact['rows'])
        filtered = client.get('/api/records?group_by_market=true&decision=TRADE_CANDIDATE&run_id=r2&search=BTC-A').json()
        assert filtered['total'] == 1 and filtered['evaluations'] == 1
        assert client.get('/api/records?group_by_market=true&mode=LIVE').json()['rows'] == []
