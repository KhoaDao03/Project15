"""Contract/day chronological splits and matched-horizon probability scores."""

import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

from .recording import metadata, read_events, replay


def scores(rows):
    if not rows:
        return dict(n=0, brier=None, log_loss=None, reliability=[])
    p = np.array([r["p"] for r in rows])
    y = np.array([r["y"] for r in rows])
    bounded = np.clip(p, 1e-15, 1 - 1e-15)
    reliability = []
    for bucket in range(10):
        mask = (p >= bucket / 10) & ((p < (bucket + 1) / 10) if bucket < 9 else (p <= 1))
        if mask.any():
            reliability.append(
                dict(
                    bin=[bucket / 10, (bucket + 1) / 10],
                    n=int(mask.sum()),
                    mean_probability=float(p[mask].mean()),
                    observed=float(y[mask].mean()),
                )
            )
    days = defaultdict(list)
    for row in rows:
        days[row["day"]].append((row["p"] - row["y"]) ** 2)
    interval = None
    if len(days) >= 2:
        values = list(days.values())
        rng = np.random.default_rng(15)
        draws = [
            np.mean([v for i in rng.integers(0, len(values), len(values)) for v in values[i]])
            for _ in range(1000)
        ]
        interval = np.quantile(draws, [0.025, 0.975]).tolist()
    return dict(
        n=len(rows),
        unique_contracts=len({r["market"] for r in rows}),
        unique_days=len(days),
        brier=float(np.mean((p - y) ** 2)),
        log_loss=float(-np.mean(y * np.log(bounded) + (1 - y) * np.log(1 - bounded))),
        log_loss_clip=1e-15,
        reliability=reliability,
        day_cluster_bootstrap_brier_interval=interval,
    )


def evaluate(path, config):
    outcomes, contracts = {}, {}
    for event in read_events(path):
        if event["kind"] == "outcome":
            d = event["data"]
            outcomes[d["market_ticker"]] = int(d["yes"])
        elif event["kind"] == "contract":
            d = event["data"]["normalized"]
            contracts[d["market_ticker"]] = d
    forecasts = list(replay(path, replace(config, provider="options")))
    rv = {(f.market_ticker, f.forecast_time): f for f in replay(path, replace(config, provider="realized"))}
    horizons = (600, 300, 120, 60, 30, 10)
    selected = {}
    for f in forecasts:
        if f.market_ticker not in outcomes:
            continue
        # Select latest forecast at or before each horizon, within 5 seconds only.
        for horizon in horizons:
            distance = f.remaining_seconds - horizon
            key = (f.market_ticker, horizon)
            if 0 <= distance <= 5 and (key not in selected or distance < selected[key][0]):
                selected[key] = (distance, f)
    days = sorted(
        {
            datetime.fromtimestamp(s["observation_end_time"], timezone.utc).date().isoformat()
            for s in contracts.values()
        }
    )
    # With fewer than three days, fall back to whole-contract chronological groups, clearly labeled.
    by_day = len(days) >= 3
    groups = days if by_day else sorted(contracts, key=lambda t: contracts[t]["observation_end_time"])

    def fold(group):
        idx = groups.index(group)
        return (
            "train"
            if idx < math.floor(0.6 * len(groups))
            else ("validation" if idx < math.floor(0.8 * len(groups)) else "test")
        )

    per_model = defaultdict(list)
    matched = defaultdict(list)
    for (ticker, horizon), (_, f) in selected.items():
        day = (
            datetime.fromtimestamp(contracts[ticker]["observation_end_time"], timezone.utc).date().isoformat()
        )
        realized = rv.get((ticker, f.forecast_time))
        values = dict(
            midpoint=float(f.quotes["midpoint"]) if f.quotes.get("midpoint") is not None else None,
            options_terminal=f.terminal["p_yes"] if f.terminal else None,
            options_settlement=f.raw_p_yes,
            realized_settlement=realized.raw_p_yes if realized else None,
        )
        sigma = f.volatility.get("sigma")
        distance = abs(float(f.reference["value"]) - float(f.target)) if f.reference and f.target else None
        common = dict(
            market=ticker,
            day=day,
            horizon=horizon,
            y=outcomes[ticker],
            split=fold(day if by_day else ticker),
            distance="unknown"
            if distance is None
            else ("<50" if distance < 50 else "50-200" if distance < 200 else ">=200"),
            volatility="unknown"
            if sigma is None
            else ("<30%" if sigma < 0.3 else "30-60%" if sigma < 0.6 else ">=60%"),
        )
        for model, p in values.items():
            if p is not None:
                row = dict(common, p=p)
                per_model[model].append(row)
                if all(v is not None for v in values.values()):
                    matched[model].append(row)
    report = dict(
        mode=metadata(path)["mode"],
        unique_contracts=len(outcomes),
        unique_days=len(days),
        split_unit="day" if by_day else "whole contract (too few days)",
        train_validation_test="chronological 60/20/20; no fitted parameters or calibrator",
        expected_contract_horizons=len(outcomes) * len(horizons),
        selected_horizons=len(selected),
        models={},
    )
    for model in ("midpoint", "realized_settlement", "options_terminal", "options_settlement"):
        rows = per_model[model]
        report["models"][model] = dict(
            available=scores(rows),
            matched=scores(matched[model]),
            coverage=len(rows) / (len(outcomes) * len(horizons)) if outcomes else 0,
            by_split={
                s: scores([r for r in rows if r["split"] == s]) for s in ("train", "validation", "test")
            },
            by_horizon={h: scores([r for r in rows if r["horizon"] == h]) for h in horizons},
            by_distance={
                g: scores([r for r in rows if r["distance"] == g])
                for g in ("<50", "50-200", ">=200", "unknown")
            },
            by_volatility={
                g: scores([r for r in rows if r["volatility"] == g])
                for g in ("<30%", "30-60%", ">=60%", "unknown")
            },
        )
    report["outcome_reconciliation"] = [
        dict(
            market=ticker,
            official_yes=outcome,
            model_settlement_yes=final.raw_p_yes,
            agrees=final.raw_p_yes == outcome,
        )
        for ticker, outcome in outcomes.items()
        for final in [
            next(
                (
                    f
                    for f in reversed(forecasts)
                    if f.market_ticker == ticker and f.probability_kind == "deterministic"
                ),
                None,
            )
        ]
        if final is not None
    ]
    report["limitations"] = [
        "Numerical probabilities are uncalibrated.",
        "Ticks within one contract are dependent; intervals resample whole days and require at least two days.",
        "No profitability or market-lead claim. Synthetic results measure pipeline behavior only.",
        "Provider comparisons are ablations, not trained improvements; no optional extensions enabled.",
    ]
    return report
