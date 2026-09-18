"""Realized live bot P&L; paper fills never enter the loss guard."""

from collections import defaultdict
from decimal import Decimal

from .domain import timestamp

LIMIT = Decimal("-20")


def order_time(row):
    value = (row.get("exchange_order") or {}).get("last_update_time")
    return timestamp(value) if value is not None else row["created_at"]


def daily_pnl(rows, settlements, now):
    """Allocate entry cost/fees pro rata to sales, then settle the remainder.

    Order economics are cumulative. A sale spanning UTC days is ambiguous and
    blocks new buys rather than assigning previous-day fills to today's P&L.
    """
    start = int(now // 86400) * 86400
    groups = defaultdict(list)
    for row in rows:
        groups[row["request"]["ticker"]].append(row)
    total = Decimal(0)
    for ticker, orders in groups.items():
        settlement = settlements.get(ticker)
        latest = max(order_time(r) for r in orders)
        if latest < start and (not settlement or settlement["timestamp"] < start):
            continue
        buys = [
            r
            for r in orders
            if r.get("origin") == "bot"
            and r["request"]["action"] == "buy"
            and Decimal((r.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0
        ]
        if not buys:
            continue
        side = buys[0]["request"]["side"]
        bought = basis = sold = Decimal(0)
        sales = []
        for row in orders:
            order = row.get("exchange_order") or {}
            qty = Decimal(order.get("fill_count_fp", "0"))
            if not qty.is_finite() or qty < 0:
                raise ValueError("Invalid live fill quantity")
            if not qty:
                continue
            action = row["request"]["action"]
            if row["request"]["side"] != side or (action == "buy" and row.get("origin") != "bot"):
                raise ValueError("Mixed live positions cannot be allocated to bot")
            paid = sum(
                (Decimal(order[k]) for k in ("maker_fill_cost_dollars", "taker_fill_cost_dollars")),
                Decimal(0),
            )
            fees = sum((Decimal(order[k]) for k in ("maker_fees_dollars", "taker_fees_dollars")), Decimal(0))
            expected = side if action == "buy" else ("no" if side == "yes" else "yes")
            if (
                order["outcome_side"] != expected
                or not all(v.is_finite() for v in (paid, fees))
                or not 0 <= paid <= qty
                or fees < 0
            ):
                raise ValueError("Invalid live fill economics")
            if action == "buy":
                bought += qty
                basis += paid + fees
            else:
                at = order_time(row)
                if row["created_at"] < start <= at:
                    raise ValueError("Cross-day cumulative sale requires fill-level accounting")
                sold += qty
                sales.append((qty, qty - paid - fees, at))
        if sold > bought or bought <= 0:
            raise ValueError("Live sales exceed bot purchases")
        for qty, proceeds, at in sales:
            if start <= at <= now:
                total += proceeds - basis * qty / bought
        remaining = bought - sold
        if remaining and settlement and start <= settlement["timestamp"] <= now:
            result = settlement["body"]["result"]
            if result not in ("yes", "no"):
                raise ValueError("Invalid settlement result")
            total += (remaining if result == side else 0) - basis * remaining / bought
    return total
