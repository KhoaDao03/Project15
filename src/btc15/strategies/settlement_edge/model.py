"""Causal reference features and a transparent settlement-average Monte Carlo."""

import math
from dataclasses import dataclass

import numpy as np


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
    out["bollinger_fresh"] = bool(
        out["bollinger"] is not None and contiguous[-1][0] == int(now // 60) - 1
    )
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
    return out


def probability(spec, ticks, now, sigma, config, *, price_shift=0):
    if not math.isfinite(sigma) or sigma < 0:
        raise ValueError("Invalid sigma")
    past = [t for t in ticks if t.source <= now and t.received <= now]
    if not past:
        raise ValueError("No causal reference")
    latest = past[-1]
    # Source timestamps may have subsecond offsets; one observation per indexed second.
    observed = {}
    for t in past:
        slot = math.floor(t.source - spec.settlement_start + 1e-6)
        if 1 <= slot <= 60:
            if slot in observed:
                raise ValueError("Duplicate settlement sample slot")
            observed[slot] = t.price
    expected_known = min(60, max(0, math.floor(now - spec.settlement_start + 1e-6)))
    if set(observed) != set(range(1, expected_known + 1)):
        raise ValueError("Missing past settlement observations")
    rng = np.random.default_rng(config.seed)
    if not math.isfinite(price_shift) or latest.price + price_shift <= 0:
        raise ValueError("Invalid stress price")
    paths = np.full(config.paths, latest.price + price_shift, dtype=float)
    sums = np.full(config.paths, sum(observed.values()), dtype=float)
    previous = latest.source
    for i, target in enumerate(spec.sample_times, 1):
        if i in observed:
            continue
        dt = target - previous
        if dt < 0:
            raise ValueError("Reference ahead of future sample grid")
        # Zero LOG drift is an explicit assumption, not fitted directional alpha.
        paths *= np.exp(sigma * math.sqrt(dt) * rng.standard_normal(config.paths))
        sums += paths
        previous = target
    averages = sums / 60
    # Decimal rounding only affects exact ties; NumPy handles bulk, ties bracketed below.
    rounded = np.round(averages, 2)
    op = {">=": np.greater_equal, ">": np.greater, "<": np.less, "<=": np.less_equal}[
        spec.comparison_operator
    ]
    yes = op(rounded, spec.strike)
    half = np.isclose(averages * 100 - np.floor(averages * 100), 0.5, atol=1e-8, rtol=0)
    ambiguity = 0
    for idx in np.flatnonzero(half):
        a, b = spec.yes(float(averages[idx]), "half_even"), spec.yes(float(averages[idx]), "half_up")
        if spec.rounding == "ambiguous_half_tie":
            yes[idx] = a
            ambiguity += a != b
        else:
            yes[idx] = spec.yes(float(averages[idx]))
    p = float(np.mean(yes))
    # Wilson radius remains nonzero even when every simulation has one outcome.
    z, n = 1.96, config.paths
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    simulation_allowance = max(p - (center - radius), (center + radius) - p)
    penalty = min(1, simulation_allowance + config.calibration_penalty + ambiguity / n)
    return dict(
        p_yes=p,
        p_no=1 - p,
        simulation_uncertainty=simulation_allowance,
        rounding_uncertainty=ambiguity / n,
        uncertainty=penalty,
        conservative_yes=max(0, p - penalty),
        conservative_no=max(0, 1 - p - penalty),
        known_samples=len(observed),
        settlement_mean=float(np.mean(averages)),
        settlement_std=float(np.std(averages)),
        required_remaining_average=(60 * spec.strike - sum(observed.values())) / (60 - len(observed))
        if len(observed) < 60
        else None,
        paths=config.paths,
        seed=config.seed,
        calibrated=False,
        model="settlement-mc-logwalk-v1",
    )


def lead_evidence(spec, ticks, now, f, p, config):
    """Entry-only stress; known settlement samples are never altered."""
    late = config.late_entry_enabled and spec.settlement_end - now <= config.no_new_entry
    side = spec.favored(p["settlement_mean"] if late else ticks[-1].price)
    direction = 1 if (side == "yes") == (spec.comparison_operator in (">=", ">")) else -1
    recent = [t for t in ticks if now - 61 <= t.source <= now and t.received <= now]
    adverse = max(
        (
            max(0, -direction * (b.price - a.price))
            for a, b in zip(recent, recent[1:])
            if 0 < b.source - a.source <= 1.5
        ),
        default=0,
    )
    stressed = probability(spec, ticks, now, f["sigma"], config, price_shift=-direction * adverse)
    margin = direction * (p["settlement_mean"] - spec.strike)
    # A cent floor avoids infinite diagnostic ratios at deterministic settlement.
    lead_sigma = margin / max(0.01, p["settlement_std"])
    return dict(
        side=side,
        source=ticks[-1].source,
        lead_sigma=lead_sigma,
        adverse_move=adverse,
        stressed_probability=stressed.get("conservative_" + side, 0) if side else 0,
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
