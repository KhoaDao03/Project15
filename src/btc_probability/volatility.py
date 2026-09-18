"""Versioned variance-rate providers; smile-mark IV is not executable IV."""

import math

import numpy as np

from .schema import YEAR


def normalize_option(instrument, book, received):
    """Deribit IV fields are percent; coin quotes are retained, never priced as USD."""
    return dict(
        instrument=instrument["instrument_name"],
        strike=float(instrument["strike"]),
        option_type=instrument["option_type"],
        expiry=instrument["expiration_timestamp"] / 1000,
        quote_currency=instrument["quote_currency"],
        settlement_currency=instrument.get("settlement_currency"),
        instrument_type=instrument.get("instrument_type"),
        settlement_period=instrument.get("settlement_period"),
        forward=book.get("underlying_price"),
        underlying_index=book.get("underlying_index"),
        index_price=book.get("index_price"),
        source_time=book["timestamp"] / 1000,
        received_time=received,
        bid=book.get("best_bid_price"),
        ask=book.get("best_ask_price"),
        bid_size=book.get("best_bid_amount"),
        ask_size=book.get("best_ask_amount"),
        mark=book.get("mark_price"),
        open_interest=book.get("open_interest"),
        volume=book.get("stats", {}).get("volume"),
        mark_iv=None if book.get("mark_iv") is None else float(book["mark_iv"]) / 100,
        bid_iv=None if book.get("bid_iv") is None else float(book["bid_iv"]) / 100,
        ask_iv=None if book.get("ask_iv") is None else float(book["ask_iv"]) / 100,
        iv_unit="annualized_decimal",
        original_iv_unit="percent",
        state=book.get("state"),
    )


def realized(history, now, config):
    results = {}
    for window in sorted({30, 60, 180, 300, config.realized_window}):
        rows = [r for r in history.causal(now) if now - window <= r.source_time <= now]
        if len(rows) < 3 or rows[-1].source_time - rows[0].source_time < window - 2:
            continue
        dt = np.diff([r.source_time for r in rows])
        if max(dt) > config.realized_max_gap or now - rows[-1].source_time > config.reference_max_age:
            continue
        returns = np.diff(np.log([float(r.value) for r in rows]))
        results[str(window)] = math.sqrt(float(np.sum(returns * returns) / np.sum(dt)) * YEAR)
        if window == config.realized_window:
            weights = 0.97 ** np.arange(len(dt) - 1, -1, -1)
            results["ewma"] = math.sqrt(
                float(np.sum(weights * returns * returns) / np.sum(weights * dt)) * YEAR
            )
    return results


def options_sigma(quotes, now, target, spot, remaining, config):
    accepted, rejected = [], []
    for q in quotes:
        reason = None
        required = (
            "strike",
            "forward",
            "mark_iv",
            "bid",
            "ask",
            "bid_size",
            "ask_size",
            "expiry",
            "source_time",
            "received_time",
            "bid_iv",
            "ask_iv",
        )
        if any(q.get(k) is None or not math.isfinite(float(q[k])) for k in required):
            reason = "MISSING_OR_NONFINITE"
        elif q.get("state") != "open":
            reason = "CLOSED"
        elif q["received_time"] > now or q["source_time"] > now + config.clock_skew:
            reason = "NONCAUSAL_OR_CLOCK_SKEW"
        elif (
            now - q["source_time"] > config.option_max_age or now - q["received_time"] > config.option_max_age
        ):
            reason = "STALE"
        elif q["expiry"] <= now:
            reason = "EXPIRED"
        elif min(q[k] for k in ("strike", "forward", "bid", "ask", "mark_iv", "bid_iv", "ask_iv")) <= 0:
            reason = "NONPOSITIVE"
        elif q["ask"] < q["bid"] or q["ask_iv"] < q["bid_iv"]:
            reason = "CROSSED"
        elif not q["bid_iv"] <= q["mark_iv"] <= q["ask_iv"]:
            reason = "MARK_OUTSIDE_IV_QUOTES"
        elif (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) > config.max_option_spread_fraction:
            reason = "WIDE"
        elif min(q["bid_size"], q["ask_size"]) < config.min_option_size:
            reason = "THIN"
        if reason:
            rejected.append(dict(instrument=q.get("instrument"), reason=reason))
        else:
            accepted.append(q)
    groups = {}
    for q in accepted:
        groups.setdefault(q["expiry"], []).append(q)
    eligible = {e: qs for e, qs in groups.items() if len({q["strike"] for q in qs}) >= config.min_strikes}
    if not eligible:
        return dict(
            provider="options-mark-smile-v1",
            sigma=None,
            reasons=["NO_LIQUID_SMILE"],
            rejected=rejected,
            selected=[],
        )
    expiry = min(eligible, key=lambda e: (abs(e - now - config.preferred_expiry_hours * 3600), e))
    qs = eligible[expiry]
    for q in accepted:
        if q["expiry"] != expiry:
            rejected.append(dict(instrument=q["instrument"], reason="UNSELECTED_EXPIRY"))
    # Collapse call/put samples at identical relative moneyness using the median.
    support = {}
    for q in qs:
        support.setdefault(math.log(q["strike"] / q["forward"]), []).append(q["mark_iv"])
    xs = sorted(support)
    ys = [float(np.median(support[x])) for x in xs]
    x = math.log(float(target) / float(spot))  # F_target=S, not source-expiry forward.
    extrapolated = not xs[0] <= x <= xs[-1]
    sigma = float(np.interp(x, xs, ys))
    return dict(
        provider="options-mark-smile-v1",
        sigma=sigma,
        implied_sigma=sigma,
        units="annualized_decimal",
        source_expiry=expiry,
        source_age=max(now - q["source_time"] for q in qs),
        selected=[q["instrument"] for q in qs],
        rejected=rejected,
        target_log_moneyness=x,
        support_log_moneyness=xs,
        support_iv=ys,
        interpolation="linear IV in log(K/F); flat endpoint extrapolation",
        strike_extrapolation=extrapolated,
        horizon_extrapolation=remaining < expiry - now,
        horizon_mapping="constant variance rate: sigma^2 * seconds / 31536000",
        source="exchange mark IV, not executable bid/ask IV",
        basis_usd=float(np.median([q["index_price"] for q in qs if q.get("index_price")])) - float(spot)
        if any(q.get("index_price") for q in qs)
        else None,
        reasons=["STRIKE_EXTRAPOLATION"] if extrapolated else [],
    )


def select(provider, history, options, spec, reference, now, config):
    rv = realized(history, now, config)
    if provider == "fixed":
        result = dict(
            provider="fixed-diagnostic-v1",
            sigma=config.fixed_sigma,
            reasons=["FIXED_SIGMA_DIAGNOSTIC"],
            units="annualized_decimal",
        )
    elif provider == "realized":
        sigma = rv.get(str(config.realized_window))
        result = dict(
            provider="realized-squared-returns-v1",
            sigma=sigma,
            reasons=[] if sigma is not None else ["REALIZED_WARMUP_OR_GAP"],
            units="annualized_decimal",
            window_seconds=config.realized_window,
        )
    else:
        result = options_sigma(
            options, now, spec.target, reference.value, max(0, spec.observation_end_time - now), config
        )
    result["realized_windows"] = rv
    result["realized_sigma"] = rv.get(str(config.realized_window))
    return result
