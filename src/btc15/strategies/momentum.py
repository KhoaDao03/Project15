"""Independent momentum configurations and deterministic paper decision rules."""

import hashlib
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass

from ..domain import D, dumps
from .settlement_edge.config import Strategy
from .settlement_edge.rules import fee_bound

REGIMES = ("LOW", "NORMAL", "HIGH", "EXTREME")


@dataclass(frozen=True)
class Momentum(Strategy):
    model_id: str = "conservative-confirmed-momentum"
    model_name: str = "Conservative Confirmed Momentum"
    model_version: str = "v1"
    mode: str = "paper"
    min_entry_price: float = 0.52
    max_entry_price: float = 0.80
    max_spread: float = 0.03
    no_new_entry: int = 180
    entry_window_start: int = 600
    min_move: float = 0.0015
    confirmation_minutes: int = 3
    min_edge: float = 0.05
    min_ev: float = 0.05
    proxy_min_edge: float = 0.15
    max_candle_age: float = 60
    fixed_contracts: int = 1
    max_contracts: int = 1
    min_liquidity: float = 1
    passive: bool = False
    slippage: float = 0
    evaluation_interval: float = 60
    # Hold research entries to official settlement; do not inherit control exits.
    hold_to_settlement: bool = True
    volatility_boundaries: tuple = (20, 75, 90)
    allowed_volatility_regimes: tuple = ("NORMAL",)
    volatility_history_minutes: int = 59
    volatility_min_samples: int = 30

    def __post_init__(self):
        super().__post_init__()
        names = {
            "conservative-confirmed-momentum": "Conservative Confirmed Momentum",
            "volatility-regime-momentum": "Volatility-Regime Momentum",
        }
        if not all(
            isinstance(v, str) for v in (self.model_id, self.model_name, self.model_version, self.mode)
        ):
            raise ValueError("Model identity, version and mode must be strings")
        if self.model_id not in names or self.model_name != names[self.model_id]:
            raise ValueError("Unknown model identity/name")
        if not self.model_version or not all(c.isalnum() or c in "-_." for c in self.model_version):
            raise ValueError("Invalid model_version")
        if self.mode != "paper":
            raise ValueError("Momentum models are paper-only (offline BACKTEST is allowed)")
        if not self.enabled:
            raise ValueError("Use model activation state separately from immutable configuration")
        if self.min_entry_price >= self.max_entry_price or self.bankroll <= 0:
            raise ValueError("Entry minimum must be below maximum; bankroll must be positive")
        if not 1 <= self.confirmation_minutes <= 5:
            raise ValueError("confirmation_minutes must be between 1 and 5")
        if self.max_contracts != 1 or self.fixed_contracts != 1:
            raise ValueError("Momentum research is limited to one contract")
        if not isinstance(self.volatility_boundaries, (tuple, list)) or not isinstance(
            self.allowed_volatility_regimes, (tuple, list)
        ):
            raise ValueError("Volatility boundaries and allowed regimes require arrays")
        boundaries = tuple(self.volatility_boundaries)
        if len(boundaries) != 3 or any(type(v) not in (int, float) for v in boundaries):
            raise ValueError("Three numeric volatility boundaries required")
        if not 0 < boundaries[0] < boundaries[1] < boundaries[2] < 100:
            raise ValueError("Volatility boundaries must be strictly ordered inside 0–100")
        allowed = tuple(self.allowed_volatility_regimes)
        if any(not isinstance(v, str) for v in allowed):
            raise ValueError("Volatility regime names must be strings")
        if not allowed or len(set(allowed)) != len(allowed) or any(v not in REGIMES for v in allowed):
            raise ValueError("Invalid allowed volatility regimes")
        if not 2 <= self.volatility_min_samples <= self.volatility_history_minutes <= 59:
            raise ValueError("Require 2 <= minimum samples <= history minutes <= 59")
        object.__setattr__(self, "volatility_boundaries", boundaries)
        object.__setattr__(self, "allowed_volatility_regimes", allowed)

    @property
    def version(self):
        return hashlib.sha256(dumps(asdict(self)).encode()).hexdigest()


def volatility_model(**overrides):
    return Momentum(
        **{
            "model_id": "volatility-regime-momentum",
            "model_name": "Volatility-Regime Momentum",
            **overrides,
        }
    )


def regime(percentile, boundaries=(20, 75, 90)):
    if not math.isfinite(percentile) or not 0 <= percentile <= 100:
        raise ValueError("Percentile outside 0–100")
    return REGIMES[sum(percentile >= b for b in boundaries)]


def observations(ticks, now, config):
    """Only ticks received by now; complete UTC minutes, no gap stitching."""
    ticks = [t for t in ticks if t.source <= now and t.received <= now]
    end = math.floor(now / 60) * 60
    minutes = {}
    for t in ticks:
        minute = math.floor(t.source / 60) * 60
        if end - (config.volatility_history_minutes + 1) * 60 <= minute < end:
            minutes.setdefault(minute, []).append(t)
    measured = {}
    for minute, samples in minutes.items():
        if len(samples) < 58 or samples[0].source - minute > 2 or minute + 60 - samples[-1].source > 2:
            continue
        intervals = [b.source - a.source for a, b in zip(samples, samples[1:])]
        if not intervals or min(intervals) <= 0 or max(intervals) > 2:
            continue
        squared = sum(math.log(b.price / a.price) ** 2 for a, b in zip(samples, samples[1:]))
        measured[minute] = math.sqrt(squared * 60 / sum(intervals))
    current = measured.get(end - 60)
    history = [
        v for m, v in measured.items() if end - (config.volatility_history_minutes + 1) * 60 <= m < end - 60
    ]
    percentile = None
    if current is not None and len(history) >= config.volatility_min_samples:
        percentile = (
            100
            * (sum(v < current for v in history) + 0.5 * sum(v == current for v in history))
            / len(history)
        )
    out = dict(
        minute_volatility=current,
        volatility_percentile=percentile,
        volatility_regime=regime(percentile, config.volatility_boundaries)
        if percentile is not None
        else None,
        volatility_samples=len(history),
        volatility_history_minutes=config.volatility_history_minutes,
        volatility_window_seconds=60,
        volatility_sample_end=end,
        candle_age=now - end if current is not None else None,
        reference_source="CF Benchmarks BRTI",
        reference_timestamp=ticks[-1].source if ticks else None,
        reference_received=ticks[-1].received if ticks else None,
    )
    for seconds in {60, 180, 300, config.confirmation_minutes * 60}:
        prior = [t for t in ticks if t.source <= now - seconds]
        segment = [t for t in ticks if prior and t.source >= prior[-1].source]
        valid = bool(prior and ticks and now - seconds - prior[-1].source <= 2)
        valid = valid and all(0 < b.source - a.source <= 2 for a, b in zip(segment, segment[1:]))
        out[f"return_{seconds}"] = float(D(ticks[-1].price) / D(prior[-1].price) - 1) if valid else None
    return out


class ObservationCache:
    """Reuse unchanged candle/history results between reference and window boundaries.

    Engine ticks are append-only between replacements; correction/reversal handling
    remains in Engine. Event-time candle age is always refreshed on each lookup.
    """

    def __init__(self):
        self.ticks = None
        self.length = -1
        self.key = None

    def get(self, ticks, now, config, calculate=observations):
        if self.ticks is not ticks or self.length != len(ticks):
            self.ticks, self.length = ticks, len(ticks)
            self.sources = [t.source for t in ticks]
            self.receipts = [t.received for t in ticks]
            self.ordered = all(a <= b for a, b in zip(self.receipts, self.receipts[1:]))
            self.key = None
        if not self.ordered:
            return calculate(ticks, now, config)
        count = min(bisect_right(self.sources, now), bisect_right(self.receipts, now))
        windows = []
        for seconds in sorted({60, 180, 300, config.confirmation_minutes * 60}):
            index = bisect_right(self.sources, now - seconds, 0, count)
            windows.append((index, bool(index and now - seconds - self.sources[index - 1] <= 2)))
        key = (count, math.floor(now / 60), tuple(windows), config)
        if key != self.key:
            self.value = calculate(ticks, now, config)
            self.key = key
        result = dict(self.value)
        if result.get("minute_volatility") is not None:
            result["candle_age"] = now - result["volatility_sample_end"]
        return result


def evaluate(market, book, tick, features, probability, quality, now, config, extra_reasons=()):
    c = config
    move = float(D(tick.price) / D(market.spec.strike) - 1)
    side = market.spec.favored(tick.price)
    ask, bid = (book.ask(side), book.bid(side)) if side else (None, None)
    spread = float(D(ask) - D(bid)) if ask is not None and bid is not None else None
    opposite = (book.no if side == "yes" else book.yes) if side else {}
    liquidity = float(opposite[max(opposite)]) if opposite else 0
    conservative = probability.get("conservative_" + str(side), 0)
    fee = fee_bound(ask, c) if ask is not None else None
    ev = float(D(conservative) - D(ask) - D(fee) - D(c.slippage)) if ask is not None else None
    confirmation = features.get(f"return_{c.confirmation_minutes * 60}")
    remaining = market.close_time - now
    reasons = []

    def check(code, okay, actual=None, required=None):
        if not okay:
            reasons.append(dict(code=code, actual=actual, required=required))

    check("FUTURE_REFERENCE", tick.source <= now and tick.received <= now)
    check("MARKET_OPEN", market.tradable(now))
    check("TOO_EARLY", remaining <= c.entry_window_start, remaining, c.entry_window_start)
    check("TOO_LATE", remaining >= c.no_new_entry, remaining, c.no_new_entry)
    check("INSUFFICIENT_MOVE", abs(move) >= c.min_move and side is not None, move, c.min_move)
    check(
        "CONFIRMATION_FAILED",
        confirmation is not None and confirmation * move > 0,
        confirmation,
        "same direction as reference move",
    )
    check("PRICE_TOO_LOW", ask is not None and ask >= c.min_entry_price, ask, c.min_entry_price)
    check("PRICE_TOO_HIGH", ask is not None and ask <= c.max_entry_price, ask, c.max_entry_price)
    check("SPREAD_TOO_WIDE", spread is not None and spread <= c.max_spread, spread, c.max_spread)
    age = features.get("candle_age")
    check("STALE_CANDLE", age is not None and 0 <= age <= c.max_candle_age, age, c.max_candle_age)
    check("INSUFFICIENT_EDGE", ev is not None and ev >= c.min_edge, ev, c.min_edge)
    if features.get("reference_source") != "CF Benchmarks BRTI":
        check("PROXY_EDGE_FAILED", ev is not None and ev >= c.proxy_min_edge, ev, c.proxy_min_edge)
    check("INSUFFICIENT_LIQUIDITY", liquidity >= c.min_liquidity, liquidity, c.min_liquidity)
    if c.model_id == "volatility-regime-momentum":
        current = features.get("volatility_regime")
        check(
            "VOLATILITY_HISTORY_INSUFFICIENT",
            current is not None,
            features.get("volatility_samples"),
            c.volatility_min_samples,
        )
        if current is not None:
            check(
                "VOLATILITY_" + current,
                current in c.allowed_volatility_regimes,
                current,
                c.allowed_volatility_regimes,
            )
    # Feed/clock/book safety remains shared. Shock remains a safety gate in both models.
    for code in quality["reasons"]:
        check(code, False)
    for code in extra_reasons:
        check(code, False)
    return dict(
        decision="NO_TRADE" if reasons else "TRADE_CANDIDATE",
        side=side,
        reasons=reasons,
        seconds_remaining=remaining,
        conservative_probability=conservative,
        estimated_probability=probability.get("p_" + str(side)),
        expected_fill_price=ask,
        bid_price=bid,
        ask_price=ask,
        spread=spread,
        liquidity=liquidity,
        estimated_fees=fee,
        expected_slippage=c.slippage,
        net_ev=ev,
        expected_profit=ev,
        raw_edge=conservative - ask if ask is not None else None,
        market_probability=ask,
        window_return=move,
        reference_price=market.spec.strike,
        spot_price=tick.price,
        signed_distance=tick.price - market.spec.strike,
        approval_status="RESEARCH_ONLY",
    )
