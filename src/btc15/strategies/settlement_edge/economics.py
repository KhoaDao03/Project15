"""Reporting-only entry scenarios. Never used as an acceptance or sizing gate."""

from decimal import ROUND_FLOOR

from ...domain import D
from .rules import FeeAccumulator


def entry_economics(
    market, book, side, price, quantity, probability, config, now, *, stage, entry_slippage=0
):
    if price is None or probability is None or side not in ("yes", "no"):
        return dict(version="both-leg-v1", status="UNAVAILABLE", reason="Missing price, side or probability")
    q, p, entry, allowance = map(D, (quantity, probability, price, entry_slippage))
    if (
        not all(x.is_finite() for x in (q, p, entry, allowance))
        or not 0 <= p <= 1
        or not 0 < entry < 1
        or allowance < 0
    ):
        return dict(version="both-leg-v1", status="UNAVAILABLE", reason="Invalid scenario inputs")
    if q <= 0 or q % D(".01"):
        return dict(
            version="both-leg-v1",
            status="UNAVAILABLE",
            reason="No executable intended quantity",
            quantity=float(q),
        )
    buy_fee = D(
        FeeAccumulator(config.fee_balance_precision).charge(
            float(entry), float(q), config.taker_fee_rate, "buy"
        )
    )
    debit = q * (entry + allowance) + buy_fee
    out = dict(
        version="both-leg-v1",
        status="AVAILABLE",
        reporting_only=True,
        stage=stage,
        quantity=float(q),
        probability=float(p),
        entry_price=float(entry),
        entry_fee=float(buy_fee),
        entry_slippage_total=float(q * allowance),
        total_entry_cost=float(debit),
        fee_rate=config.taker_fee_rate,
        balance_precision=config.fee_balance_precision,
        fee_assumption="Taker fees; one fill per assumed leg, depth fills share one unwind order",
        settlement=dict(
            ev_total=float(p * q - debit),
            ev_per_contract=float(p - debit / q),
            win_net_total=float(q - debit),
            loss_net_total=float(-debit),
            exit_fee=0,
        ),
    )

    def sale(price):
        fee = D(
            FeeAccumulator(config.fee_balance_precision).charge(
                price, float(q), config.taker_fee_rate, "sell"
            )
        )
        proceeds = q * D(price) - fee
        return dict(
            status="AVAILABLE",
            assumed_sale_price=price,
            exit_fee=float(fee),
            net_total=float(proceeds - debit),
            net_per_contract=float((proceeds - debit) / q),
        )

    try:
        target = market.snap(config.take_profit, up=True) if config.take_profit is not None else None
        target_case = sale(target) if target is not None else dict(status="DISABLED")
        if target is not None:
            target_case.update(
                probability_weighted_proxy_total=float(
                    p * (q * D(target) - D(target_case["exit_fee"])) - debit
                ),
                probability_weighted_proxy_per_contract=float(
                    (p * (q * D(target) - D(target_case["exit_fee"])) - debit) / q
                ),
                assumption="Settlement winners sell at target; losers pay zero. Not a dynamic-exit EV.",
            )
    except ValueError:
        target_case = dict(status="NO_SUPPORTED_TICK")
    out["target_sale"] = target_case
    try:
        stop_case = sale(market.snap(entry * D(config.stop_multiplier)))
        stop_case.update(
            trigger_price=float(entry * D(config.stop_multiplier)),
            assumption="Full sale at rounded stop threshold; future depth and gaps unknown. Not a loss cap.",
        )
    except ValueError:
        stop_case = dict(status="NO_SUPPORTED_TICK")
    out["stop_exit"] = stop_case

    unwind = dict(status="NO_FRESH_BOOK", net_total=None)
    if (
        book is not None
        and book.valid
        and 0 <= now - book.received <= config.book_max_age
        and -config.max_clock_skew <= now - book.source_time <= config.book_max_age
    ):
        remaining, revenue, fees = q, D(0), D(0)
        accumulator = FeeAccumulator(config.fee_balance_precision)
        levels = book.yes if side == "yes" else book.no
        for level, size in sorted(levels.items(), reverse=True):
            amount = min(remaining, size).quantize(D(".01"), rounding=ROUND_FLOOR)
            if amount <= 0 or not market.valid_tick(float(level)):
                continue
            revenue += amount * level
            fees += D(accumulator.charge(float(level), float(amount), config.taker_fee_rate, "sell"))
            remaining -= amount
            if not remaining:
                break
        unwind = dict(
            status="AVAILABLE" if not remaining else "INSUFFICIENT_DEPTH",
            filled_quantity=float(q - remaining),
            exit_fee=float(fees),
            depth_proceeds=float(revenue),
            net_total=float(revenue - fees - debit) if not remaining else None,
            assumption="Immediate sale into current bids; spread is already included, no extra exit haircut.",
        )
    out["immediate_unwind"] = unwind
    return out
