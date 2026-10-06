"""Compare targeted reads to the pre-optimization selection, using synthetic journals only."""

import json
import random
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from btc15 import live_automation as live_module
from btc15.domain import timestamp
from btc15.live_automation import LiveAutomation
from btc15.manual_trading import UNRESOLVED, ManualTrading


def legacy_cycle_controls(self):
    """Five recent markets per asset, plus older unresolved/open obligations."""
    controls = self.controls()
    selected = {}
    counts = {}
    for control in sorted(controls.values(), key=lambda c: (c["close_time"], c["ticker"]), reverse=True):
        asset = control["asset"]
        if counts.get(asset, 0) < 5:
            selected[control["ticker"]] = control
            counts[asset] = counts.get(asset, 0) + 1
    held = {}
    now = live_module.time.time()
    active = [ticker for ticker, control in controls.items() if control["close_time"] > now]
    obligations = {r["id"]: r for r in self.manual.rows(tickers=active, origin="bot")}
    obligations.update({r["id"]: r for r in self.manual.rows(unresolved=True, origin="bot")})
    for row in obligations.values():
        if row.get("origin") != "bot":
            continue
        ticker = row["request"]["ticker"]
        if ticker not in controls:
            continue
        if row["state"] in UNRESOLVED:
            selected[ticker] = controls[ticker]
        quantity = Decimal((row.get("exchange_order") or {}).get("fill_count_fp", "0"))
        held[ticker] = held.get(ticker, Decimal(0)) + (
            quantity if row["request"]["action"] == "buy" else -quantity
        )
    now = live_module.time.time()
    for ticker, quantity in held.items():
        if quantity > 0 and controls[ticker]["close_time"] > now:
            selected[ticker] = controls[ticker]
    return sorted(selected.values(), key=lambda c: not bool(c.get("exit_reason")))


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(live_module, "time", SimpleNamespace(time=lambda: 1000))
    manual = ManualTrading(tmp_path / "orders.sqlite", settings=SimpleNamespace())
    return LiveAutomation(manual, {}, {})


def seed(worker, controls, orders=()):
    with worker.manual.db() as db:
        db.executemany(
            "INSERT OR REPLACE INTO live_controls VALUES (?,?)",
            [(c["ticker"], json.dumps(c)) for c in controls],
        )
        db.executemany(
            "INSERT OR REPLACE INTO manual_orders VALUES (?,?)",
            [(r["id"], json.dumps(r)) for r in orders],
        )


def order(ticker, number, *, state="complete", action="buy", quantity="1.25", origin="bot"):
    return dict(
        id=str(number),
        origin=origin,
        state=state,
        request=dict(ticker=ticker, action=action),
        exchange_order=dict(fill_count_fp=quantity),
    )


def test_empty_controls_ignore_orphan_orders(worker):
    seed(worker, [], [order("missing", 1, state="unknown")])
    assert worker.cycle_controls() == legacy_cycle_controls(worker) == []


def test_control_read_transaction_is_released_before_order_readers(worker, monkeypatch):
    seed(worker, [dict(ticker="BTC", asset="BTC", close_time=1100)])
    original_rows = worker.manual.rows
    writes = []

    def rows(**kwargs):
        # In rollback-journal mode a separate writer cannot commit while a control
        # reader holds a shared lock. This must remain possible between reads.
        with worker.manual.db() as db:
            db.execute("PRAGMA busy_timeout=50")
            db.execute("INSERT OR REPLACE INTO live_assets VALUES ('BTC', '{}')")
        writes.append(True)
        return original_rows(**kwargs)

    monkeypatch.setattr(worker.manual, "rows", rows)
    assert worker.cycle_controls()[0]["ticker"] == "BTC"
    assert len(writes) == 2


@pytest.mark.parametrize("random_seed", range(20))
def test_identical_selection_and_order_across_mixed_histories(worker, random_seed):
    rng = random.Random(random_seed)
    controls = [
        dict(
            ticker=f"{asset}-{i:03}",
            asset=asset,
            close_time=rng.choice([900, 999, 1000, 1001, 1100]),
            enabled=rng.choice([False, True]),
            paused=rng.choice([False, True]),
            exit_reason=rng.choice([None, "HARD_STOP", ""]),
            revision=i,
        )
        for asset in ("BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "DOGE", "LEGACY")
        for i in range(25)
    ]
    rng.shuffle(controls)
    orders = [
        order(
            rng.choice(controls)["ticker"],
            i,
            state=rng.choice(["complete", "rejected", *UNRESOLVED]),
            action=rng.choice(["buy", "sell"]),
            quantity=rng.choice(["0", "0.25", "2.50"]),
            origin=rng.choice(["bot", "manual"]),
        )
        for i in range(300)
    ]
    orders.append(order("NO-CONTROL", "missing", state="unknown"))
    seed(worker, controls, orders)
    assert worker.cycle_controls() == legacy_cycle_controls(worker)


def test_expired_unresolved_and_positive_active_holdings_survive_window(worker):
    controls = [dict(ticker=f"BTC-{i}", asset="BTC", close_time=1100 + i) for i in range(10)]
    controls += [dict(ticker="expired", asset="BTC", close_time=900, exit_reason="HARD_STOP")]
    orders = [
        order("expired", 1, state="unknown", quantity="0"),
        order("BTC-0", 2),
        order("BTC-1", 3),
        order("BTC-1", 4, action="sell"),
        order("BTC-2", 5, origin="manual"),
    ]
    seed(worker, controls, orders)
    result = worker.cycle_controls()
    assert result == legacy_cycle_controls(worker)
    assert result[0]["ticker"] == "expired"
    assert {c["ticker"] for c in result} == {"expired", "BTC-0", *[f"BTC-{i}" for i in range(5, 10)]}
    # No cross-cycle cache: completed obligations and changed exits are read immediately.
    orders[0]["state"] = "complete"
    seed(worker, [], orders[:1])
    assert "expired" not in {c["ticker"] for c in worker.cycle_controls()}


def test_expiry_between_initial_and_final_clock_reads(worker, monkeypatch):
    seed(
        worker,
        [dict(ticker=f"BTC-{i}", asset="BTC", close_time=1001 + i) for i in range(8)],
        [order("BTC-0", 1)],
    )

    def clock():
        ticks = iter([1000, 1001])
        monkeypatch.setattr(live_module, "time", SimpleNamespace(time=lambda: next(ticks)))

    clock()
    expected = legacy_cycle_controls(worker)
    clock()
    assert worker.cycle_controls() == expected
    assert "BTC-0" not in {c["ticker"] for c in expected}


def test_large_history_uses_indexes_and_decodes_only_selected_controls(worker, monkeypatch):
    assets = ["BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "DOGE"]
    controls = [
        dict(ticker=f"{assets[i % 7]}-{i:05}", asset=assets[i % 7], close_time=i - 10000) for i in range(9176)
    ]
    controls += [dict(ticker=a + "-active", asset=a, close_time=1100) for a in assets]
    seed(worker, controls)
    expected = legacy_cycle_controls(worker)
    decoded = []
    statements = []
    original_db = worker.manual.db

    @contextmanager
    def traced_db():
        with original_db() as db:
            db.set_trace_callback(statements.append)
            yield db

    def loads(body):
        result = json.loads(body)
        if "asset" in result:
            decoded.append(result["ticker"])
        return result

    monkeypatch.setattr(worker.manual, "db", traced_db)
    monkeypatch.setattr(live_module, "json", SimpleNamespace(loads=loads, dumps=json.dumps))
    assert worker.cycle_controls() == expected
    assert len(decoded) == 42  # 5 recent per asset + 7 active; independent of archived history.
    assert not any(s == "SELECT ticker,body FROM live_controls" for s in statements)
    with worker.manual.db() as db:
        recent_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT body FROM live_controls WHERE json_extract(body, '$.asset') = ? "
            "ORDER BY json_extract(body, '$.close_time') DESC, json_extract(body, '$.ticker') DESC LIMIT 5",
            ("BTC",),
        ).fetchall()
        active_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT ticker,body FROM live_controls "
            "WHERE json_extract(body, '$.close_time') > ?",
            (1000,),
        ).fetchall()
    assert any("live_controls_asset_recent" in r[3] for r in recent_plan)
    assert not any("TEMP B-TREE" in r[3] for r in recent_plan)
    assert any("live_controls_close_time" in r[3] for r in active_plan)


def test_sync_only_reads_snapshot_tickers_and_preserves_existing_controls(worker, monkeypatch):
    seed(worker, [dict(ticker=f"BTC-{i}", asset="BTC", close_time=900) for i in range(1000)])
    existing = dict(ticker="existing", asset="BTC", close_time=1100, enabled=False, paused=True, revision=4)
    seed(worker, [existing])
    worker.members = {"BTC": dict(run_id="current")}
    markets = [dict(ticker=t, close_time="1970-01-01T00:18:20+00:00") for t in ("existing", "new")]
    worker.stores = {
        "BTC": SimpleNamespace(read_market_display=lambda: dict(run_id="current", markets=markets))
    }
    monkeypatch.setattr(
        worker,
        "assets",
        lambda: {"BTC": dict(enabled=True, contracts=10, config_version="v1", stop_price=0.5)},
    )
    original = worker.controls
    scopes = []

    def scoped(tickers=None):
        assert tickers is not None, "sync must not scan historical controls"
        scopes.append(tickers)
        return original(tickers)

    monkeypatch.setattr(worker, "controls", scoped)
    worker.sync_markets()
    assert scopes == [["existing", "new"]]
    assert worker.control("existing") == existing
    assert worker.control("new")["contracts"] == 10
    assert len(original()) == 1002


def legacy_sync_markets(self):
    controls = self.controls()
    for asset, policy in self.assets().items():
        if not policy["enabled"]:
            continue
        member = self.members[asset]
        snapshot = self.stores[asset].read_market_display() or {}
        if snapshot.get("run_id") != member["run_id"]:
            continue
        for market in snapshot.get("markets", []):
            if market["ticker"] in controls or timestamp(market["close_time"]) <= live_module.time.time():
                continue
            self.write(
                dict(
                    ticker=market["ticker"],
                    asset=asset,
                    enabled=True,
                    contracts=policy["contracts"],
                    revision=1,
                    paused=False,
                    config_version=policy["config_version"],
                    stop_price=policy["stop_price"],
                    close_time=timestamp(market["close_time"]),
                )
            )
