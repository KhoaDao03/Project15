"""Indexes and transactional revisions for the read-only dashboard history cache."""

HISTORY_TRIGGERS = (
    "manual_history_insert",
    "manual_history_replace",
    "manual_history_update",
    "manual_history_delete",
)


def initialize(db):
    db.execute(
        "CREATE INDEX IF NOT EXISTS manual_orders_ticker_prefix ON manual_orders("
        "json_extract(body, '$.request.ticker') COLLATE NOCASE)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS manual_history_revisions (prefix TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
    )

    def prefix(row):
        ticker = f"json_extract({row}.body, '$.request.ticker')"
        return f"upper(substr({ticker}, 1, instr({ticker}, '-') - 1))"

    def bump(value, condition="1"):
        return (
            "INSERT INTO manual_history_revisions(prefix,revision) "
            f"SELECT {value},1 WHERE ({condition}) AND {value} IS NOT NULL "
            "ON CONFLICT(prefix) DO UPDATE SET revision=revision+1; "
        )

    # Only fields used by LiveFallbackStore's accounting/history projection.
    # Reconciliation diagnostics, timing and state-only updates are not history.
    fields = (
        "id",
        "origin",
        "request",
        "created_at",
        "exchange_order",
        "automation_reason",
        "dashboard_history_cleared",
    )
    paths = ",".join("'$." + field + "'" for field in fields)
    relevant_change = f"json_extract(OLD.body,{paths}) IS NOT json_extract(NEW.body,{paths})"
    old, new = prefix("OLD"), prefix("NEW")
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS manual_history_insert AFTER INSERT ON manual_orders BEGIN "
        + bump(new)
        + "END"
    )
    # REPLACE may delete the old row without firing DELETE triggers. Capture a
    # moved order's previous asset before the replacement, on every connection.
    previous = f"(SELECT {prefix('manual_orders')} FROM manual_orders WHERE id=NEW.id)"
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS manual_history_replace BEFORE INSERT ON manual_orders BEGIN "
        + bump(previous, f"{previous} IS NOT {new}")
        + "END"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS manual_history_update AFTER UPDATE ON manual_orders "
        f"WHEN OLD.body IS NOT NEW.body AND ({relevant_change}) BEGIN "
        + bump(old)
        + bump(new, f"{new} IS NOT {old}")
        + "END"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS manual_history_delete AFTER DELETE ON manual_orders BEGIN "
        + bump(old)
        + "END"
    )
