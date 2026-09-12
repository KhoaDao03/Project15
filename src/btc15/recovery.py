"""Explicit settlement recovery for quarantined PAPER/BACKTEST contracts.

No prices or user-supplied outcomes are converted into settlement results. The
operator can acknowledge a retained, parser-validated final REST observation.
"""

import copy
import hashlib
import time
import uuid
from dataclasses import asdict

from .domain import Market, SettlementSpecification, dumps, parse_market


def restore_market(snapshot):
    body = copy.deepcopy(snapshot)
    body["spec"] = SettlementSpecification(**body["spec"])
    return Market(**body)


def contract_identity(market):
    return dict(
        ticker=market.ticker,
        event_ticker=market.event_ticker,
        market_id=market.market_id,
        exchange_index=market.exchange_index,
    )


def contract_hash(market):
    # Ignore quotes, volume, titles and status, but pin all settlement semantics.
    body = dict(
        **contract_identity(market),
        open_time=market.open_time,
        close_time=market.close_time,
        spec=asdict(market.spec),
    )
    return hashlib.sha256(dumps(body).encode()).hexdigest()


def evidence_hash(evidence):
    return hashlib.sha256(dumps(evidence).encode()).hexdigest()


def validate_final_evidence(pinned, result, now, evidence):
    if not isinstance(evidence, dict) or evidence.get("source") != "kalshi_rest":
        raise ValueError("FINAL_REST_METADATA_REQUIRED")
    try:
        final = parse_market(evidence["market"], evidence["series"])
    except (AttributeError, KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise ValueError("INVALID_FINAL_METADATA: " + str(exc)) from exc
    if contract_identity(final) != contract_identity(pinned):
        raise ValueError("FINAL_IDENTITY_MISMATCH")
    if final.status != "finalized" or evidence["market"].get("result") != result:
        raise ValueError("FINAL_RESULT_NOT_CONFIRMED")
    if result not in ("yes", "no") or now < max(pinned.close_time, final.close_time):
        raise ValueError("PREMATURE_OR_UNSUPPORTED_SETTLEMENT")
    return final


def recover_settlement(store, run_id, ticker, *, evidence_id=None, confirm=None, reason=None):
    """Preview by default; apply only with an immutable evidence ID and its hash.

    Application holds the same database writer lease as the collector. In
    BACKTEST, record the adjustment at evidence time, never the current wall time.
    """
    from .config import Strategy
    from .execution import PaperExecutor
    from .models import require_single_run

    require_single_run(store, run_id)
    runs = store.list(kind="run", run_id=run_id, limit=1)
    checkpoint = store.load_checkpoint(run_id)
    if not runs or not checkpoint or runs[0]["mode"] not in ("PAPER", "BACKTEST"):
        raise ValueError("An existing research run and atomic checkpoint are required")
    if ticker not in checkpoint.get("contracts", {}):
        raise ValueError("No pinned contract: resume once with this version to validate legacy history")
    rows = store.list(kind="settlement_evidence", run_id=run_id, market=ticker, limit=None)
    if evidence_id is None:
        if confirm is not None or reason is not None:
            raise ValueError("Specify --evidence-id with --confirm and --reason to apply recovery")
        return dict(
            run_id=run_id,
            mode=runs[0]["mode"],
            market=ticker,
            state=store.state(run_id, ticker),
            quarantine=checkpoint.get("quarantines", {}).get(ticker),
            pinned_contract=checkpoint["contracts"][ticker],
            evidence=[dict(id=r["id"], timestamp=r["timestamp"], **r["body"]) for r in rows],
            action="Preview only. Review evidence, then provide its ID, hash and a reason after stopping the writer.",
        )
    if not isinstance(reason, str) or not reason.strip() or not confirm:
        raise ValueError("Applying recovery requires an evidence hash and a nonempty review reason")
    owner = "settlement-recovery-" + uuid.uuid4().hex
    store.acquire("collector", owner)
    try:
        # Re-read after taking the lease: previewed state is not write authority.
        executor = PaperExecutor(store, run_id, runs[0]["mode"], Strategy(**runs[0]["body"]["config"]))
        executor.restore(store.load_checkpoint(run_id))
        rows = store.list(kind="settlement_evidence", run_id=run_id, market=ticker, limit=None)
        row = next((r for r in rows if r["id"] == evidence_id), None)
        if row is None or row["mode"] != executor.mode:
            raise ValueError("Evidence does not belong to this run, market and mode")
        body = row["body"]
        if confirm != body["evidence_hash"] or confirm != evidence_hash(body["evidence"]):
            raise ValueError("Evidence hash mismatch")
        # Do not authorize a superseded final result using an older preview.
        if any(r["timestamp"] >= row["timestamp"] and r["body"]["evidence_hash"] != confirm for r in rows):
            raise ValueError("Conflicting or newer final evidence requires a fresh review")
        pinned = restore_market(executor.contracts[ticker])
        if body["expected_contract_hash"] != contract_hash(pinned):
            raise ValueError("Pinned contract changed since evidence was recorded")
        now = max(time.time(), row["timestamp"]) if executor.mode == "PAPER" else row["timestamp"]
        result = executor.settle(
            pinned,
            body["result"],
            now,
            evidence=body["evidence"],
            review=dict(evidence_id=evidence_id, evidence_hash=confirm, reason=reason.strip()),
        )
        return dict(run_id=run_id, market=ticker, result=result, state=store.state(run_id, ticker))
    finally:
        store.release("collector", owner)


def restore_contract_history(executor, now):
    """Rebuild legacy entry identity from retained, validated pre-entry metadata.

    Checkpointed identities win over later metadata. Missing/ambiguous history
    fails explicitly; no contract or result is guessed from a ticker.
    """
    history = executor.store.list(kind="market", run_id=executor.run_id, limit=None)
    parsed = [(r, parse_market(r["body"]["raw"], r["body"]["series"])) for r in history]
    markets = {m.ticker: m for _, m in parsed}
    for ticker in set(executor.orders) | set(executor.positions):
        order = executor.orders.get(ticker)
        if ticker not in executor.positions and not (order and order.active):
            continue
        entered = order.created if order else executor.positions[ticker].opened
        legacy_contract = ticker not in executor.contracts
        if legacy_contract:
            candidates = [(r, m) for r, m in parsed if m.ticker == ticker and r["timestamp"] <= entered]
            if not candidates:
                raise ValueError(
                    f"Missing entry-time contract history for {ticker}; forensic recovery required"
                )
            latest_time = max(r["timestamp"] for r, _ in candidates)
            latest = [m for r, m in candidates if r["timestamp"] == latest_time]
            if len({contract_hash(m) for m in latest}) != 1:
                raise ValueError(
                    f"Ambiguous entry-time contract history for {ticker}; forensic recovery required"
                )
            executor.contracts[ticker] = asdict(latest[-1])
        pinned = restore_market(executor.contracts[ticker])
        changed = any(
            m.ticker == ticker and r["timestamp"] >= entered and contract_hash(m) != contract_hash(pinned)
            for r, m in parsed
        )
        if changed or executor.store.has_market_integrity_failure(executor.run_id, ticker, entered):
            # Only old checkpoints need legacy classification. Never convert a
            # modern unrelated HALTED state into a recoverable metadata halt.
            reason = "LEGACY_METADATA_QUARANTINE" if legacy_contract else "METADATA_HISTORY_RECONCILIATION"
            executor.quarantine(pinned, now, reason)
        if ticker in executor.quarantines:
            markets[ticker] = pinned
    for ticker in executor.quarantines:
        markets[ticker] = restore_market(executor.contracts[ticker])
    return markets
