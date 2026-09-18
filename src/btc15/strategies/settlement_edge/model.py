"""Causal reference features, quality checks and Bleep settlement-lead evidence."""

import math
from dataclasses import dataclass

import numpy as np

from .bleep import indicator_inputs


@dataclass(frozen=True)
class Tick:
    source: float
    received: float
    price: float

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (self.source, self.received, self.price)) or self.price <= 0:
            raise ValueError("Invalid reference tick")


def features(ticks, now, config):
    ticks = [t for t in ticks if t.source <= now and t.received <= now]
    if len(ticks) < 2:
        raise ValueError("Insufficient reference history")
    ts, prices = np.array([t.source for t in ticks]), np.array([t.price for t in ticks])
    dt = np.diff(ts)
    if np.any(dt <= 0):
        raise ValueError("Duplicate or reversed reference timestamps")
    returns = np.diff(np.log(prices))
    out = {
        "reference": float(prices[-1]),
        "history_seconds": float(ts[-1] - ts[0]),
        "max_gap": float(max(dt[ts[1:] >= now - config.warmup_seconds], default=0)),
        "returns_count": len(returns),
    }
    sigmas = []
    for window in (5, 15, 30, 60, 180, 300):
        mask = ts >= now - window
        ps, times = prices[mask], ts[mask]
        out[f"momentum_{window}"] = float(ps[-1] / ps[0] - 1) if len(ps) > 1 else None
        if len(ps) > 1:
            sigma = math.sqrt(float(np.sum(np.diff(np.log(ps)) ** 2)) / (times[-1] - times[0]))
            out[f"rv_{window}"] = sigma
            if window >= 30:
                sigmas.append(sigma)
        else:
            out[f"rv_{window}"] = None
    weights = (1 - config.ewma_decay) * config.ewma_decay ** np.arange(len(returns) - 1, -1, -1)
    ewma = math.sqrt(float(np.average(returns**2 / dt, weights=weights)))
    out["ewma"] = ewma
    out["sigma"] = max([config.volatility_floor, ewma] + sigmas)
    out["volatility_disagreement"] = float(np.std(sigmas + [ewma]) / max(out["sigma"], 1e-12))
    recent = returns[ts[1:] >= now - 60]
    out["recent_jump"] = float(max(abs(recent))) if len(recent) else 0
    out["shock"] = out["recent_jump"] >= config.shock_threshold
    out["regime"] = (
        "EXTREME"
        if out["shock"] or out["sigma"] >= config.extreme_sigma
        else "ELEVATED"
        if out["sigma"] >= config.extreme_sigma / 2
        else "LOW"
        if out["sigma"] <= config.volatility_floor * 2
        else "NORMAL"
    )
    out["return_acceleration"] = (out["momentum_5"] or 0) / 5 - (out["momentum_30"] or 0) / 30
    signs = np.sign(returns)
    count = 0
    for sign in signs[::-1]:
        if sign != signs[-1]:
            break
        count += 1
    out["consecutive_direction"] = int(count * signs[-1])
    recent_prices = prices[ts >= now - 300]
    out["maximum_adverse_move"] = float(min(recent_prices) / recent_prices[0] - 1)
    out["maximum_favorable_move"] = float(max(recent_prices) / recent_prices[0] - 1)
    # Closed UTC-minute candles only. Incomplete/gappy candles are not invented.
    candles = []
    for minute in sorted(set((ts // 60).astype(int))):
        if (minute + 1) * 60 > now:
            continue
        idx = np.flatnonzero((ts >= minute * 60) & (ts < (minute + 1) * 60))
        if len(idx) < 58 or max(np.diff(ts[idx]), default=0) > 2:
            continue
        p = prices[idx]
        candles.append((minute, float(p[0]), float(max(p)), float(min(p)), float(p[-1])))
    # Indicators require contiguous candles, not stitched gaps.
    contiguous = []
    for candle in candles:
        if contiguous and candle[0] != contiguous[-1][0] + 1:
            contiguous = []
        contiguous.append(candle)
    closes = np.array([c[4] for c in contiguous])
    out.update(atr=None, bollinger=None, stochastic_rsi=None, candle_direction=None, max_minute_shock=None)
    if len(closes):
        out["candle_direction"] = int(np.sign(contiguous[-1][4] - contiguous[-1][1]))
        out["max_minute_shock"] = max(c[2] - c[3] for c in contiguous[-5:])
    if len(closes) >= config.bollinger_period:
        c = closes[-config.bollinger_period :]
        middle, width = float(c.mean()), float(c.std()) * config.bollinger_std
        out["bollinger"] = dict(
            middle=middle,
            upper=middle + width,
            lower=middle - width,
            position=(prices[-1] - (middle - width)) / (2 * width) if width else 0.5,
            distance_outside=max(0, prices[-1] - middle - width, middle - width - prices[-1]),
        )
    # An older contiguous sequence may survive a missing latest closed minute.
    # Freshness belongs to this causal model snapshot, including when it is cached.
    out["bollinger_fresh"] = bool(out["bollinger"] is not None and contiguous[-1][0] == int(now // 60) - 1)
    if len(closes) > config.atr_period:
        tr = [
            max(c[2] - c[3], abs(c[2] - prev[4]), abs(c[3] - prev[4]))
            for prev, c in zip(contiguous, contiguous[1:])
        ]
        out["atr"] = float(np.mean(tr[-config.atr_period :]))
    if len(closes) >= config.rsi_period + config.stochastic_period:
        changes = np.diff(closes)
        rsi = []
        for i in range(config.rsi_period, len(changes) + 1):
            r = changes[i - config.rsi_period : i]
            gain, loss = float(np.maximum(r, 0).mean()), float(np.maximum(-r, 0).mean())
            rsi.append(100 * gain / (gain + loss) if gain + loss else 50)
        r = rsi[-config.stochastic_period :]
        out["stochastic_rsi"] = (r[-1] - min(r)) / (max(r) - min(r)) if max(r) > min(r) else 0.5
    # Retain reference complete-candle checks; Bleep also uses the live candle.
    bleep_candles = list(contiguous)
    minute = int(now // 60)
    current = np.flatnonzero(ts // 60 == minute)
    if len(current):
        if not bleep_candles or bleep_candles[-1][0] != minute - 1:
            bleep_candles = []
        p = prices[current]
        bleep_candles.append((minute, float(p[0]), float(max(p)), float(min(p)), float(p[-1])))
    elif not bleep_candles or bleep_candles[-1][0] != minute - 1:
        bleep_candles = []
    # Bleepblorp atrRolling: last 15 official-reference candles (including
    # the causal live minute), giving exactly 14 true ranges. Never seed this.
    window = bleep_candles[-15:]
    out["reference_rolling_atr"] = (
        sum(max(c[2] - c[3], abs(c[2] - prev[4]), abs(c[3] - prev[4])) for prev, c in zip(window, window[1:]))
        / 14
        if len(window) == 15
        else None
    )
    out["bleep"] = indicator_inputs(bleep_candles)
    return out


def lead_evidence(spec, ticks, now, f, p, config):
    """Confirm the selected side using Bleep's settlement mean and uncertainty."""
    late = config.late_entry_enabled and spec.settlement_end - now <= config.no_new_entry
    side = spec.favored(p["settlement_mean"] if late else ticks[-1].price)
    direction = 1 if (side == "yes") == (spec.comparison_operator in (">=", ">")) else -1
    margin = direction * (p["settlement_mean"] - spec.strike)
    # One settlement unit avoids infinite ratios without imposing BTC precision on XRP.
    lead_sigma = margin / max(10**-spec.round_digits, p["settlement_std"])
    return dict(
        side=side,
        source=ticks[-1].source,
        lead_sigma=lead_sigma,
        known_samples=p["known_samples"],
        required_remaining_average=p["required_remaining_average"],
    )


def quality(features_, ticks, book, now, config):
    reasons = []
    if (
        not ticks
        or not 0 <= now - ticks[-1].received <= config.reference_max_age
        or not -config.max_clock_skew <= now - ticks[-1].source <= config.reference_max_age
    ):
        reasons.append("STALE_REFERENCE")
    if (
        not book.valid
        or not 0 <= now - book.received <= config.book_max_age
        or not -config.max_clock_skew <= now - book.source_time <= config.book_max_age
    ):
        reasons.append("STALE_BOOK")
    if features_["history_seconds"] < config.warmup_seconds:
        reasons.append("WARMUP")
    if features_["max_gap"] > 2:
        reasons.append("REFERENCE_GAP")
    if features_["shock"]:
        reasons.append("SHOCK")
    score = max(0, 100 - 25 * len(reasons) - 10 * features_["volatility_disagreement"])
    return dict(score=score, reasons=reasons, calibrated=False)
