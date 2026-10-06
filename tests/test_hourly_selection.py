# ruff: noqa: F811
import asyncio
import json
from dataclasses import replace

import pytest
from test_collection import fake_client, fake_socket
from test_hourly_contracts import hourly  # noqa: F401

from btc15 import runner
from btc15.config import Settings
from btc15.domain import Book, parse_market
from btc15.engine import Engine
from btc15.hourly import entry_obligations, publish_candidates, select_markets
from btc15.strategies.settlement_edge.model import Tick


def ladder(raw):
    rows = []
    for n, ask in enumerate((0.85, 0.90, 0.99, 0.50)):
        strike = raw["floor_strike"] + n
        rows.append(
            dict(
                raw,
                floor_strike=strike,
                ticker=raw["event_ticker"] + "-T" + str(strike),
                rules_primary=raw["rules_primary"].replace(
                    "above " + str(raw["floor_strike"]) + " at", "above " + str(strike) + " at"
                ),
                yes_bid_dollars=str(ask - 0.02),
                yes_ask_dollars=str(ask),
                no_bid_dollars=str(1 - ask),
                no_ask_dollars=str(1 - ask + 0.02),
                volume_fp=str(1000 if n == 2 else 10 - n),
            )
        )
    return rows


def inputs(hourly, store):
    raw, series, c = hourly
    e = Engine(store, c, "PAPER", execute=False, record_evaluations=False)
    return e, ladder(raw), series, parse_market(raw, series).close_time - 300


def test_two_closest_price_candidates_not_highest_volume(hourly, store):
    e, rows, series, now = inputs(hourly, store)
    selected, evidence = select_markets(e, rows, series, now)
    assert {r["ticker"] for r in selected} == {r["ticker"] for r in rows[:2]}
    assert all(x["basis"] == "QUOTE_ONLY_WARMUP" for x in evidence.values())


def test_model_probability_shortfall_changes_selection(hourly, store, monkeypatch):
    e, rows, series, now = inputs(hourly, store)
    e.ticks = [Tick(now, now, rows[-1]["floor_strike"] + 10)]
    monkeypatch.setattr(
        "btc15.strategies.settlement_edge.model.features", lambda *a: {"reference": e.ticks[-1].price}
    )
    monkeypatch.setattr(
        "btc15.strategies.settlement_edge.bleep.probability",
        lambda spec, *a: {"p_yes": 0.50 if spec.strike == rows[0]["floor_strike"] else 0.95},
    )
    selected, evidence = select_markets(e, rows, series, now)
    assert {r["ticker"] for r in selected} == {rows[1]["ticker"], rows[2]["ticker"]}
    assert all(x["basis"] == "MODEL_AND_REST_QUOTES" for x in evidence.values())
    assert evidence[rows[2]["ticker"]]["shortfall"] == pytest.approx(0.03)


def test_obligations_take_slots_and_ties_do_not_churn(hourly, store):
    e, rows, series, now = inputs(hourly, store)
    selected, _ = select_markets(e, rows, series, now, {rows[2]["ticker"]})
    assert {r["ticker"] for r in selected} == {rows[2]["ticker"], rows[0]["ticker"]}
    selected, _ = select_markets(e, rows, series, now, {rows[2]["ticker"], rows[3]["ticker"]})
    assert {r["ticker"] for r in selected} == {rows[2]["ticker"], rows[3]["ticker"]}
    e.hourly_watchlist = {rows[1]["ticker"]}
    selected, _ = select_markets(e, rows[:2], series, now, {rows[2]["ticker"]})
    assert selected[0]["ticker"] == rows[1]["ticker"]


def test_invalid_metadata_and_quotes_cannot_win_ranking(hourly, store):
    e, rows, series, now = inputs(hourly, store)
    rows[0]["rules_primary"] = "Unverified rules"
    rows[1]["yes_bid_dollars"] = "nan"
    rows[1]["no_bid_dollars"] = "nan"
    selected, _ = select_markets(e, rows, series, now)
    assert {r["ticker"] for r in selected} == {rows[2]["ticker"], rows[3]["ticker"]}


def test_rotation_drops_old_signal_and_requires_new_sequenced_book(hourly, store):
    e, rows, series, now = inputs(hourly, store)
    for raw in rows:
        e.markets[raw["ticker"]] = parse_market(raw, series)
        e.books[raw["ticker"]] = Book(valid=True, received=now, source_time=now)
    old, keep, new = [r["ticker"] for r in rows[:3]]
    e.hourly_watchlist = {old, keep}
    e.latest[old] = {"decision": "TRADE_CANDIDATE", "timestamp": now}
    published = {old: {"eligible": True, "timestamp": now, "since": now}}

    def ingest(kind, msg, seq=None):
        payload = dict(type=kind, msg=msg)
        if seq is not None:
            payload.update(sid=3, seq=seq)
        return e.ingest(
            dict(
                id=str(seq) + kind,
                received=now,
                monotonic_ns=int(now * 1e9),
                connection_id="test",
                collector_entries_blocked=True,
                analysis_suspended=True,
                payload=payload,
            )
        )

    ingest("connected", {})
    assert ingest(
        "metadata",
        dict(
            series=series,
            markets=rows[1:3],
            hourly_watchlist={keep: {}, new: {}},
            clock_skew=0,
            exchange_status={"trading_active": True},
        ),
    )
    assert e.hourly_watchlist == {keep, new}
    assert old not in e.latest and not e.books[old].valid
    assert {t for t, _ in e.processing_markets(now, "heartbeat", {})} == {keep, new}
    publish_candidates(e, published)
    assert old not in store.read_market_display("hourly_candidates:" + e.run_id)
    # Late old frames consume the shared sequence without reviving the old book.
    assert ingest(
        "orderbook_snapshot",
        dict(market_ticker=old, yes_dollars_fp=[[".8", "10"]], no_dollars_fp=[[".9", "10"]]),
        1,
    )
    assert not e.books[old].valid and e.sequences[3] == 1
    assert ingest(
        "orderbook_snapshot",
        dict(market_ticker=new, yes_dollars_fp=[[".8", "10"]], no_dollars_fp=[[".9", "10"]]),
        2,
    )
    assert e.books[new].valid


def test_journal_preserves_partial_and_uncertain_buys(tmp_path):
    import sqlite3

    path = tmp_path / "orders.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE manual_orders (body TEXT)")
        for suffix, state, fills in [
            ("partial", "complete", "0.5"),
            ("pending", "unknown", "0"),
            ("killed", "complete", "0"),
        ]:
            db.execute(
                "INSERT INTO manual_orders VALUES (?)",
                (
                    json.dumps(
                        dict(
                            request=dict(ticker="KXETHD-event-T" + suffix, action="buy"),
                            state=state,
                            exchange_order={"fill_count_fp": fills},
                        )
                    ),
                ),
            )
    assert entry_obligations(path, "KXETHD") == {"KXETHD-event-Tpartial", "KXETHD-event-Tpending"}


def test_collector_subscribes_only_two_hourly_books(hourly, store, tmp_path, monkeypatch):
    raw, series, c = hourly
    rows = ladder(raw)
    now = parse_market(raw, series).close_time - 300
    monkeypatch.setattr("time.time", lambda: now)
    fake_client(monkeypatch, raw, series)
    base = runner.KalshiClient

    class Client(base):
        async def discover(self):
            return series, rows

    monkeypatch.setattr(runner, "KalshiClient", Client)
    fake_socket(monkeypatch, [])
    sent = []
    connect = runner.websockets.connect

    def socket(*a, **kw):
        ws = connect(*a, **kw)

        async def send(message):
            sent.append(json.loads(message))

        ws.send = send
        return ws

    monkeypatch.setattr(runner.websockets, "connect", socket)
    asyncio.run(
        runner.collect(replace(Settings(), asset=c.asset, data_dir=str(tmp_path)), c, store, duration=0.25)
    )
    books = [s for s in sent if s.get("params", {}).get("channels") == ["orderbook_delta"]]
    assert books and set(books[0]["params"]["market_tickers"]) == {r["ticker"] for r in rows[:2]}
