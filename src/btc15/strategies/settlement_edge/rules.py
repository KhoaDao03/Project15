import math
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


def evaluate(market, book, tick, features, probability, quality, now, config, extra_reasons=()):
    remaining = market.close_time - now
    side = market.spec.favored(tick.price)
    ask = book.ask(side) if side else None
    bid = book.bid(side) if side else None
    conservative = probability["conservative_" + side] if side else 0
    penalty = min(0.05, features["volatility_disagreement"] * 0.01)
    conservative = max(0, conservative - penalty)
    spread = ask - bid if ask is not None and bid is not None else None
    liquidity = book.asks(side)[0][1] if side and book.asks(side) else 0
    fee = fee_bound(ask, config) if ask is not None else None
    ev = conservative - ask - fee - config.slippage if ask is not None else None
    reasons = []

    def check(code, okay, actual=None, required=None):
        if not okay:
            reasons.append(dict(code=code, actual=actual, required=required))

    check("STRATEGY_DISABLED", config.enabled)
    check("MARKET_OPEN", market.tradable(now), market.status, "active")
    check(
        "ENTRY_WINDOW",
        config.no_new_entry < remaining <= config.entry_window_start,
        remaining,
        [config.no_new_entry, config.entry_window_start],
    )
    check("FAVORED_SIDE", side is not None)
    for r in quality["reasons"]:
        check(r, False)
    check("MODEL_QUALITY", quality["score"] >= config.min_quality, quality["score"], config.min_quality)
    check("MIN_PRICE", ask is not None and ask >= config.min_entry_price, ask, config.min_entry_price)
    check("MAX_PRICE", ask is not None and ask <= config.max_entry_price, ask, config.max_entry_price)
    check("MIN_PROBABILITY", conservative >= config.min_probability, conservative, config.min_probability)
    check("MIN_EDGE", ev is not None and ev >= config.min_edge, ev, config.min_edge)
    check("MIN_EV", ev is not None and ev >= config.min_ev, ev, config.min_ev)
    check("SPREAD", spread is not None and spread <= config.max_spread, spread, config.max_spread)
    check("LIQUIDITY", liquidity >= config.min_liquidity, liquidity, config.min_liquidity)
    check("REGIME", features["regime"] != "EXTREME", features["regime"], "not EXTREME")
    for r in extra_reasons:
        check(r, False)
    return dict(
        decision="NO_TRADE" if reasons else "TRADE_CANDIDATE",
        side=side,
        reasons=reasons,
        seconds_remaining=remaining,
        conservative_probability=conservative,
        model_disagreement_penalty=penalty,
        expected_fill_price=ask,
        estimated_fees=fee,
        expected_slippage=config.slippage,
        raw_edge=conservative - ask if ask is not None else None,
        net_ev=ev,
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
        c, d = self.config, self.day(now)
        if self.halted or d["pnl"] <= -c.max_daily_loss or d["trades"] >= c.max_daily_trades:
            return 0
        cost = price + fee_bound(price, c) + c.slippage
        bankroll = max(0, c.bankroll + self.realized)
        target = {
            "fixed_contracts": c.fixed_contracts * cost,
            "fixed_dollars": c.fixed_dollars,
            "bankroll_percentage": bankroll * c.bankroll_fraction,
        }[c.sizing_mode]
        budget = min(
            target,
            c.max_trade_dollars,
            bankroll * c.bankroll_fraction,
            c.max_open_exposure - sum(self.reserved.values()),
            c.max_daily_exposure - d["exposure"],
            bankroll - sum(self.reserved.values()),
        )
        return max(0, min(c.max_contracts, math.floor(budget / cost)))

    def reserve(self, key, price, quantity, now):
        if key in self.reserved or quantity <= 0 or quantity > self.size(price, now):
            raise ValueError("Risk reservation rejected")
        amount = quantity * (price + fee_bound(price, self.config) + self.config.slippage)
        self.reserved[key] = amount
        day = self.day(now)
        day["exposure"] += amount
        day["trades"] += 1

    def close(self, key, pnl, now):
        self.reserved.pop(key, None)
        self.realized += pnl
        self.day(now)["pnl"] += pnl


def passive_price(market, book, side, conservative, config):
    ask, bid = book.ask(side), book.bid(side)
    if ask is None or bid is None:
        raise ValueError("Incomplete book")
    # Discount bounded by spread, available edge and configured patience. No chasing.
    discount = min(config.passive_discount, (ask - bid) / 2, max(0, conservative - ask - config.min_edge))
    price = market.snap(ask - discount)
    if price >= ask:
        price = market.snap(ask - 0.0001)
    return max(bid, price)
