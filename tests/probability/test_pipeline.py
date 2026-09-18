import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from btc_probability.adapters import DeribitReader, KalshiReader, normalize_reference
from btc_probability.books import Book
from btc_probability.contracts import discover_spec
from btc_probability.dashboard import create_app
from btc_probability.engine import Engine
from btc_probability.evaluation import evaluate
from btc_probability.recording import Recording, read_connection, read_events, replay
from btc_probability.schema import Config, Reference, encode
from btc_probability.synthetic import create_recording
from btc_probability.volatility import normalize_option, options_sigma

from .test_models import spec

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())["response"]


def apply(engine, kind, data, now):
    return engine.apply(dict(seq=engine.last_sequence + 1, kind=kind, data=data, received_time=now))


def test_book_fixed_point_gap_empty_reconnect():
    book = Book()
    snap = {
        "type": "orderbook_snapshot",
        "seq": 1,
        "sid": 2,
        "msg": {"yes_dollars_fp": [["0.4510", "2.50"]], "no_dollars_fp": [["0.5200", "3.75"]]},
    }
    book.apply(snap, 0)
    q = book.quotes(0, 3)
    assert q["yes_ask"] == "0.4800" and q["yes_ask_size"] == "3.75"
    assert q["midpoint"] == "0.4655"
    book.apply(
        {
            "type": "orderbook_delta",
            "seq": 3,
            "sid": 2,
            "msg": {"side": "yes", "price_dollars": "0.4510", "delta_fp": "1.0"},
        },
        1,
    )
    assert not book.quotes(1, 3)["valid"]
    book.apply(snap, 1)
    book.invalidate()
    assert book.quotes(1, 3)["yes_bid"] is None
    book.apply({"type": "rest_snapshot", "msg": {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}}, 2)
    assert book.quotes(2, 3)["midpoint"] is None


def test_raw_probability_independent_of_book_and_calibration_null():
    engine = Engine(Config(provider="fixed", paths=1000), "synthetic")
    apply(engine, "contract", {"normalized": asdict(spec())}, 0)
    apply(engine, "reference", {"normalized": asdict(Reference("100", 0, 0))}, 0)
    first = engine.forecast("SYNTHETIC-1", 0)
    apply(
        engine,
        "book",
        {
            "market_ticker": "SYNTHETIC-1",
            "normalized": {
                "type": "rest_snapshot",
                "msg": {"orderbook_fp": {"yes_dollars": [[".20", "1"]], "no_dollars": [[".70", "2"]]}},
            },
        },
        0,
    )
    second = engine.forecast("SYNTHETIC-1", 0)
    assert first.raw_p_yes == second.raw_p_yes
    assert first.quotes["midpoint"] is None and second.quotes["midpoint"] is not None
    assert second.calibrated_p_yes is None


def test_fail_closed_and_deterministic_without_volatility():
    engine = Engine(Config(paths=1000), "synthetic")
    s = spec()
    apply(engine, "contract", {"normalized": asdict(s)}, 0)
    assert "NO_REFERENCE" in engine.forecast(s.market_ticker, 0).reasons
    apply(engine, "reference", {"normalized": asdict(Reference("100", 0, 0))}, 0)
    assert "NO_LIQUID_SMILE" in engine.forecast(s.market_ticker, 0).reasons
    for t in range(1, 61):
        apply(engine, "reference", {"normalized": asdict(Reference("101", t, t))}, t)
    result = engine.forecast(s.market_ticker, 60)
    assert result.raw_p_yes == 1 and result.probability_kind == "deterministic"


def test_stale_reference_and_unverified_rules():
    engine = Engine(Config(provider="fixed", paths=1000), "synthetic")
    s = spec(end=600)
    apply(engine, "contract", {"normalized": asdict(s)}, 0)
    apply(engine, "reference", {"normalized": asdict(Reference("100", 0, 0))}, 0)
    assert "STALE_REFERENCE" in engine.forecast(s.market_ticker, 10).reasons
    s = replace(s, verified=False, rounding=None, unresolved=("ROUNDING_TIE_UNVERIFIED",))
    apply(engine, "contract", {"normalized": asdict(s)}, 10)
    assert engine.forecast(s.market_ticker, 10).raw_p_yes is None


def test_captured_metadata_and_options_units():
    series = fixture("kalshi_series")["series"]
    market = fixture("kalshi_markets")["markets"][0]
    s = discover_spec(series, market, 2e9)
    assert not s.verified and s.target is None and "MISSING_TARGET" in s.unresolved
    assert (
        s.observation_end_time
        != __import__("datetime").datetime.fromisoformat(market["expiration_time"]).timestamp()
    )
    book = fixture("deribit_book")["result"]
    instrument = next(
        i for i in fixture("deribit_instruments")["result"] if i["instrument_name"] == book["instrument_name"]
    )
    q = normalize_option(instrument, book, book["timestamp"] / 1000)
    assert q["mark_iv"] == pytest.approx(book["mark_iv"] / 100)
    assert q["mark_iv"] == pytest.approx(0.9591)
    assert q["quote_currency"] == instrument["quote_currency"]
    assert q["mark"] == book["mark_price"]  # No coin-to-USD silent conversion.


def quotes(now=0):
    return [
        dict(
            instrument=f"option-{k}",
            strike=k,
            forward=100,
            mark_iv=0.3,
            bid_iv=0.25,
            ask_iv=0.35,
            bid=0.01,
            ask=0.011,
            bid_size=1,
            ask_size=1,
            expiry=108000,
            source_time=now,
            received_time=now,
            state="open",
            index_price=100,
        )
        for k in (95, 100, 105)
    ]


def test_smile_roll_stale_sparse_crossed_and_moneyness():
    config = Config()
    q = quotes()
    result = options_sigma(q, 0, 100, 100, 300, config)
    assert result["sigma"] == 0.3 and result["horizon_extrapolation"]
    assert result["target_log_moneyness"] == 0
    assert options_sigma(q, 70, 100, 100, 300, config)["sigma"] is None
    assert options_sigma(q[:2], 0, 100, 100, 300, config)["sigma"] is None
    bad = [dict(v, ask=0.009) for v in q]
    assert options_sigma(bad, 0, 100, 100, 300, config)["rejected"][0]["reason"] == "CROSSED"
    q += [dict(v, instrument=v["instrument"] + "next", expiry=216000) for v in q]
    rolled = options_sigma(
        [dict(v, source_time=108001, received_time=108001) for v in q], 108001, 100, 100, 100, config
    )
    assert rolled["source_expiry"] == 216000


def test_reference_documented_schema_not_aggregate():
    frame = {
        "msg": {
            "index_id": "BRTI",
            "data": json.dumps({"type": "value", "id": "BRTI", "time": 1000, "value": "100.12345678"}),
            "avg_60s_data": {"value": "999"},
        }
    }
    ref = normalize_reference(frame, 2)
    assert ref.value == "100.12345678" and ref.source_time == 1 and ref.received_time == 2


def test_read_only_network_allowlists():
    calls = []

    def respond(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"result": [], "markets": []})

    async def run():
        k = KalshiReader(transport=httpx.MockTransport(respond))
        d = DeribitReader(transport=httpx.MockTransport(respond))
        await k.get("markets")
        await d.get("get_instruments", {"currency": "BTC"})
        for path in ("portfolio/orders", "markets/../portfolio", "https://example.com", "markets/x/orders"):
            with pytest.raises(ValueError):
                await k.get(path)
        with pytest.raises(ValueError):
            await d.get("buy", {})
        with pytest.raises(ValueError):
            k.headers("/trade-api/v2/portfolio/orders")
        await k.close()
        await d.close()

    asyncio.run(run())
    assert all(method == "GET" for method, url in calls)
    package = Path(__file__).parents[2] / "src/btc_probability"
    assert all(
        "from btc15" not in p.read_text() and "import btc15" not in p.read_text()
        for p in package.glob("*.py")
    )


def test_record_replay_resume_lineage_and_dashboard(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    c = Config(paths=100, provider="options")
    create_recording(path, c, markets=2, step=300)
    actual = list(replay(path))
    with read_connection(path) as db:
        stored = [json.loads(r[0]) for r in db.execute("SELECT data FROM forecasts ORDER BY tick_seq,market")]
    assert [json.loads(encode(f)) for f in actual] == stored
    recording = Recording(path, "synthetic", c)
    engine = Engine(c, "synthetic")
    recording.restore(engine)
    recording.close()
    assert [json.loads(encode(f)) for f in replay(path)] == stored
    assert len({f.market_ticker for f in actual}) == 2
    with read_connection(path) as db:
        for tick, raw in db.execute("SELECT tick_seq,data FROM forecasts"):
            assert max(json.loads(raw)["lineage"]) <= tick
    client = TestClient(create_app(path))
    assert client.get("/health").json()["read_only"]
    assert client.get("/api/markets").json()["markets"][0]["market"] == actual[-1].market_ticker
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.post("/api/live/control", json={}).status_code == 405
    assert client.get(
        "/api/forecasts", params={"market": actual[0].market_ticker, "provider": "options"}
    ).json()
    report = evaluate(path, c)
    assert report["unique_contracts"] == 2
    assert report["mode"] == "synthetic"
    assert report["models"]["options_settlement"]["matched"]["n"] > 0
    with pytest.raises(ValueError, match="mismatch"):
        Recording(path, "live", c)


def test_recording_integrity_and_writer_ownership(tmp_path):
    path = tmp_path / "record.sqlite"
    r = Recording(path, "synthetic", Config())
    with pytest.raises(RuntimeError):
        Recording(path, "synthetic", Config())
    r.append("failure", {"reason": "test"}, 0, 0)
    r.db.execute("UPDATE events SET data='{}'")
    r.db.commit()
    r.close()
    with pytest.raises(ValueError, match="INTEGRITY"):
        list(read_events(path))


def test_review_binds_current_rules_and_requires_target():
    series = fixture("kalshi_series")["series"]
    market = fixture("kalshi_active_market")["market"]
    s = discover_spec(series, market, 2e9)
    assert s.target is not None and not s.verified
    end = s.observation_end_time
    review = dict(
        market_ticker=market["ticker"],
        rules_hash=s.rules_hash,
        target_field="floor_strike",
        observation_end_time=end,
        sample_times=list(range(int(end) - 59, int(end) + 1)),
        comparison=">=",
        rounding="ROUND_HALF_UP",
        decimal_places=2,
        evidence_urls=["synthetic://test-review-not-real-verification"],
        reviewed_at="2026-09-17",
    )
    verified = discover_spec(series, market, 2e9, review)
    assert not verified.verified and verified.target == s.target
    assert "EXTERNAL_REVIEW_REQUIRES_VERSIONED_PROFILE" in verified.unresolved
    changed = discover_spec(series, dict(market, rules_secondary="changed"), 2e9, review)
    assert "SECONDARY_RULES_CHANGED" in changed.unresolved


def test_later_recovery_and_future_events_cannot_change_saved_forecasts(tmp_path):
    path = tmp_path / "causal.sqlite"
    config = Config(provider="fixed", paths=100)
    r = Recording(path, "synthetic", config)
    e = Engine(config, "synthetic")

    def put(kind, data, now):
        event = r.append(kind, data, now, now)
        fs = e.apply(event)
        r.forecasts(event["seq"], fs)
        return fs

    put("contract", {"normalized": asdict(spec())}, 0)
    put("reference", {"normalized": asdict(Reference("101", 1, 1))}, 1)
    put("reference", {"normalized": asdict(Reference("101", 3, 3))}, 3)
    before = put("forecast_tick", {}, 3)
    put("reference", {"normalized": asdict(Reference("101", 2, 4, recovered=True))}, 4)
    put("reference", {"normalized": asdict(Reference("101", 4, 4))}, 4)
    after = put("forecast_tick", {}, 4)
    r.close()
    assert all(f.raw_p_yes is None for f in before)
    assert next(f for f in after if f.provider == "fixed").raw_p_yes is not None
    replayed = list(replay(path))
    assert [encode(f) for f in replayed[:3]] == [encode(f) for f in before]
    with pytest.raises(ValueError, match="NONCAUSAL"):
        from btc_probability.models import simulate

        simulate(spec(), Reference("100", 10, 10), {}, 0, 0.2, paths=100)


def test_lost_reference_access_and_clock_skew_block_forecasts():
    e = Engine(Config(provider="fixed", paths=100), "synthetic")
    s = spec(end=600)
    apply(e, "contract", {"normalized": asdict(s)}, 0)
    apply(e, "reference", {"normalized": asdict(Reference("100", 0, 0))}, 0)
    apply(e, "failure", {"component": "reference", "reason": "ENTITLEMENT_LOST"}, 0)
    assert "ENTITLEMENT_LOST" in e.forecast(s.market_ticker, 0).reasons
    apply(e, "reference", {"normalized": asdict(Reference("100", 30, 1))}, 1)
    assert "REFERENCE_CLOCK_SKEW" in e.forecast(s.market_ticker, 1).reasons


def test_book_delta_valid_and_snapshot_repair():
    b = Book()
    snapshot = dict(type="orderbook_snapshot", seq=1, sid=4, msg={"yes_dollars_fp": [[".4", "2.50"]]})
    b.apply(snapshot, 0)
    b.apply(
        dict(
            type="orderbook_delta",
            seq=2,
            sid=4,
            msg={"side": "yes", "price_dollars": ".4", "delta_fp": "-.25"},
        ),
        1,
    )
    assert b.quotes(1, 3)["yes_bid_size"] == "2.25"
    b.invalidate()
    b.apply(
        dict(
            type="orderbook_delta", seq=3, sid=4, msg={"side": "yes", "price_dollars": ".4", "delta_fp": "1"}
        ),
        2,
    )
    assert not b.valid
    b.apply(snapshot, 2)
    assert b.valid


def test_interrupted_projection_rebuilt_and_no_duplicate_samples(tmp_path):
    path = tmp_path / "restart.sqlite"
    c = Config(paths=100, provider="fixed")
    r = Recording(path, "synthetic", c)
    r.append("contract", {"normalized": asdict(spec())}, 0, 0)
    r.append("reference", {"normalized": asdict(Reference("100", 0, 0))}, 0, 0)
    r.append("forecast_tick", {}, 0, 0)
    r.close()
    r = Recording(path, "synthetic", c)
    engine = Engine(c, "synthetic")
    r.restore(engine)
    r.close()
    with read_connection(path) as db:
        assert db.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0] == 3
    assert len(engine.history.rows) == 1


def test_reference_history_keeps_late_availability():
    from btc_probability.adapters import history_references

    refs = history_references({"data": {"payload": [{"id": "BRTI", "time": 1000, "value": "100.25"}]}}, 8)
    assert refs[0].source_time == 1 and refs[0].received_time == 8 and refs[0].recovered
    assert history_references({"unknown": ["100.25"]}, 8) == []
