"""Bleep settlement-average probability and indicator calculations."""

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

SIGMA_MULTIPLIERS = {
    "BTC": 1.35,
    "ETH": 1.10,
    "SOL": 1.0,
    "XRP": 1.0,
    "GOLD": 1.35,
    "SILVER": 1.35,
    "WTI": 1.35,
}


def model_name(asset):
    return "bleep-reference-atr-finish-v5" if asset in SIGMA_MULTIPLIERS else "bleep-settlement-reference-v2"


def capped_confidence(probability, bid, ask, asset):
    """Selected-side confidence; capping one side must not inflate the other."""
    if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
        return None
    if asset not in SIGMA_MULTIPLIERS:
        return probability
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in (bid, ask)):
        return None
    if bid > ask:
        return None
    # Preserve decimal quote boundaries (e.g. exactly 77c mid + 6pp = 83%).
    mid = min(Decimal(".99"), max(Decimal(".01"), (Decimal(str(bid)) + Decimal(str(ask))) / 2))
    premium = Decimal(".06") if asset in ("BTC", "ETH", "GOLD", "SILVER", "WTI") else Decimal(".10")
    cap = float(min(Decimal(".98"), max(Decimal(".02"), mid + premium)))
    return min(probability, cap)


def settlement_distribution(spec, ticks, now, features, atr):
    """Normal approximation to the discrete settlement average, in price units.

    Future prices share increments; their variance cannot be treated as independent.
    ATR/sqrt(60) remains a floor under recent reference-return volatility. This is
    an explicit conservative heuristic, not a calibrated diffusion estimate.
    """
    past = [t for t in ticks if t.source <= now and t.received <= now]
    if not past or any(b.source <= a.source for a, b in zip(past, past[1:])):
        raise ValueError("BLEEP_REFERENCE: missing or unordered causal reference")
    samples = len(spec.sample_times)
    observed = {}
    if spec.reference_source == "Pyth":
        if now >= spec.settlement_end:
            raise ValueError("Await official commodity settlement after close")
    else:
        for t in past:
            slot = math.floor(t.source - spec.settlement_start + 1e-6)
            if 1 <= slot <= 60:
                if slot in observed:
                    raise ValueError("BLEEP_SETTLEMENT: duplicate sample slot")
                observed[slot] = t.price
        known = min(60, max(0, math.floor(now - spec.settlement_start + 1e-6)))
        if set(observed) != set(range(1, known + 1)):
            raise ValueError("BLEEP_SETTLEMENT: missing observed sample")
    known = len(observed)
    estimates = [features.get(k) for k in ("rv_30", "rv_60", "ewma")]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in estimates):
        raise ValueError("BLEEP_VOLATILITY: missing valid recent reference estimates")
    spot = past[-1].price
    reference_rate = spot * max(estimates)
    atr_rate = atr / math.sqrt(60)
    rate = max(atr_rate, reference_rate)
    future = spec.sample_times[known:]
    previous = past[-1].source
    variance_time = 0.0
    for i, target in enumerate(future):
        dt = target - previous
        if dt < 0:
            raise ValueError("BLEEP_SETTLEMENT: reference ahead of sample grid")
        variance_time += dt * ((len(future) - i) / samples) ** 2
        previous = target
    mean = (sum(observed.values()) + len(future) * spot) / samples
    # Apply the contract's rounded-price comparison, including strict inequalities.
    unit = Decimal(1).scaleb(-spec.round_digits)
    strict_up = spec.comparison_operator in (">", "<=")
    strike_units = Decimal(str(spec.strike)) / unit
    boundary = (
        (strike_units.to_integral_value(rounding=ROUND_FLOOR) + Decimal(".5"))
        if strict_up
        else (strike_units.to_integral_value(rounding=ROUND_CEILING) - Decimal(".5"))
    )
    return dict(
        model="bleep-settlement-reference-v2",
        known_samples=known,
        observed_sum=sum(observed.values()),
        remaining_samples=len(future),
        settlement_mean=mean,
        settlement_std=rate * math.sqrt(variance_time),
        effective_boundary=float(boundary * unit),
        variance_time=variance_time,
        atr_volatility_per_sqrt_second=atr_rate,
        reference_volatility_per_sqrt_second=reference_rate,
        volatility_per_sqrt_second=rate,
        volatility_source="reference" if reference_rate > atr_rate else "atr",
    )


def indicator_inputs(candles):
    """Bleep's fixed 14/14/3 Wilder Stoch RSI, Wilder ATR(14), and BB(20,2).

    Candles are (UTC minute, open, high, low, close), with an optional causal
    in-progress final candle. Require full history instead of synthetic defaults.
    """
    if len(candles) < 33:
        return None
    closes = [c[4] for c in candles]
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in changes[:14]) / 14
    loss = sum(max(-d, 0) for d in changes[:14]) / 14
    rsi = [100 if loss == 0 else 100 - 100 / (1 + gain / loss)]
    for d in changes[14:]:
        gain = (gain * 13 + max(d, 0)) / 14
        loss = (loss * 13 + max(-d, 0)) / 14
        rsi.append(100 if loss == 0 else 100 - 100 / (1 + gain / loss))
    raw = []
    for i in range(13, len(rsi)):
        window = rsi[i - 13 : i + 1]
        low, high = min(window), max(window)
        raw.append(50 if high == low else (rsi[i] - low) / (high - low) * 100)
    k = sum(raw[-3:]) / 3
    previous_k = sum(raw[-4:-1]) / 3
    tr = [max(c[2] - c[3], abs(c[2] - prev[4]), abs(c[3] - prev[4])) for prev, c in zip(candles, candles[1:])]
    atr = sum(tr[:14]) / 14
    for value in tr[14:]:
        atr = (atr * 13 + value) / 14
    middle = sum(closes[-20:]) / 20
    width = 2 * math.sqrt(sum((p - middle) ** 2 for p in closes[-20:]) / 20)
    return dict(
        atr=atr,
        stoch_k=k,
        stoch_k_previous=previous_k,
        bb_middle=middle,
        bb_upper=middle + width,
        bb_lower=middle - width,
        candles=len(candles),
        last_candle_minute=candles[-1][0],
    )


def indicator_lean(spot, inputs):
    k, previous = inputs["stoch_k"], inputs["stoch_k_previous"]
    rising, falling = k > previous, k < previous
    momentum = 0
    if 45 <= k <= 80 and rising:
        momentum = 0.55
    elif 20 <= k <= 55 and falling:
        momentum = -0.55
    elif k > 55 and rising:
        momentum = 0.35
    elif k < 45 and falling:
        momentum = -0.35
    upper, lower = inputs["bb_upper"], inputs["bb_lower"]
    position = (spot - inputs["bb_middle"]) / max((upper - lower) / 2, 1e-9)
    outside_up, outside_down = spot > upper, spot < lower
    reversal = 0
    if k < 25 and rising:
        reversal = 0.85
    elif k > 75 and falling:
        reversal = -0.85
    elif position <= -0.75 and not outside_down and (rising or k < 35):
        reversal = 0.7
    elif position >= 0.75 and not outside_up and (falling or k > 65):
        reversal = -0.7
    elif outside_down and rising:
        reversal = 0.55
    elif outside_up and falling:
        reversal = -0.55
    elif position <= -0.75 and k <= 40:
        reversal = 0.45
    elif position >= 0.75 and k >= 60:
        reversal = -0.45
    if outside_up and rising:
        momentum, reversal = max(momentum, 0.7), 0
    elif outside_down and falling:
        momentum, reversal = min(momentum, -0.7), 0
    if abs(momentum) >= abs(reversal) + 0.1:
        lean = momentum
    elif abs(reversal) > abs(momentum) + 0.1:
        lean = reversal
    elif abs(momentum) < 0.2 and abs(reversal) < 0.2:
        lean = 0
    elif abs(reversal) >= 0.45 and abs(reversal) >= abs(momentum):
        lean = reversal
    else:
        lean = 0.5 * momentum + 0.5 * reversal
    return min(1, max(-1, lean))


def safety_clamp(p_up, safety, limit=0.75):
    return (
        min(p_up, limit) if p_up >= 0.5 and safety < 0.5 else max(p_up, 1 - limit) if safety < 0.5 else p_up
    )


def mode_b_probability(spec, spot, seconds_left, inputs, *, clamp=False, distribution, asset=None):
    """Return contract YES, not confidence in whichever side happens to lead."""
    if inputs is None:
        raise ValueError("BLEEP_WARMUP: need 33 contiguous causal one-minute candles")
    if not all(math.isfinite(v) for v in inputs.values()) or not math.isfinite(spot) or spot <= 0:
        raise ValueError("Invalid Bleep inputs")
    atr_finish = asset in SIGMA_MULTIPLIERS
    atr = max(inputs["atr"], spot * 0.00015, 1e-8 if atr_finish else 10**-spec.round_digits)
    center, boundary = distribution["settlement_mean"], distribution["effective_boundary"]
    sigma = distribution["settlement_std"]
    if atr_finish:
        center, boundary = spot, spec.strike
        sigma = atr * math.sqrt(max(seconds_left, 1) / 60) * SIGMA_MULTIPLIERS[asset]
    z = (center - boundary) / max(sigma, 1e-9)
    # Same Abramowitz–Stegun approximation as Bleep's normalCdf.
    t = 1 / (1 + 0.2316419 * abs(z))
    tail = (
        math.exp(-0.5 * z * z)
        / math.sqrt(2 * math.pi)
        * t
        * (0.319381539 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    )
    base_up = 1 - tail if z >= 0 else tail
    complete = distribution["remaining_samples"] == 0
    if complete:
        final_yes = float(spec.yes(distribution["settlement_mean"]))
        base_up = final_yes if spec.comparison_operator in (">=", ">") else 1 - final_yes
    lean = indicator_lean(spot, inputs)
    weight = min(0.15, max(0, seconds_left) / 900)
    if complete:
        weight = 0
    indicator_up = min(0.95, max(0.05, 0.5 + 0.12 * lean))
    up = min(0.98, max(0.02, (1 - weight) * base_up + weight * indicator_up))
    if complete:
        up = base_up
    pre_clamp_up = up
    safety = abs(center - boundary) / max(sigma, 1e-9)
    if clamp and not complete:
        up = safety_clamp(up, safety, 0.78 if atr_finish else 0.75)
    yes = up if spec.comparison_operator in (">=", ">") else 1 - up
    return dict(
        p_yes=yes,
        p_no=1 - yes,
        p_up=up,
        base_p_up=base_up,
        lean=lean,
        indicator_weight=weight,
        atr=atr,
        sigma_t=sigma,
        safety_ratio=safety,
        safety_clamp_enabled=clamp,
        safety_clamp_applied=up != pre_clamp_up,
        pre_clamp_p_up=pre_clamp_up,
        **{k: v for k, v in inputs.items() if k != "atr"},
        **{k: v for k, v in distribution.items() if k != "model"},
        model=model_name(asset),
        sigma_multiplier=SIGMA_MULTIPLIERS.get(asset, 1.0),
    )


def probability(spec, ticks, now, features, config):
    """Use official-reference ATR finish estimates; retain settlement evidence separately."""
    inputs = features.get("bleep")
    if inputs is None:
        raise ValueError("BLEEP_WARMUP: need 33 contiguous causal one-minute candles")
    atr = max(inputs["atr"], features["reference"] * 0.00015, 10**-spec.round_digits)
    distribution = settlement_distribution(spec, ticks, now, features, atr)
    reference_atr = features.get("reference_rolling_atr")
    if config.asset in SIGMA_MULTIPLIERS:
        if reference_atr is not None and (
            type(reference_atr) not in (int, float) or not math.isfinite(reference_atr) or reference_atr < 0
        ):
            raise ValueError("BLEEP_ATR: invalid official-reference ATR")
        # Match Bleepblorp: insufficient reference candles use the relative floor,
        # never exchange-seeded ATR. Keep the other seeded indicators intact.
        inputs = {**inputs, "atr": reference_atr if reference_atr is not None else 0.0}
    result = mode_b_probability(
        spec,
        features["reference"],
        spec.settlement_end - now,
        inputs,
        clamp=config.bleep_safety_clamp_enabled,
        distribution=distribution,
        asset=config.asset,
    )
    remaining = distribution["remaining_samples"]
    return {
        **result,
        "atr_source": ("official_reference_rolling" if reference_atr is not None else "reference_price_floor")
        if config.asset in SIGMA_MULTIPLIERS
        else "indicator_wilder",
        "reference_rolling_atr": reference_atr,
        "conservative_yes": result["p_yes"],
        "conservative_no": result["p_no"],
        "required_remaining_average": (len(spec.sample_times) * spec.strike - distribution["observed_sum"])
        / remaining
        if remaining
        else None,
        "calibrated": False,
    }
