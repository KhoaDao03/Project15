"""Observed-book and submission diagnostics regressions on real Engine/SQL paths.

Quotes and model outputs are synthetic. No network, credentials or real orders.
"""

import copy
import random
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_execution import decision, ready
from test_position_management import scenario as scenario
from test_position_management import sells

from btc15 import engine as module
from btc15.config import Settings
from btc15.dashboard import create_app
from btc15.domain import D
from btc15.storage import Store
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import Risk, fee_bound


def frame(s, market, when, quantity, *, bid=".50", kind="orderbook_snapshot", source=None, connection="book"):
    """Deliver a real wire-format book update without replacing the engine's book."""
    e = s.e
    if e.connection is None:
        e.connection = connection
    s.clock[0] = when
    e.ticks = [Tick(when, when, market.spec.strike + (200 if s.side == "yes" else -200))]
    price = D(bid)
    other = 1 - price - D(".02")
    if kind == "orderbook_snapshot":
        held = [[str(price if s.side == "yes" else 1 - price), str(quantity)]] if D(quantity) else []
        opposite = [[str(1 - other if s.side == "yes" else other), "100"]]
        msg = dict(
            yes_dollars_fp=held if s.side == "yes" else opposite,
            no_dollars_fp=opposite if s.side == "yes" else held,
        )
    else:
        msg = dict(
            side=s.side, price_dollars=str(price if s.side == "yes" else 1 - price), delta_fp=str(quantity)
        )
    msg.update(market_ticker=market.ticker, ts_ms=(when if source is None else source) * 1000)
    return e.ingest(
        dict(
            id=f"{connection}:{when}:{quantity}:{kind}",
            received=when,
            monotonic_ns=round(when * 1e9),
            connection_id=connection,
            payload=dict(type=kind, msg=msg),
        )
    )


def sold(store):
    return sum((D(r["body"]["quantity"]) for r in sells(store)), D(0))


def first_exit(s, store, market, now, bid=".50"):
    assert frame(s, market, now + 2, ".10", bid=bid)
    assert frame(s, market, now + 3, ".10", bid=bid)
    assert sold(store) == D(".10")
    assert D(s.e.executor.positions[market.ticker].quantity) == D(".30")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_observed_disappearance_replenishes_and_completes(scenario, store, market, now, mode, side):
    s = scenario(mode, side)
    first_exit(s, store, market, now)
    assert frame(s, market, now + 4, "0")
    assert sold(store) == D(".10")
    assert frame(s, market, now + 5, ".30")
    assert sold(store) == D(".40")
    assert not s.e.executor.positions and not s.e.executor.risk.reserved
    assert store.state(s.e.run_id, market.ticker) == "CLOSED"
    results = store.list(kind="trade_result", run_id=s.e.run_id)
    assert len(results) == 1 and results[0]["body"]["net_pnl"] < 0
    fills = store.list(kind="fill", run_id=s.e.run_id)
    expected = sum(
        r["body"]["quantity"] * r["body"]["price"] * (1 if r["body"]["action"] == "sell" else -1)
        - r["body"]["fee"]
        for r in fills
    )
    assert results[0]["body"]["net_pnl"] == pytest.approx(expected)
    assert not store.load_checkpoint(s.e.run_id)["positions"]
    # Repeated final book events do not create another close or payout.
    assert frame(s, market, now + 6, ".30")
    assert len(store.list(kind="trade_result", run_id=s.e.run_id)) == 1


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_delta_removal_reappearance_and_unchanged_snapshot(scenario, store, market, now, mode, side):
    s = scenario(mode, side)
    first_exit(s, store, market, now)
    for i in (4, 5):
        assert frame(s, market, now + i, ".10")
    assert sold(store) == D(".10")
    assert frame(s, market, now + 6, "-.10", kind="orderbook_delta")
    assert frame(s, market, now + 7, ".30", kind="orderbook_delta")
    assert sold(store) == D(".40")
    assert not s.e.executor.positions


@pytest.mark.parametrize("side", ["yes", "no"])
def test_partial_reduction_credits_only_observed_replenishment(scenario, store, market, now, side):
    s = scenario(side=side)
    first_exit(s, store, market, now)
    assert frame(s, market, now + 4, ".06")
    assert sold(store) == D(".10")
    assert frame(s, market, now + 5, ".10")
    assert sold(store) == D(".14")
    assert frame(s, market, now + 6, ".10")
    assert sold(store) == D(".14")
    assert frame(s, market, now + 7, ".15")
    assert sold(store) == D(".19")
    assert D(s.e.executor.positions[market.ticker].quantity) == D(".21")


def test_observe_empty_depth_without_an_exit_signal(scenario, store, market, now):
    s = scenario()
    s.p["conservative_yes"] = 0.70
    first_exit(s, store, market, now, bid=".80")
    s.p["conservative_yes"] = 0.99  # No price stop, TP or invalidation now.
    assert frame(s, market, now + 4, "0", bid=".80")
    assert frame(s, market, now + 5, ".30", bid=".80")
    assert sold(store) == D(".10")
    s.p["conservative_yes"] = 0.70
    assert frame(s, market, now + 6, ".30", bid=".80")
    assert sold(store) == D(".10")  # New trigger starts a new latency period.
    assert frame(s, market, now + 6.25, ".30", bid=".80")
    assert sold(store) == D(".40")


@pytest.mark.parametrize("problem", ["stale", "future", "disconnect", "invalid", "quarantine"])
def test_unsafe_or_discontinuous_observations_do_not_reset_depth(scenario, store, market, now, problem):
    s = scenario()
    first_exit(s, store, market, now)
    e = s.e
    key = (market.ticker, D(".50"))
    if problem == "quarantine":
        e.executor.quarantine(market, now + 3.5, "test")
        assert frame(s, market, now + 4, "0")
    elif problem == "invalid":
        assert not frame(s, market, now + 4, "-.11", kind="orderbook_delta")
    elif problem == "disconnect":
        assert frame(s, market, now + 4, "0", connection="new-connection")
    else:
        source = now + 4 - e.config.book_max_age - 1 if problem == "stale" else now + 20
        assert frame(s, market, now + 4, "0", source=source)
    assert e.executor.exit_consumed[key] == D(".10")
    assert sold(store) == D(".10")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_replenishment_checkpoint_rollback_and_resume(scenario, store, market, now, monkeypatch, mode):
    s = scenario(mode)
    first_exit(s, store, market, now)
    before = copy.deepcopy(s.e.executor.snapshot())
    with monkeypatch.context() as patch:
        patch.setattr(store, "checkpoint", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            frame(s, market, now + 4, "0")
    assert s.e.executor.snapshot() == before
    assert sold(store) == D(".10")
    assert frame(s, market, now + 4, "0")
    assert s.e.executor.exit_consumed[(market.ticker, D(".50"))] == 0
    resumed = module.Engine(
        store,
        s.e.config,
        mode,
        run_id=s.e.run_id,
        resume=True,
        clock=lambda: s.clock[0],
        record_evaluations=False,
    )
    s.e = resumed
    resumed.healthy = resumed.clock_ok = resumed.exchange_open = True
    resumed.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
    resumed.series_fee_changes = []
    resumed.fee_changes = {market.event_ticker: []}
    assert frame(s, market, now + 5, ".30")
    assert sold(store) == D(".40")
    assert not resumed.executor.positions


def test_restart_does_not_reset_pre_gap_consumption(scenario, store, market, now):
    s = scenario()
    first_exit(s, store, market, now)
    resumed = module.Engine(store, s.e.config, run_id=s.e.run_id, resume=True, clock=lambda: s.clock[0])
    s.e = resumed
    resumed.healthy = resumed.clock_ok = resumed.exchange_open = True
    resumed.series_fees = dict(fee_type="quadratic", fee_multiplier=1)
    resumed.series_fee_changes = []
    resumed.fee_changes = {market.event_ticker: []}
    # A missing interval is not proof that old liquidity was traded/replenished.
    assert frame(s, market, now + 4, "0")
    assert resumed.executor.exit_consumed[(market.ticker, D(".50"))] == D(".10")
    assert frame(s, market, now + 5, ".10")
    assert sold(store) == D(".10")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_disabled_dispatch_has_one_specific_rejection(scenario, store, market, now, mode):
    s = scenario(mode, buy=False)
    s.e.execute = False
    s.e.process(now, "observed-candidate", "orderbook_snapshot", dict(market_ticker=market.ticker))
    rows = store.list(kind="execution_rejection", run_id=s.e.run_id)
    assert len(rows) == 1
    assert rows[0]["body"]["reason"] == "EXECUTION_DISABLED"
    assert rows[0]["body"]["details"]["snapshot_id"] == "observed-candidate"
    assert not s.e.executor.orders and not store.list(kind="fill")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_post_computation_rejection_is_specific_and_not_a_feed_exception(scenario, store, market, now, mode):
    s = scenario(mode, buy=False)
    s.delay[0] = 10
    s.e.process(now, "slow-model", "orderbook_snapshot", dict(market_ticker=market.ticker))
    rows = store.list(kind="execution_rejection", run_id=s.e.run_id)
    assert len(rows) == 1 and rows[0]["body"]["reason"] == "PROCESSING_LAG"
    checks = {r["code"]: r for r in rows[0]["body"]["details"]["rechecks"]}
    assert checks["PROCESSING_LAG"]["actual"] == 10
    assert {"BOOK_RECEIVE_AGE", "REFERENCE_SOURCE_AGE"} <= checks.keys()
    assert not s.e.executor.orders and not store.list(kind="health")


@pytest.mark.parametrize(
    "case,code",
    [
        ("disabled", "STRATEGY_DISABLED"),
        ("signal", "SIGNAL_NOT_CANDIDATE"),
        ("gate", "EXECUTION_GATE_BLOCKED"),
        ("quarantine", "METADATA_QUARANTINED"),
        ("inactive", "MARKET_NOT_TRADABLE"),
        ("window", "ENTRY_WINDOW"),
        ("side", "INVALID_SIDE"),
        ("book", "BOOK_INVALID"),
        ("age", "BOOK_RECEIVE_AGE"),
        ("ask", "NO_ASK"),
        ("bid", "NO_BID"),
        ("probability", "INVALID_PROBABILITY"),
        ("edge", "NET_EDGE_RECHECK"),
        ("tick", "UNSUPPORTED_ENTRY_TICK"),
        ("halt", "KILL_SWITCH"),
        ("daily_loss", "DAILY_LOSS_LIMIT"),
        ("attempts", "DAILY_ATTEMPT_LIMIT"),
        ("open_budget", "OPEN_EXPOSURE_LIMIT"),
        ("daily_budget", "DAILY_EXPOSURE_LIMIT"),
        ("claim", "ENTRY_ALREADY_CLAIMED"),
    ],
)
def test_submit_refusal_reports_cause_without_buying(store, config, market, book, now, case, code):
    c = replace(config, enabled=False) if case == "disabled" else config
    e = ready(store, market, now, c)
    d = {**decision(), "expected_fill_price": 0.90}
    freshness = case != "gate"
    when = now
    if case == "signal":
        d["decision"] = "NO_TRADE"
    elif case == "quarantine":
        e.quarantine(market, now, "test")
    elif case == "inactive":
        market = replace(market, status="inactive")
    elif case == "window":
        when = market.close_time - 120
    elif case == "side":
        d["side"] = "up"
    elif case == "book":
        book.valid = False
    elif case == "age":
        book.received = now - 10
    elif case == "ask":
        book.no.clear()
    elif case == "bid":
        book.yes.clear()
    elif case == "probability":
        d["conservative_probability"] = float("nan")
    elif case == "edge":
        book.yes = {D(".94"): D(100)}
        book.no = {D(".04"): D(100)}
    elif case == "tick":
        market = replace(market, price_ranges=[dict(start=".95", end=".99", step=".01")])
    elif case == "halt":
        e.risk.halted = True
    elif case == "daily_loss":
        e.risk.day(now)["pnl"] = -c.max_daily_loss
    elif case == "attempts":
        e.risk.day(now)["trades"] = c.max_daily_trades
    elif case == "open_budget":
        e.risk.reserved["other"] = c.max_open_exposure
    elif case == "daily_budget":
        e.risk.day(now)["exposure"] = c.max_daily_exposure
    elif case == "claim":
        assert store.claim(e.run_id, "entry:" + market.ticker)
    before = copy.deepcopy(e.risk.reserved)
    assert e.submit(market, book, d, "rejected-op", when, freshness) is None
    rows = store.list(kind="execution_rejection", run_id=e.run_id)
    assert len(rows) == 1 and rows[0]["body"]["reason"] == code
    assert rows[0]["body"]["message"] and rows[0]["opportunity_id"] == "rejected-op"
    assert rows[0]["body"]["config_version"] == c.version
    assert not e.orders and not e.positions and e.risk.reserved == before
    assert not store.list(kind="fill")
    if case == "edge":
        details = rows[0]["body"]["details"]
        assert details["ask"] == 0.96 and details["evaluated_ask"] == 0.90
        assert details["actual"] < details["required"]


@pytest.mark.parametrize("filled", [False, True])
def test_existing_attempt_or_inventory_is_identified(store, config, market, book, now, filled):
    e = ready(store, market, now, config)
    o = e.submit(market, book, decision(), "first", now, True)
    assert o and not store.list(kind="execution_rejection")
    if filled:
        e.fill(o, 0.10, o.limit, now + 0.5, True)
    e.cancel(market.ticker, now + 1, "test")
    assert e.submit(market, book, decision(), "second", now + 1, True) is None
    expected = "EXISTING_POSITION" if filled else "ORDER_ALREADY_ATTEMPTED"
    assert store.list(kind="execution_rejection")[0]["body"]["reason"] == expected


def test_risk_evaluation_names_daily_attempt_budget(scenario, store, market, now):
    s = scenario(buy=False)
    s.e.executor.risk.day(now)["trades"] = s.e.config.max_daily_trades
    s.e.process(now, "risk", "orderbook_snapshot", dict(market_ticker=market.ticker))
    r = next(r for r in s.e.latest[market.ticker]["reasons"] if r["code"] == "RISK_LIMIT")
    assert r["details"]["reasons"][0]["code"] == "DAILY_ATTEMPT_LIMIT"
    assert r["details"]["reasons"][0]["actual"] == s.e.config.max_daily_trades
    assert "unfilled" in r["message"]
    assert not store.list(kind="execution_rejection")  # It failed a pre-submission risk evaluation.


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_rejections_persist_and_are_queryable_without_entry_evidence(
    scenario, store, market, now, tmp_path, mode
):
    s = scenario(mode, buy=False)
    s.e.execute = False
    s.e.process(now, "disabled", "orderbook_snapshot", dict(market_ticker=market.ticker))
    url = store.engine.url.render_as_string(hide_password=False)
    store.engine.dispose()
    reopened = Store(url)
    try:
        with TestClient(
            create_app(reopened, settings=Settings(data_dir=str(tmp_path)), config=s.e.config)
        ) as client:
            response = client.get(
                "/api/records",
                params=dict(kind="execution_rejection", mode=mode, run_id=s.e.run_id, scope="settlement"),
            )
            response.raise_for_status()
            rows = response.json()
            assert rows["total"] == 1
            assert rows["rows"][0]["body"]["reason"] == "EXECUTION_DISABLED"
            other = "BACKTEST" if mode == "PAPER" else "PAPER"
            assert (
                client.get("/api/records", params=dict(kind="execution_rejection", mode=other)).json()[
                    "total"
                ]
                == 0
            )
            assert (
                client.get("/api/analytics", params=dict(mode=mode, run_id=s.e.run_id)).json()["trades"] == 0
            )
        assert not reopened.list(kind="opportunity", run_id=s.e.run_id)
    finally:
        reopened.engine.dispose()


def test_rejection_write_error_is_not_hidden(store, config, market, book, now, monkeypatch):
    e = ready(store, market, now, config)
    before = copy.deepcopy(e.snapshot())
    original = store.add
    with monkeypatch.context() as patch:

        def fail(kind, *args, **kwargs):
            if kind == "execution_rejection":
                raise OSError("storage unavailable")
            return original(kind, *args, **kwargs)

        patch.setattr(store, "add", fail)
        with pytest.raises(OSError):
            e.submit(market, book, decision(), "op", now, False)
    assert e.snapshot() == before and not store.list(kind="execution_rejection")
    assert e.submit(market, book, decision(), "op", now, False) is None
    assert len(store.list(kind="execution_rejection")) == 1


@pytest.mark.parametrize("sizing_mode", ["fixed_contracts", "fixed_dollars", "bankroll_percentage"])
def test_explained_sizing_matches_exact_formula(config, sizing_mode):
    c = replace(config, sizing_mode=sizing_mode)
    r = Risk(c)
    rng = random.Random(15)
    for _ in range(250):
        price = rng.uniform(0.01, 0.99)
        r.realized = rng.uniform(-c.bankroll, c.bankroll)
        r.reserved = {"other": rng.uniform(0, c.max_open_exposure * 1.1)}
        d = r.day(100)
        d.update(
            pnl=rng.uniform(-c.max_daily_loss * 1.1, 10),
            exposure=rng.uniform(0, c.max_daily_exposure * 1.1),
            trades=rng.randrange(c.max_daily_trades + 2),
        )
        r.halted = rng.random() < 0.1
        if r.halted or d["pnl"] <= -c.max_daily_loss or d["trades"] >= c.max_daily_trades:
            expected = 0
        else:
            # Rational oracle preserves the policy without the old float-floor bug.
            def exact(value):
                return Fraction(str(value))

            cost = exact(price) + exact(fee_bound(price, c)) + exact(c.slippage)
            bankroll = max(0, exact(c.bankroll) + exact(r.realized))
            reserved = sum((exact(value) for value in r.reserved.values()), Fraction(0))
            allocation = bankroll * exact(c.bankroll_fraction)
            target = {
                "fixed_contracts": c.fixed_contracts * cost,
                "fixed_dollars": exact(c.fixed_dollars),
                "bankroll_percentage": allocation,
            }[sizing_mode]
            budget = min(
                target,
                exact(c.max_trade_dollars),
                allocation,
                exact(c.max_open_exposure) - reserved,
                exact(c.max_daily_exposure) - exact(d["exposure"]),
                bankroll - reserved,
            )
            expected = max(0, min(c.max_contracts, budget // cost))
        details = r.size_details(price, 100)
        assert r.size(price, 100) == details["quantity"] == expected
        assert bool(details["reasons"]) == (expected == 0)


def test_submission_history_selector_and_safe_rendering():
    root = Path(__file__).parents[1] / "src/btc15/static"
    assert (
        '<option value="execution_rejection">Submission rejections</option>'
        in (root / "index.html").read_text()
    )
    js = (root / "app.js").read_text()
    assert "['order','fill','execution_rejection'].includes(kind)" in js
    assert "text('p',b.message" in js
    assert "r.message||labels[r.code]" in js
