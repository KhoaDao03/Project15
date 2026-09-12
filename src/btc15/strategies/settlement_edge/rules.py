from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR

from ...domain import D


@dataclass
class FeeAccumulator:
    precision: str = "0.01"
    carry: object = D(0)

    def charge(self, price, quantity, rate, action="buy"):
        p, q, r = D(price), D(quantity), D(rate)
        trade = (r * q * p * (1 - p)).quantize(D(".000001"), rounding=ROUND_CEILING)
        revenue = p * q * (-1 if action == "buy" else 1)
        precision = D(self.precision)
        aligned = ((revenue - trade) / precision).to_integral_value(rounding=ROUND_FLOOR) * precision
        rounding = revenue - trade - aligned
        self.carry += rounding
        rebate = min(
            (self.carry / precision).to_integral_value(rounding=ROUND_FLOOR) * precision,
            ((trade + rounding) / precision).to_integral_value(rounding=ROUND_FLOOR) * precision,
        )
        self.carry -= rebate
        return float(trade + rounding - rebate)


def fee_bound(price, config):
    # Per-contract upper bound: no rounding rebates, taker rate even for passive entries.
    return float(
        (D(config.taker_fee_rate) * D(price) * (1 - D(price))).quantize(D(".000001"), rounding=ROUND_CEILING)
        + D(config.fee_balance_precision)
    )


def effective_entry_ceiling(market, conservative, config, *, late=False):
    """Largest supported ask passing probability and entry-value filters only."""
    if conservative < config.probability_floor(late):
        return None
    required = D(max(config.min_edge, config.min_ev))

    def affordable(price):
        return D(conservative) - price - D(fee_bound(price, config)) - D(config.slippage) >= required

    candidates = []
    for band in market.price_ranges:
        start, end, step = (D(band[k]) for k in ("start", "end", "step"))
        low = max(
            0, int(((D(config.min_entry_price) - start) / step).to_integral_value(rounding=ROUND_CEILING))
        )
        high = int(
            ((min(end, D(config.max_entry_price)) - start) / step).to_integral_value(rounding=ROUND_FLOOR)
        )
        # Cost is concave in price: an affordable upper endpoint is already maximal.
        if low > high:
            continue
        if affordable(start + high * step):
            candidates.append(start + high * step)
            continue
        if not affordable(start + low * step):
            continue
        while low < high:
            mid = (low + high + 1) // 2
            if affordable(start + mid * step):
                low = mid
            else:
                high = mid - 1
        candidates.append(start + low * step)
    return float(max(candidates)) if candidates else None


def evaluate(
    market, book, tick, features, probability, quality, now, config, extra_reasons=(), *, entry_price=None
):
    remaining = market.close_time - now
    side = market.spec.favored(tick.price)
    path = "late_settlement" if config.late_entry_enabled and remaining <= config.no_new_entry else "standard"
    late = path == "late_settlement"
    minimum_probability = config.probability_floor(late)
    lead = probability.get("lead", {})
    if path == "late_settlement":
        side = lead.get("side")
    ask, liquidity = book.ask_level(side) if side else (None, 0)
    bid = book.bid(side) if side else None
    penalty = 0
    if config.entry_probability_deductions:
        conservative = probability["conservative_" + side] if side else 0
        if config.sustained_lead_enabled:
            conservative = min(conservative, lead.get("stressed_probability", 0))
        penalty = min(0.05, features["volatility_disagreement"] * 0.01)
        conservative = max(0, conservative - penalty)
    else:
        conservative = probability["p_" + side] if side else 0
    spread = float(D(ask) - D(bid)) if ask is not None and bid is not None else None
    price = ask if entry_price is None else entry_price
    fee = fee_bound(price, config) if price is not None else None
    # An IOC limit already includes its slippage allowance; do not deduct it twice.
    slippage = 0 if entry_price is not None and not config.passive else config.slippage
    ev = D(conservative) - D(price) - D(fee) - D(slippage) if price is not None else None
    reasons = []

    def check(code, okay, actual=None, required=None):
        if not okay:
            reasons.append(dict(code=code, actual=actual, required=required))

    check("STRATEGY_DISABLED", config.enabled)
    check("MARKET_OPEN", market.tradable(now), market.status, "active")
    check(
        "ENTRY_WINDOW",
        config.entry_cutoff < remaining <= config.entry_window_start,
        remaining,
        [config.entry_cutoff, config.entry_window_start],
    )
    check("FAVORED_SIDE", side is not None)
    if config.sustained_lead_enabled:
        threshold = config.late_min_lead_sigma if path == "late_settlement" else config.min_lead_sigma
        check("LEAD_MODEL", bool(lead) and lead.get("side") == side)
        check("SETTLEMENT_LEAD", lead.get("lead_sigma", 0) >= threshold, lead.get("lead_sigma"), threshold)
        check(
            "LEAD_CONFIRMATION",
            lead.get("confirmed_late" if path == "late_settlement" else "confirmed_normal", False),
            lead.get("confirmation_samples", 0),
            config.confirmation_count(late),
        )
    for r in quality["reasons"]:
        check(r, False)
    check("MODEL_QUALITY", quality["score"] >= config.min_quality, quality["score"], config.min_quality)
    check("MIN_PRICE", price is not None and price >= config.min_entry_price, price, config.min_entry_price)
    check("MAX_PRICE", price is not None and price <= config.max_entry_price, price, config.max_entry_price)
    check("MIN_PROBABILITY", conservative >= minimum_probability, conservative, minimum_probability)
    check(
        "MIN_EDGE",
        ev is not None and ev >= D(config.min_edge),
        float(ev) if ev is not None else None,
        config.min_edge,
    )
    check(
        "MIN_EV",
        ev is not None and ev >= D(config.min_ev),
        float(ev) if ev is not None else None,
        config.min_ev,
    )
    check("SPREAD", spread is not None and spread <= config.max_spread, spread, config.max_spread)
    check("LIQUIDITY", liquidity >= config.min_liquidity, liquidity, config.min_liquidity)
    check("REGIME", features["regime"] != "EXTREME", features["regime"], "not EXTREME")
    for r in extra_reasons:
        check(r, False)
    bollinger_filter = dict(enabled=config.bollinger_entry_filter_enabled, status="disabled")
    if config.bollinger_entry_filter_enabled:
        bands = features.get("bollinger")
        reference = features.get("reference")
        available = bool(bands and features.get("bollinger_fresh") and reference is not None)
        bound = (bands["upper"] if side == "yes" else bands["lower"]) if available and side else None
        rejected = bound is not None and (
            (side == "yes" and reference > bound) or (side == "no" and reference < bound)
        )
        bollinger_filter.update(
            status="unavailable" if not available else "rejected" if rejected else "allowed",
            reference=reference,
            lower=bands["lower"] if available else None,
            upper=bands["upper"] if available else None,
        )
        check("BOLLINGER_EXTENSION", not rejected, reference, bound)
    return dict(
        decision="NO_TRADE" if reasons else "TRADE_CANDIDATE",
        side=side,
        entry_path=path,
        lead=lead,
        bollinger_entry_filter=bollinger_filter,
        reasons=reasons,
        seconds_remaining=remaining,
        conservative_probability=conservative,
        entry_probability_basis="adjusted" if config.entry_probability_deductions else "raw",
        model_disagreement_penalty=penalty,
        expected_fill_price=price,
        effective_max_entry_price=effective_entry_ceiling(market, conservative, config, late=late),
        estimated_fees=fee,
        expected_slippage=slippage,
        raw_edge=conservative - price if price is not None else None,
        net_ev=float(ev) if ev is not None else None,
        market_probability=ask,
        liquidity=liquidity,
        spread=spread,
        signed_distance=tick.price - market.spec.strike,
        absolute_distance=abs(tick.price - market.spec.strike),
        distance_pct=(tick.price - market.spec.strike) / market.spec.strike,
        approval_status="RESEARCH_ONLY",
    )


class Risk:
    def __init__(self, config):
        self.config = config
        self.reserved = {}
        self.realized = 0.0
        self.daily = {}
        self.halted = False

    def day(self, now):
        key = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        return self.daily.setdefault(key, dict(pnl=0.0, exposure=0.0, trades=0))

    def size(self, price, now):
        return self.size_details(price, now)["quantity"]

    def size_details(self, price, now):
        """Explain the existing whole-contract sizing policy; do not relax any budget."""
        c, d = self.config, self.day(now)
        reasons = []
        for code, failed, message, actual, required in (
            ("KILL_SWITCH", self.halted, "Risk kill switch is latched", self.halted, False),
            (
                "DAILY_LOSS_LIMIT",
                d["pnl"] <= -c.max_daily_loss,
                "Realized daily loss threshold reached",
                d["pnl"],
                -c.max_daily_loss,
            ),
            (
                "DAILY_ATTEMPT_LIMIT",
                c.daily_entry_limits_enabled and d["trades"] >= c.max_daily_trades,
                "Daily submitted-order limit reached (including unfilled attempts)",
                d["trades"],
                c.max_daily_trades,
            ),
        ):
            if failed:
                reasons.append(dict(code=code, message=message, actual=actual, required=required))
        if reasons:
            return dict(quantity=0, reasons=reasons)
        # Convert operands before arithmetic, not after a float division/floor.
        # Preserve every budget cap; no epsilon may fund an unaffordable contract.
        cost = D(price) + D(fee_bound(price, c)) + D(c.slippage)
        bankroll = max(D(0), D(c.bankroll) + D(self.realized))
        reserved = sum((D(value) for value in self.reserved.values()), D(0))
        allocation = bankroll * D(c.bankroll_fraction)
        target = {
            "fixed_contracts": D(c.fixed_contracts) * cost,
            "fixed_dollars": D(c.fixed_dollars),
            "bankroll_percentage": allocation,
        }[c.sizing_mode]
        limits = {
            "SIZING_TARGET": target,
            "MAX_TRADE_DOLLARS": D(c.max_trade_dollars),
            "BANKROLL_ALLOCATION": allocation,
            "OPEN_EXPOSURE_LIMIT": D(c.max_open_exposure) - reserved,
            "DAILY_EXPOSURE_LIMIT": D(c.max_daily_exposure) - D(d["exposure"]),
            "AVAILABLE_BANKROLL": bankroll - reserved,
        }
        if not c.daily_entry_limits_enabled:
            del limits["DAILY_EXPOSURE_LIMIT"]
        budget = min(limits.values())
        quantity = max(0, min(c.max_contracts, int(budget // cost)))
        if quantity == 0:
            for code, available in limits.items():
                if available < cost:
                    reasons.append(
                        dict(
                            code=code,
                            message=code.replace("_", " ").capitalize() + " cannot fund one whole contract",
                            actual=float(available),
                            required=float(cost),
                        )
                    )
        # Keep the existing JSON/API numeric types; only decision arithmetic is Decimal.
        return dict(
            quantity=quantity,
            reasons=reasons,
            cost_per_contract=float(cost),
            budget=float(budget),
            limits={name: float(value) for name, value in limits.items()},
        )

    def reserve(self, key, price, quantity, now):
        if key in self.reserved or quantity <= 0 or quantity > self.size(price, now):
            raise ValueError("Risk reservation rejected")
        amount = D(quantity) * (D(price) + D(fee_bound(price, self.config)) + D(self.config.slippage))
        self.reserved[key] = float(amount)
        day = self.day(now)
        day["exposure"] = float(D(day["exposure"]) + amount)
        day["trades"] += 1

    def close(self, key, pnl, now):
        self.reserved.pop(key, None)
        self.realized = float(D(self.realized) + D(pnl))
        day = self.day(now)
        day["pnl"] = float(D(day["pnl"]) + D(pnl))


def passive_price(market, book, side, conservative, config):
    ask, bid = book.ask(side), book.bid(side)
    if ask is None or bid is None:
        raise ValueError("Incomplete book")
    # Discount bounded by spread, available edge and configured patience. No chasing.
    ask, bid = D(ask), D(bid)
    discount = min(
        D(config.passive_discount),
        (ask - bid) / 2,
        max(D(0), D(conservative) - ask - D(config.min_edge)),
    )
    price = market.snap(ask - discount)
    if D(price) >= ask:
        price = market.snap(ask - D("0.0001"))
    return float(max(bid, D(price)))
