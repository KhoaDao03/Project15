"""Immutable, local execution evidence; no network calls or trading authority."""

import json
import math
import time
import uuid
from decimal import Decimal, InvalidOperation

from .domain import dumps

# Explicitly exclude account identifiers, authentication and future arbitrary fields.
ORDER_FIELDS = (
    "id",
    "request",
    "state",
    "created_at",
    "updated_at",
    "origin",
    "automation_reason",
    "timing",
    "order_id",
    "exchange_index",
    "effective_limit_cents",
    "resting_take_profit",
    "cancel_requested_at",
    "cancel_attempted_at",
    "message",
    "exchange_error",
    "fill_notification",
)
EXCHANGE_FIELDS = (
    "order_id",
    "client_order_id",
    "ticker",
    "status",
    "fill_count_fp",
    "remaining_count_fp",
    "initial_count_fp",
    "yes_price_dollars",
    "no_price_dollars",
    "maker_fees_dollars",
    "taker_fees_dollars",
    "maker_fill_cost_dollars",
    "taker_fill_cost_dollars",
    "outcome_side",
    "created_time",
    "last_update_time",
    "action",
    "side",
)


def order_snapshot(row):
    result = {k: row[k] for k in ORDER_FIELDS if k in row}
    result["request"] = {k: v for k, v in row.get("request", {}).items() if k != "confirm"}
    for name in ("exchange_order", "acknowledgement"):
        if row.get(name):
            result[name] = {k: row[name][k] for k in EXCHANGE_FIELDS if k in row[name]}
    return result


def initialize(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS execution_events ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
        "market TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS execution_events_market ON execution_events(market,seq)")
    for action in ("UPDATE", "DELETE"):
        db.execute(
            f"CREATE TRIGGER IF NOT EXISTS execution_events_no_{action.lower()} "
            f"BEFORE {action} ON execution_events BEGIN "
            "SELECT RAISE(ABORT, 'Execution events are immutable'); END"
        )


def append(db, kind, *, row=None, body=None, market=None, received_at=None, source_time=None, event_id=None):
    now = time.time()
    event = dict(
        event_id=event_id or str(uuid.uuid4()),
        kind=kind,
        market=market if market is not None else (row or {}).get("request", {}).get("ticker", ""),
        order_id=(row or {}).get("id"),
        exchange_order_id=(row or {}).get("order_id"),
        decision_id=(row or {}).get("timing", {}).get("decision_id"),
        execution_check_id=(row or {}).get("timing", {}).get("execution_check_id"),
        source_time=source_time,
        received_at=received_at,
        observed_at=now,
        observed_monotonic_ns=time.monotonic_ns(),
        body=body or {},
    )
    if row is not None:
        event["order"] = order_snapshot(row)
    cursor = db.execute(
        "INSERT OR IGNORE INTO execution_events(event_id,market,kind,body) VALUES (?,?,?,?)",
        (event["event_id"], event["market"], kind, dumps(event)),
    )
    if not cursor.rowcount:
        return None
    event["journal_seq"] = cursor.lastrowid
    return event


def read_events(db, after=0, prefix="", limit=200):
    for seq, raw in db.execute(
        "SELECT seq,body FROM execution_events WHERE seq>? AND (market LIKE ? OR market='') ORDER BY seq LIMIT ?",
        (after, prefix + "%", limit),
    ):
        yield dict(json.loads(raw), journal_seq=seq)


def fill_details(fill, side):
    """Preserve exact exchange decimals; missing prices/fees stay unknown."""
    raw = {
        k: fill[k]
        for k in (
            "trade_id",
            "order_id",
            "client_order_id",
            "market_ticker",
            "exchange_index",
            "action",
            "side",
            "purchased_side",
            "outcome_side",
            "book_side",
            "is_taker",
            "yes_price_dollars",
            "no_price_dollars",
            "count_fp",
            "fee_cost",
            "ts",
            "ts_ms",
            "post_position_fp",
        )
        if k in fill
    }
    price = fill.get(side + "_price_dollars") if side in ("yes", "no") else None
    try:
        if price is None and side == "no" and fill.get("yes_price_dollars") is not None:
            price = str(1 - Decimal(fill["yes_price_dollars"]))
        price_ok = price is not None and Decimal(price).is_finite() and 0 <= Decimal(price) <= 1
        fee = fill.get("fee_cost")
        fee_ok = fee is not None and Decimal(fee).is_finite() and Decimal(fee) >= 0
    except (InvalidOperation, ValueError, TypeError):
        price_ok = fee_ok = False
    return dict(
        fill_id=fill.get("trade_id"),
        side=side,
        action=fill.get("action"),
        quantity=fill.get("count_fp"),
        price_dollars=price if price_ok else None,
        fee_dollars=fill.get("fee_cost") if fee_ok else None,
        economics_complete=price_ok and fee_ok,
        exchange_fill=raw,
    )


def fill_source_time(fill):
    try:
        value = float(fill["ts_ms"]) / 1000 if "ts_ms" in fill else float(fill["ts"])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
