"""BTC-only live-entry exception for confirmed processing backlog.

The operator accepts aged trading inputs during backlog. Connection/integrity,
metadata, strategy and account checks remain independent. Health stays truthful.
"""

import math

HEALTH_REASONS = frozenset(("COLLECTOR_RECOVERING", "BACKLOG_WARNING", "PROCESSING_LAG", "STALE_REFERENCE"))
AGE_REASONS = frozenset(
    ("BOOK_RECEIVE_AGE", "BOOK_SOURCE_AGE", "REFERENCE_RECEIVE_AGE", "REFERENCE_SOURCE_AGE")
)
DECISION_REASONS = HEALTH_REASONS | AGE_REASONS | {"STALE_BOOK"}


def finite_age(value, minimum=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def permitted(asset, report):
    """Only a current, connected status can establish a backlog exception."""
    if asset != "BTC" or not finite_age(report.get("status_age")) or report["status_age"] > 5:
        return False
    status = report.get("status", {})
    recovery = status.get("recovery", {})
    reasons = set(report.get("reasons", []))
    recovery_reasons = {r.split(":", 1)[0] for r in recovery.get("reasons", [])}
    if (
        not reasons
        or not reasons <= HEALTH_REASONS
        or not all(status.get(k) for k in ("connected", "clock_ok", "exchange_open", "live_signals"))
        or status.get("halted")
        or status.get("settlement_recovery")
        or recovery.get("state") not in ("READY", "RECOVERING")
        or not recovery_reasons <= AGE_REASONS | {"BACKLOG_NOT_DRAINED", "WAITING_FOR_FRESH_REFERENCE"}
    ):
        return False
    return (
        finite_age(status.get("queue_depth"), 1)
        and finite_age(status.get("processing_lag"), 0.5)
        and ("BACKLOG_WARNING" in reasons or "BACKLOG_NOT_DRAINED" in recovery_reasons)
    )


def decision_reasons(decision, config, bypass):
    """Remove only age/backlog failures, including their quality-score penalty."""
    ignored = {"EXISTING_ENTRY", "POST_CLOSE_COOLDOWN"}
    if bypass:
        ignored |= DECISION_REASONS
        quality = decision.get("quality", {})
        stale = set(quality.get("reasons", [])) & {"STALE_REFERENCE", "STALE_BOOK"}
        score = quality.get("score")
        if stale and finite_age(score) and min(100, score + 25 * len(stale)) >= config.min_quality:
            ignored.add("MODEL_QUALITY")
    return [r for r in decision.get("reasons", []) if r["code"] not in ignored]
