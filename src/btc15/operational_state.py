"""Read-only operator view; this projection never authorizes trading."""

MESSAGES = {
    "NO_STATUS": "No collector status is available.",
    "STALE_STATUS": "Collector status is stale; running state cannot be confirmed.",
    "STARTUP_FAILED": "Collector startup failed. Check the service log.",
    "WRITER_LEASE_BLOCKED": "Collector startup is blocked by database ownership. Another writer is active or its previous owner cannot be verified dead.",
    "COLLECTOR_FAILED": "Collector stopped after an error. Check the service log before restarting.",
    "DISCONNECTED": "Market-data connection is unavailable.",
    "HALTED": "The risk halt is active.",
    "STRATEGY_DISABLED": "Strategy entries are disabled in this run.",
    "MODEL_INACTIVE": "New entries are paused for this run.",
    "OBSERVE_ONLY": "Observation mode records data without placing paper orders.",
    "SETTLEMENT_RECOVERY_REQUIRED": "A held contract requires settlement recovery.",
    "CLOCK_SKEW": "The clock check has not passed.",
    "EXCHANGE_CLOSED": "The exchange is not available for trading.",
    "WAITING_FOR_CONNECTION": "Waiting for a new market-data connection.",
    "WAITING_FOR_FRESH_METADATA": "Waiting for fresh contract, fee, and exchange metadata.",
    "WAITING_FOR_FRESH_REFERENCE": "Waiting for a fresh official reference tick.",
    "WAITING_FOR_SEQUENCED_SNAPSHOT": "Waiting for a fresh, sequenced order-book snapshot.",
    "BACKLOG_NOT_DRAINED": "Waiting for the processing backlog to drain.",
    "PROCESSING_OVERLOAD": "Processing fell behind; draining old data before reconnecting.",
    "DATA_INTEGRITY_FAILURE": "Data integrity failed; rebuilding from a new connection.",
    "FEED_INTERRUPTED": "The feed was interrupted; rebuilding fresh data.",
    "CONNECTION_LOST": "The connection was lost; reconnecting.",
    "REFERENCE_STREAM_STALLED": "Required reference data stopped progressing; reconnecting for fresh data.",
    "BOOK_STREAM_STALLED": "Order-book data stopped progressing; reconnecting for a fresh sequenced snapshot.",
    "MARKET_INTEGRITY": "Contract integrity or venue-pause checks block this market.",
    "NO_ACTIVE_MARKET": "Waiting for an active, validated market.",
    "STOPPING": "The collector is shutting down safely.",
    "BOOK_INVALID": "The order book has not passed validation.",
    "BOOK_RECEIVE_AGE": "Received order-book data is stale.",
    "BOOK_SOURCE_AGE": "The order-book source timestamp is stale or invalid.",
    "REFERENCE_RECEIVE_AGE": "Received reference data is stale.",
    "REFERENCE_SOURCE_AGE": "The reference source timestamp is stale or invalid.",
    "PROCESSING_LAG": "Processing is behind the live feed.",
    "NO_FRESH_EVALUATION": "Waiting for a fresh evaluation for this run.",
    "WARMUP": "Collecting enough reference history for the model.",
    "MODEL_UNAVAILABLE": "The model is waiting for usable reference history.",
    "REFERENCE_GAP": "Waiting for uninterrupted reference history after a gap.",
    "ENTRY_WINDOW": "Outside the configured entry time window.",
    "EXISTING_ENTRY": "Existing exposure, an order, or re-entry rules block this entry.",
    "RISK_LIMIT": "A risk limit blocks this entry.",
    "COLLECTOR_RECOVERING": "Entries are blocked while collector recovery completes.",
    "BACKLOG_WARNING": "Processing lag or outstanding queue usage is elevated.",
    "REFERENCE_AGE_WARNING": "Reference age exceeds the monitoring threshold.",
}


def reason(code, message=None):
    base, _, market = code.partition(":")
    return dict(
        code=base,
        message=message or MESSAGES.get(base, base.replace("_", " ").capitalize()),
        **({"market": market} if market else {}),
    )


def operational_state(status, evaluation, now, *, startup_error=None, reference=None, failure=None):
    body = status["body"] if status else {}
    run_id = body.get("run_id") or (status or {}).get("run_id")
    age = now - status["timestamp"] if status else None
    fresh = age is not None and 0 <= age < 5
    if not (
        failure
        and not fresh
        and 0 <= failure.get("timestamp", -1) <= now
        and (not status or failure["timestamp"] >= status["timestamp"])
    ):
        failure = None
    if failure:
        run_id = failure["run_id"]
    recovery = body.get("recovery") or {}
    # The receipt projection can expose recovery even while the worker is stuck.
    reference_current = bool(
        reference and reference.get("run_id") == run_id and 0 <= now - reference.get("published_at", 0) < 2
    )
    if reference_current and reference.get("recovery", {}).get("entries_blocked"):
        recovery = reference["recovery"]
    connected = bool(fresh and body.get("connected"))
    if reference_current and reference.get("connected") is False:
        connected = False
    member = next((m for m in body.get("models", []) if m.get("run_id") == run_id), {})
    blockers = []
    if failure:
        blockers.append(reason(failure["code"]))
    if startup_error:
        blockers.append(reason("STARTUP_FAILED"))
    if not status:
        blockers.append(reason("NO_STATUS"))
    elif not fresh:
        blockers.append(reason("STALE_STATUS"))
    if body.get("halted") or member.get("halted"):
        blockers.append(reason("HALTED"))
    if body.get("settlement_recovery"):
        blockers.append(reason("SETTLEMENT_RECOVERY_REQUIRED"))
    if member.get("strategy_enabled") is False:
        blockers.append(reason("STRATEGY_DISABLED"))
    if member.get("entries_active") is False:
        blockers.append(reason("MODEL_INACTIVE"))
    if body.get("paper_execution") is False:
        blockers.append(reason("OBSERVE_ONLY"))
    recovery_reasons = [reason(c) for c in recovery.get("reasons", [])] if fresh else []
    if fresh and not recovery.get("entries_blocked"):
        if not body.get("clock_ok"):
            blockers.append(reason("CLOCK_SKEW"))
        if not body.get("exchange_open"):
            blockers.append(reason("EXCHANGE_CLOSED"))
        if not connected:
            blockers.append(reason("DISCONNECTED"))
    warnings = [reason("BACKLOG_WARNING")] if fresh and recovery.get("warning") else []
    if fresh:
        if body.get("reference_age") is None or not 0 <= body["reference_age"] <= 2:
            warnings.append(reason("REFERENCE_AGE_WARNING"))
        if body.get("processing_lag") is None or not 0 <= body["processing_lag"] <= 1:
            warnings.append(reason("PROCESSING_LAG"))
    valid_eval = bool(
        evaluation and evaluation.get("run_id") == run_id and 0 <= now - evaluation["timestamp"] <= 10
    )
    decision = evaluation["body"] if valid_eval else {}
    close = decision.get("market_close_timestamp")
    if isinstance(close, (int, float)) and close <= now:
        valid_eval, decision = False, {}
    filters = [
        dict(reason(r["code"], r.get("message")), **({"actual": r["actual"]} if "actual" in r else {}))
        for r in decision.get("reasons", [])
    ]
    if blockers:
        state, summary, entry = "BLOCKED", blockers[0]["message"], "BLOCKED"
        if not fresh or startup_error or failure:
            entry = "UNKNOWN"
        reasons = blockers + recovery_reasons
    elif recovery.get("entries_blocked"):
        state, entry = "RECOVERING", "BLOCKED"
        summary = (
            "Draining recorded backlog."
            if recovery.get("state") == "DRAINING"
            else "Rebuilding and checking fresh market data."
        )
        reasons = recovery_reasons or [reason("COLLECTOR_RECOVERING")]
    elif not valid_eval or any(
        r["code"] in ("WARMUP", "MODEL_UNAVAILABLE", "REFERENCE_GAP") for r in filters
    ):
        state, summary, entry = "COLLECTING", "Collecting data; no usable current evaluation yet.", "CHECKING"
        reasons = filters or [reason("NO_FRESH_EVALUATION")]
    else:
        state, summary, reasons = "EVALUATING", "Evaluating current market data.", []
        entry = "CANDIDATE" if decision.get("decision") == "TRADE_CANDIDATE" else "WAITING"
    return dict(
        state=state,
        summary=summary,
        run_id=run_id,
        entry_status=entry,
        reasons=reasons,
        entry_reasons=filters,
        evaluation_market=decision.get("ticker"),
        evaluation_age=now - evaluation["timestamp"] if valid_eval else None,
        status_age=age,
        connected=connected,
        failure=dict(failure, message=reason(failure["code"])["message"]) if failure else None,
        recovery_phase=recovery.get("state") if fresh else None,
        warnings=warnings,
        processing_lag=body.get("processing_lag") if fresh else None,
        queue_depth=body.get("queue_depth") if fresh else None,
        queue_capacity=body.get("queue_capacity") if fresh else None,
        reference_age=body.get("reference_age") if fresh else None,
    )
