"""Pure Black digital diagnostic and correlated arithmetic-average simulation."""

import math
from decimal import ROUND_HALF_DOWN, ROUND_HALF_UP, Decimal

import numpy as np

from .schema import YEAR, decimal


def terminal(forward, strike, seconds, sigma, *, comparison=">=", discount=1):
    f, k, seconds, sigma = map(float, (forward, strike, seconds, sigma))
    if not all(math.isfinite(x) for x in (f, k, seconds, sigma, discount)):
        raise ValueError("Nonfinite input")
    if f <= 0 or k <= 0 or seconds < 0 or sigma < 0 or not 0 < discount <= 1:
        raise ValueError("Invalid digital input")
    if comparison not in (">", ">=", "<", "<="):
        raise ValueError("Unsupported comparison")
    variance = sigma * sigma * seconds / YEAR
    d2 = None
    if variance == 0:
        a, b = decimal(forward), decimal(strike)
        p = float({">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[comparison])
    else:
        d2 = (math.log(f / k) - variance / 2) / math.sqrt(variance)
        p = 0.5 * math.erfc((-d2 if comparison in (">", ">=") else d2) / math.sqrt(2))
    return dict(
        p_yes=p,
        p_no=1 - p,
        value_yes=discount * p,
        d2=d2,
        integrated_variance=variance,
        assumption="F=S; D configured; terminal diagnostic only",
    )


def wilson(successes, n):
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def simulate(spec, reference, known, now, sigma, *, paths=20000, seed=15, requested_half_width=0.01, mu=0):
    if not spec.verified or not spec.sample_times:
        raise ValueError("UNVERIFIED_RULES")
    return _simulate(
        spec,
        reference,
        known,
        now,
        sigma,
        classify=lambda values: [spec.yes(v) for v in values],
        paths=paths,
        seed=seed,
        requested_half_width=requested_half_width,
        mu=mu,
    )


def round_with_lean(value, p_yes):
    """User research rule: nearest cent; only exact half ties depend on pre-rounding lean."""
    if not 0 <= p_yes <= 1 or p_yes == 0.5:
        raise ValueError("NO_DIRECTIONAL_LEAN")
    return decimal(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP if p_yes > 0.5 else ROUND_HALF_DOWN)


def research_settlement(spec, reference, known, now, sigma, **kwargs):
    """Separate assumption-based estimate; never modifies contract verification.

    Freeze lean from the same unrounded average paths before applying any rounding.
    This avoids feeding the rounded probability back into its own tie decision.
    """
    if (
        spec.research_tie_policy != "user-model-lean-v1"
        or set(spec.unresolved) != {"ROUNDING_TIE_UNSPECIFIED"}
        or not spec.sample_times
        or spec.target is None
        or spec.comparison != ">="
        or spec.decimal_places != 2
    ):
        raise ValueError("UNSUPPORTED_RESEARCH_RULES")
    info = dict(
        label="ASSUMPTION-BASED SETTLEMENT RESEARCH; USER TIE RULE; UNCALIBRATED",
        policy=spec.research_tie_policy,
        verified_settlement=False,
    )

    def classify(values):
        values = [decimal(v) for v in values]
        target = decimal(spec.target)
        lean = sum(v >= target for v in values) / len(values)
        info.update(
            pre_rounding_p_yes=lean,
            lean="YES" if lean > 0.5 else "NO" if lean < 0.5 else "NEUTRAL",
            tie_rounding="UP" if lean > 0.5 else "DOWN" if lean < 0.5 else None,
        )
        return [round_with_lean(v, lean) >= target for v in values]

    try:
        info["result"] = _simulate(spec, reference, known, now, sigma, classify=classify, **kwargs)
        info["available"] = True
    except ValueError as exc:
        if str(exc) != "NO_DIRECTIONAL_LEAN":
            raise
        info.update(available=False, reason="NO_DIRECTIONAL_LEAN", result=None)
    return info


def _simulate(
    spec, reference, known, now, sigma, *, classify, paths=20000, seed=15, requested_half_width=0.01, mu=0
):
    if not math.isfinite(sigma) or sigma < 0 or not math.isfinite(mu) or paths < 1:
        raise ValueError("INVALID_MODEL_INPUT")
    elapsed = {t for t in spec.sample_times if t <= now}
    if set(known) != elapsed:
        raise ValueError("MISSING_OR_NONCAUSAL_SETTLEMENT_SAMPLES")
    total = sum((decimal(v) for v in known.values()), Decimal(0))
    future = [t for t in spec.sample_times if t > now]
    if not future:
        mean = total / len(spec.sample_times)
        p = float(classify([mean])[0])
        return dict(
            p_yes=p,
            p_no=1 - p,
            mean=float(mean),
            std=0.0,
            numerical_interval=[p, p],
            paths=0,
            seed=seed,
            method="exact-decimal",
            precision_met=True,
        )
    if reference.source_time > now or reference.received_time > now:
        raise ValueError("NONCAUSAL_REFERENCE")
    if sigma == 0:
        values = [
            decimal(reference.value) * (decimal(mu) * decimal(t - reference.source_time) / YEAR).exp()
            for t in future
        ]
        mean = (total + sum(values, Decimal(0))) / len(spec.sample_times)
        p = float(classify([mean])[0])
        return dict(
            p_yes=p,
            p_no=1 - p,
            mean=float(mean),
            std=0.0,
            numerical_interval=[p, p],
            paths=0,
            seed=seed,
            method="zero-variance-model",
            precision_met=True,
        )
    price = np.full(paths, float(reference.value))
    sums = np.full(paths, float(total))
    previous = reference.source_time
    rng = np.random.default_rng(seed)
    for timestamp in future:
        dt = (timestamp - previous) / YEAR
        if dt < 0:
            raise ValueError("REFERENCE_AFTER_SAMPLE")
        price *= np.exp((mu - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * rng.standard_normal(paths))
        sums += price
        previous = timestamp
    averages = sums / len(spec.sample_times)
    if not np.isfinite(averages).all():
        raise ValueError("NUMERICAL_OVERFLOW")
    # Decimal conversion preserves the specified threshold and rounding policy.
    # Float simulation uncertainty is distinct from exact official settlement.
    successes = sum(classify([str(v) for v in averages]))
    p = successes / paths
    interval = wilson(successes, paths)
    return dict(
        p_yes=p,
        p_no=1 - p,
        mean=float(averages.mean()),
        std=float(averages.std()),
        numerical_interval=interval,
        paths=paths,
        seed=seed,
        method="iid-gbm-exact-steps",
        requested_half_width=requested_half_width,
        precision_met=max(p - interval[0], interval[1] - p) <= requested_half_width,
    )
