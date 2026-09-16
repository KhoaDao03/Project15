import copy
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from btc15.config import Settings
from btc15.dashboard import create_app
from btc15.engine import Engine
from btc15.recovery import contract_hash, recover_settlement
from btc15.storage import Store


def opened(store, config, market, book, now, series, mode="PAPER", side="yes", fill=True):
    if side == "no":
        book = copy.deepcopy(book)
        book.yes, book.no = book.no, book.yes
    e = Engine(store, config, mode, run_id="recovery-run", record_evaluations=False)
    e.connection = "test"
    e.markets[market.ticker] = market
    e.books[market.ticker] = book
    store.add(
        "market",
        dict(raw=market.raw, series=series, spec=asdict(market.spec)),
        e.run_id,
        mode,
        now - 1,
        market.ticker,
    )
    for state in (
        "DISCOVER_MARKET",
        "VALIDATE_MARKET",
        "WARMUP",
        "ENTRY_WINDOW",
        "EVALUATING",
        "TRADE_CANDIDATE",
    ):
        e.state(market.ticker, state, now)
    op = "entry-op"
    store.add(
        "opportunity",
        dict(
            timestamp=now,
            ticker=market.ticker,
            mode=mode,
            decision="TRADE_CANDIDATE",
            reasons=[],
            model=e.executor.model_identity,
            side=side,
            net_ev=0.05,
            expected_fill_price=book.ask(side),
            seconds_remaining=market.close_time - now,
            settlement_spec=asdict(market.spec),
            config=asdict(config),
            probability={},
            features={},
            quality={},
            book=book.summary(),
            versions=e.versions,
        ),
        e.run_id,
        mode,
        now,
        market.ticker,
        op,
        record_id=op,
    )
    order = e.executor.submit(
        market,
        book,
        dict(decision="TRADE_CANDIDATE", side=side, conservative_probability=0.99),
        op,
        now,
        True,
    )
    assert order
    if fill:
        e.executor.fill(order, 0.40, order.limit, now + 0.5, True)
    return e


def metadata(e, raw, series, now):
    payload = dict(
        type="metadata",
        msg=dict(
            series=series,
            markets=[raw],
            fee_changes={},
            series_fee_changes=[],
            exchange_status={"trading_active": True},
            clock_skew=0,
        ),
    )
    assert e.ingest(
        dict(
            id=f"metadata-{now}",
            received=now,
            monotonic_ns=int(now * 1e9),
            connection_id="test",
            payload=payload,
        )
    )


def proof(raw, series, result="yes"):
    return dict(
        source="kalshi_rest",
        market={**copy.deepcopy(raw), "status": "finalized", "result": result},
        series=copy.deepcopy(series),
    )


def assert_closed(e, store, market, side, result):
    assert not e.executor.positions and not e.executor.risk.reserved
    assert store.state(e.run_id, market.ticker) == "CLOSED"
    fills = store.list(kind="fill", run_id=e.run_id)
    buys = [r["body"] for r in fills if r["body"]["action"] == "buy"]
    records = store.list(kind="trade_result", run_id=e.run_id)
    assert len(records) == 1
    debit = sum(r["quantity"] * r["price"] + r["fee"] for r in buys)
    assert records[0]["body"]["net_pnl"] == pytest.approx((0.40 if side == result else 0) - debit)
    assert not store.load_checkpoint(e.run_id)["positions"]
    assert not store.load_checkpoint(e.run_id)["quarantines"]


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_invalid_metadata_retains_position_and_settles(store, config, market, book, now, series, mode, side):
    e = opened(store, config, market, book, now, series, mode, side)
    reserved = copy.deepcopy(e.executor.risk.reserved)
    metadata(e, {**market.raw, "rules_primary": "unrecognized"}, series, now + 1)
    assert market.ticker in e.markets
    assert e.executor.positions and e.executor.risk.reserved == reserved
    assert store.state(e.run_id, market.ticker) == "HALTED"
    assert not e.executor.orders[market.ticker].active
    assert e.settle(market.ticker, "yes", market.close_time + 1) == "BLOCKED"
    assert not store.list(kind="settlement")
    assert (
        e.settle(market.ticker, "yes", market.close_time + 2, evidence=proof(market.raw, series)) == "SETTLED"
    )
    assert_closed(e, store, market, side, "yes")


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_changed_metadata_recovers_with_final_original_terms(
    store, config, market, book, now, series, mode, side
):
    e = opened(store, config, market, book, now, series, mode, side)
    metadata(e, {**market.raw, "floor_strike": market.spec.strike + 100}, series, now + 1)
    assert e.markets[market.ticker].spec == market.spec
    assert (
        e.settle(market.ticker, "no", market.close_time + 1, evidence=proof(market.raw, series, "no"))
        == "SETTLED"
    )
    assert_closed(e, store, market, side, "no")
    audit = store.list(kind="settlement_recovery")[0]
    assert audit["body"]["method"] == "matching_final_metadata"
    transitions = store.list(kind="transition")
    assert any(r["body"].get("settlement_recovery_id") == audit["id"] for r in transitions)


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_changed_final_terms_require_hash_bound_review(store, config, market, book, now, series, mode):
    e = opened(store, config, market, book, now, series, mode)
    changed = {**market.raw, "floor_strike": market.spec.strike + 100}
    metadata(e, changed, series, now + 1)
    assert e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(changed, series)) == "BLOCKED"
    assert not store.list(kind="trade_result") and e.executor.risk.reserved
    before = len(store.list(limit=None))
    preview = recover_settlement(store, e.run_id, market.ticker)
    assert len(store.list(limit=None)) == before
    row = preview["evidence"][-1]
    for opts in (
        {},
        {"confirm": "bad", "reason": "reviewed"},
        {"confirm": row["evidence_hash"], "reason": ""},
    ):
        with pytest.raises(ValueError):
            recover_settlement(store, e.run_id, market.ticker, evidence_id=row["id"], **opts)
    applied = recover_settlement(
        store,
        e.run_id,
        market.ticker,
        evidence_id=row["id"],
        confirm=row["evidence_hash"],
        reason="Confirmed finalized venue terms",
    )
    assert applied["result"] == "SETTLED" and applied["state"] == "CLOSED"
    assert store.list(kind="settlement_recovery")[0]["body"]["method"] == "operator_review"
    assert not store.load_checkpoint(e.run_id)["positions"]
    assert store.writer_owner() is None


@pytest.mark.parametrize(
    "damage", ["strike_missing", "primary", "identity", "exchange", "not_final", "scalar", "source"]
)
def test_invalid_final_evidence_cannot_be_overridden(store, config, market, book, now, series, damage):
    e = opened(store, config, market, book, now, series)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 1)
    assert store.state(e.run_id, market.ticker) == "HALTED"
    p = proof(market.raw, series)
    if damage == "strike_missing":
        p["market"]["floor_strike"] = None
    elif damage == "primary":
        p["market"]["rules_primary"] = "Unsupported new methodology"
    elif damage == "identity":
        p["market"]["event_ticker"] = "KXBTC15M-OTHER"
    elif damage == "exchange":
        p["market"]["exchange_index"] += 1
    elif damage == "not_final":
        p["market"]["status"] = "determined"
    elif damage == "scalar":
        p["market"]["result"] = "scalar"
    else:
        p["source"] = "guessed"
    assert e.settle(market.ticker, "yes", market.close_time + 1, evidence=p) == "BLOCKED"
    row = recover_settlement(store, e.run_id, market.ticker)["evidence"][-1]
    result = recover_settlement(
        store,
        e.run_id,
        market.ticker,
        evidence_id=row["id"],
        confirm=row["evidence_hash"],
        reason="Cannot bypass validation",
    )
    assert result["result"] == "BLOCKED"
    assert not store.list(kind="settlement") and not store.list(kind="trade_result")
    assert store.load_checkpoint(e.run_id)["risk"]["reserved"]


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_recovery_is_atomic_idempotent_and_survives_restart(
    store, config, market, book, now, series, monkeypatch, mode, tmp_path
):
    e = opened(store, config, market, book, now, series, mode)
    metadata(e, {**market.raw, "floor_strike": market.spec.strike + 10}, series, now + 1)
    e.executor.halt(now + 2)
    original = copy.deepcopy(store.load_checkpoint(e.run_id))
    e = Engine(store, config, mode, run_id=e.run_id, resume=True, record_evaluations=False)
    assert e.markets[market.ticker].spec == market.spec
    assert e.executor.quarantines and e.executor.risk.halted
    before = copy.deepcopy(e.executor.snapshot())
    p = proof(market.raw, series)
    with monkeypatch.context() as m:

        def fail(*args):
            raise OSError("checkpoint unavailable")

        m.setattr(store, "checkpoint", fail)
        with pytest.raises(OSError):
            e.settle(market.ticker, "yes", market.close_time + 1, evidence=p)
    assert e.executor.snapshot() == before
    assert not store.list(kind="settlement") and not store.list(kind="settlement_recovery")
    assert store.load_checkpoint(e.run_id)["risk"]["reserved"] == original["risk"]["reserved"]
    assert e.settle(market.ticker, "yes", market.close_time + 1, evidence=p) == "SETTLED"
    assert e.settle(market.ticker, "yes", market.close_time + 2, evidence=p) == "ALREADY_SETTLED"
    assert e.settle(market.ticker, "no", market.close_time + 3) == "BLOCKED"
    assert len(store.list(kind="trade_result")) == len(store.list(kind="settlement")) == 1
    assert e.executor.risk.halted
    assert_closed(e, store, market, "yes", "yes")
    url = store.engine.url.render_as_string(hide_password=False)
    store.engine.dispose()
    reopened = Store(url)
    try:
        with TestClient(
            create_app(reopened, settings=Settings(data_dir=str(tmp_path)), config=config)
        ) as client:
            params = {"mode": mode, "run_id": e.run_id}
            assert client.get("/api/trades", params=params).json()["total"] == 1
            result = client.get("/api/analytics", params=params).json()
            assert result["trades"] == 1
            assert result["net_pnl"] == pytest.approx(
                reopened.list(kind="trade_result")[0]["body"]["net_pnl"]
            )
            assert client.get("/api/replay/entry-op").status_code == 200
    finally:
        reopened.engine.dispose()


@pytest.mark.parametrize("kind", ["invalid", "changed"])
def test_legacy_checkpoint_uses_entry_history_not_later_metadata(
    store, config, market, book, now, series, kind
):
    e = opened(store, config, market, book, now, series)
    changed = {**market.raw, "floor_strike": None if kind == "invalid" else market.spec.strike + 100}
    metadata(e, changed, series, now + 1)
    checkpoint = store.load_checkpoint(e.run_id)
    checkpoint.pop("contracts")
    checkpoint.pop("quarantines")
    store.checkpoint(e.run_id, checkpoint)
    e = Engine(store, config, run_id=e.run_id, resume=True)
    assert e.markets[market.ticker].spec == market.spec
    assert e.executor.quarantines
    assert contract_hash(e.markets[market.ticker]) == contract_hash(market)
    assert (
        e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series)) == "SETTLED"
    )


def test_wrong_or_unreviewed_state_cannot_reopen(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    e.executor.cancel(market.ticker, now + 1, "test")
    store.transition(e.run_id, "PAPER", market.ticker, "HALTED", now + 1)
    assert (
        e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series)) == "BLOCKED"
    )
    for target in ("POSITION_OPEN", "SETTLEMENT_PENDING"):
        with pytest.raises(ValueError):
            store.transition(e.run_id, "PAPER", market.ticker, target, market.close_time + 1)
    with pytest.raises(ValueError):
        store.transition(
            e.run_id,
            "PAPER",
            market.ticker,
            "SETTLEMENT_PENDING",
            market.close_time + 1,
            settlement_recovery_id="not-an-audit-record",
        )


def test_active_writer_prevents_manual_recovery(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    p = proof({**market.raw, "floor_strike": market.spec.strike + 10}, series)
    e.settle(market.ticker, "yes", market.close_time + 1, evidence=p)
    row = recover_settlement(store, e.run_id, market.ticker)["evidence"][-1]
    store.acquire("collector", "running")
    with pytest.raises(RuntimeError, match="writer owns"):
        recover_settlement(
            store,
            e.run_id,
            market.ticker,
            evidence_id=row["id"],
            confirm=row["evidence_hash"],
            reason="reviewed",
        )
    assert store.writer_owner() == "running"
    assert not store.list(kind="trade_result")
    store.release("collector", "running")


def test_quarantine_without_fill_closes_without_profit(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series, fill=False)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 1)
    assert not e.executor.risk.reserved
    assert (
        e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series)) == "SETTLED"
    )
    assert not store.list(kind="trade_result") and not store.list(kind="fill")
    assert store.state(e.run_id, market.ticker) == "CLOSED"


def test_settlement_before_close_does_not_pay(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 1)
    with pytest.raises(ValueError):
        e.settle(market.ticker, "yes", now + 2, evidence=proof(market.raw, series))
    assert not store.list(kind="settlement") and e.executor.positions


def test_service_health_reports_unresolved_metadata(store):
    from btc15.operation import health

    body = dict(
        connected=True,
        clock_ok=True,
        paper_execution=True,
        exchange_open=True,
        reference_age=0,
        processing_lag=0,
        halted=False,
        settlement_recovery={"ticker": {"reason": "changed"}},
    )
    store.add("status", body, "run", "PAPER", 100)
    assert health(store, "run", now=100)["reasons"] == ["SETTLEMENT_RECOVERY_REQUIRED"]


def test_collector_retries_invalid_final_metadata_until_accounting_commits(
    store, config, market, book, now, series, tmp_path, monkeypatch
):
    import asyncio

    from test_collection import fake_client, fake_socket

    from btc15 import runner
    from btc15.storage import read_events

    e = opened(store, config, market, book, now, series)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 1)
    fake_client(monkeypatch, market.raw, series)
    fake_socket(monkeypatch, [])
    base = runner.KalshiClient
    calls = []

    class Client(base):
        async def get(self, path, *args):
            if path.startswith("markets/"):
                calls.append(path)
                raw = proof(market.raw, series)["market"]
                if len(calls) == 1:
                    raw["rules_primary"] = "Temporarily malformed response"
                return {"market": raw}
            return await super().get(path, *args)

    monkeypatch.setattr(runner, "KalshiClient", Client)
    wait_for = asyncio.wait_for

    async def refresh_quickly(future, timeout):
        return await wait_for(future, 0.02 if timeout == 15 else timeout)

    monkeypatch.setattr(runner.asyncio, "wait_for", refresh_quickly)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(
            runner.collect(
                Settings(data_dir=str(tmp_path)),
                config,
                store,
                paper=True,
                resume=e.run_id,
                stop_event=stop,
            )
        )
        try:
            for _ in range(300):
                if task.done():
                    await task
                    raise AssertionError("Collector stopped before recovery")
                if store.list(kind="trade_result", run_id=e.run_id):
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("Finalized metadata was not retried")
        finally:
            stop.set()
            await wait_for(task, 10)

    asyncio.run(run())
    assert len(calls) >= 2
    assert len(store.list(kind="trade_result", run_id=e.run_id)) == 1
    assert not store.load_checkpoint(e.run_id)["positions"]
    rows = list(read_events(next((tmp_path / "raw").glob("*.jsonl.gz"))))
    proofs = [r["payload"]["msg"]["evidence"] for r in rows if r["payload"]["type"] == "settlement"]
    assert len(proofs) >= 2 and all(p["source"] == "kalshi_rest" for p in proofs)
    assert store.writer_owner() is None


@pytest.mark.parametrize(
    "field,value", [("market", None), ("series", None), ("custom_strike", None), ("price_ranges", None)]
)
def test_malformed_final_shapes_stay_unresolved(store, config, market, book, now, series, field, value):
    e = opened(store, config, market, book, now, series)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 1)
    p = proof(market.raw, series)
    if field in ("market", "series"):
        p[field] = value
    else:
        p["market"][field] = value
    assert e.settle(market.ticker, "yes", market.close_time + 1, evidence=p) == "BLOCKED"
    assert store.list(kind="settlement_blocked")
    assert not store.list(kind="trade_result") and e.executor.positions


def test_metadata_update_cannot_relabel_an_unclassified_halt(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    e.executor.cancel(market.ticker, now + 1, "operator_review")
    e.state(market.ticker, "HALTED", now + 1)
    metadata(e, {**market.raw, "floor_strike": None}, series, now + 2)
    assert market.ticker not in e.executor.quarantines
    assert (
        e.settle(market.ticker, "yes", market.close_time + 1, evidence=proof(market.raw, series)) == "BLOCKED"
    )
    assert not store.list(kind="trade_result")


def test_manual_review_rejects_superseded_evidence(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    changed = {**market.raw, "floor_strike": market.spec.strike + 10}
    for offset, result in ((1, "yes"), (2, "no")):
        assert (
            e.settle(
                market.ticker, result, market.close_time + offset, evidence=proof(changed, series, result)
            )
            == "BLOCKED"
        )
    rows = recover_settlement(store, e.run_id, market.ticker)["evidence"]
    assert len(rows) == 2
    with pytest.raises(ValueError, match="newer final evidence"):
        recover_settlement(
            store,
            e.run_id,
            market.ticker,
            evidence_id=rows[0]["id"],
            confirm=rows[0]["evidence_hash"],
            reason="Old review",
        )
    assert not store.list(kind="trade_result")
    applied = recover_settlement(
        store,
        e.run_id,
        market.ticker,
        evidence_id=rows[1]["id"],
        confirm=rows[1]["evidence_hash"],
        reason="Reviewed latest finalized result",
    )
    assert applied["result"] == "SETTLED"
    assert store.list(kind="trade_result")[0]["body"]["settlement_result"] == "no"


def test_unrelated_metadata_changes_do_not_quarantine(store, config, market, book, now, series):
    e = opened(store, config, market, book, now, series)
    metadata(e, {**market.raw, "title": "Updated display title", "volume_fp": "123.00"}, series, now + 1)
    assert not e.executor.quarantines
    assert not store.list(kind="metadata_quarantine")
    assert e.executor.positions
