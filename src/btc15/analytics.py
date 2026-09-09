"""Calibration is measured separately from execution P&L."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import fsum

import numpy as np

from .models import history_models, select_history

BUCKETS = [0, 0.5, 0.8, 0.85, 0.9, 0.92, 0.95, 0.97, 0.99, 1.0000001]


def calibration(predictions):
    if not predictions:
        return dict(n=0, brier=None, log_loss=None, ece=None, buckets=[])
    p = np.array([p for p, y in predictions], dtype=float)
    y = np.array([y for p, y in predictions], dtype=float)
    buckets = []
    ece = 0.0
    for low, high in zip(BUCKETS, BUCKETS[1:]):
        mask = (p >= low) & (p < high)
        n = int(sum(mask))
        if n:
            predicted, actual = float(p[mask].mean()), float(y[mask].mean())
            ece += n / len(p) * abs(predicted - actual)
            buckets.append(dict(low=low, high=min(1, high), n=n, predicted=predicted, actual=actual))
    clipped = np.clip(p, 1e-12, 1 - 1e-12)
    return dict(
        n=len(p),
        brier=float(np.mean((p - y) ** 2)),
        log_loss=float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))),
        ece=ece,
        buckets=buckets,
    )


def daily_block_interval(observations, minimum_days=20, samples=2000):
    """Resample whole UTC days, retaining dependence between intraday markets."""
    blocks = defaultdict(list)
    for when, value in observations:
        day = datetime.fromtimestamp(when, timezone.utc).date().isoformat()
        blocks[day].append(value)
    result = dict(
        method="UTC-day cluster bootstrap",
        days=len(blocks),
        samples=samples,
        minimum_days=minimum_days,
        confidence=0.95,
        interval=None,
        status="INSUFFICIENT_INDEPENDENT_DAYS",
    )
    if len(blocks) < minimum_days:
        return result
    values = [blocks[k] for k in sorted(blocks)]
    sums = np.array([sum(v) for v in values])
    counts = np.array([len(v) for v in values])
    rng = np.random.default_rng(15)
    means = []
    for _ in range(samples):
        index = rng.integers(0, len(values), len(values))
        means.append(float(sums[index].sum() / counts[index].sum()))
    result.update(status="ESTIMATED", interval=list(map(float, np.quantile(means, [0.025, 0.975]))))
    return result


def metrics(store, mode="PAPER", run_id=None, *, scope="settlement"):
    models = history_models(store, mode)

    def read(kind):
        return select_history(store.list(kind, run_id, mode, limit=None), models, scope)

    ops = read("opportunity")
    identities = {
        (
            r["body"].get("model", {}).get("model_id", "settlement-edge"),
            r["body"].get("model", {}).get("model_version", "v1"),
            r["body"].get("versions", {}).get("config"),
        )
        for r in ops
    }
    if run_id is None and len(identities) > 1:
        result = metrics(store, mode, "__no_pooled_model_results__", scope=scope)
        result.update(
            run_id=None,
            mixed_models=True,
            model_comparison=[],
            trades=None,
            wins=None,
            losses=None,
            net_pnl=None,
            gross_pnl=None,
            fees=None,
            max_drawdown=None,
        )
        result["limitations"].insert(
            0,
            "Multiple configurations or archived strategies: select a run; pooled results are suppressed",
        )
        return result
    results = read("trade_result")
    settlements = {(r["run_id"], r["market"]): r["body"]["result"] for r in read("settlement")}
    pairs = []
    grouped = defaultdict(list)
    # One last eligible entry-window prediction per market for primary calibration.
    # Repeated intramarket predictions are correlated and are reported separately.
    last = {}
    all_predictions = []
    for r in ops:
        b = r["body"]
        key = r["run_id"], r["market"]
        if key not in settlements or "p_yes" not in b["probability"]:
            continue
        pair = (b["probability"]["p_yes"], int(settlements[key] == "yes"))
        all_predictions.append(pair)
        if b["config"]["no_new_entry"] < b["seconds_remaining"] <= b["config"]["entry_window_start"]:
            last[key] = (r, pair)
    for r, pair in last.values():
        pairs.append(pair)
        b = r["body"]
        for name, value in [
            ("regime", b["features"].get("regime")),
            ("model", b["versions"]["probability"]),
            ("entry_minute", int(b["seconds_remaining"] // 60)),
            ("side", b.get("side")),
        ]:
            grouped[f"{name}:{value}"].append(pair)
    pnl = [r["body"]["net_pnl"] for r in results]
    cumulative = np.cumsum([0] + pnl)
    drawdown = np.maximum.accumulate(cumulative) - cumulative
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    reasons = Counter(r["body"]["reason"] for r in results)
    orders = read("order")
    submitted = [r for r in orders if r["body"].get("status") == "submitted"]
    fills = read("fill")
    entry_fills = [r for r in fills if r["body"]["action"] == "buy"]
    filled_ids = {r["opportunity_id"] for r in entry_fills}
    partial_ids = {r["opportunity_id"] for r in entry_fills if r["body"].get("remaining", 0) > 0}
    rejections = Counter(reason["code"] for r in ops for reason in r["body"]["reasons"])
    by_group = defaultdict(list)
    op_map = {r["id"]: r["body"] for r in ops}
    for r in results:
        b = op_map.get(r["opportunity_id"], {})
        for name, value in [
            ("side", r["body"]["side"]),
            ("regime", b.get("features", {}).get("regime")),
            ("model", b.get("versions", {}).get("probability")),
            ("entry_minute", int(b.get("seconds_remaining", 0) // 60)),
            ("price_bucket", round(b.get("expected_fill_price", 0), 2)),
            ("edge_bucket", round(b.get("net_ev", 0), 2)),
        ]:
            by_group[f"{name}:{value}"].append(r["body"]["net_pnl"])
    streaks = {1: 0, -1: 0}
    previous, current = 0, 0
    for p in pnl:
        sign = 1 if p > 0 else -1 if p < 0 else 0
        current = current + 1 if sign == previous else 1
        if sign:
            streaks[sign] = max(streaks[sign], current)
        previous = sign
    return dict(
        mode=mode,
        run_id=run_id,
        history_scope=scope,
        dataset_audits=[r["body"].get("dataset_audit") for r in read("experiment")],
        failed_experiments=[dict(run_id=r["run_id"], **r["body"]) for r in read("experiment_failed")],
        opportunities=len(ops),
        trades=len(results),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(pnl) if pnl else None,
        net_pnl=sum(pnl),
        gross_pnl=sum(r["body"]["gross_pnl"] for r in results),
        fees=sum(r["body"]["fees"] for r in results),
        realized_pnl_per_trade=float(np.mean(pnl)) if pnl else None,
        expected_ev_per_candidate=float(
            np.mean([r["body"]["net_ev"] for r in ops if r["body"]["decision"] == "TRADE_CANDIDATE"])
        )
        if any(r["body"]["decision"] == "TRADE_CANDIDATE" for r in ops)
        else None,
        profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
        average_win=float(np.mean(wins)) if wins else None,
        average_loss=float(np.mean(losses)) if losses else None,
        max_drawdown=float(max(drawdown)),
        sharpe=None,
        sharpe_note="Not reported: short, irregular trade samples lack defensible annualization",
        longest_win_streak=streaks[1],
        longest_loss_streak=streaks[-1],
        fill_rate=len(filled_ids) / len(submitted) if submitted else None,
        partial_fill_rate=len(partial_ids) / len(submitted) if submitted else None,
        exit_reasons=dict(reasons),
        rejections=dict(rejections),
        calibration=calibration(pairs),
        uncertainty=dict(
            brier=daily_block_interval(
                [(r["timestamp"], (pair[0] - pair[1]) ** 2) for r, pair in last.values()]
            ),
            pnl_per_trade=daily_block_interval([(r["timestamp"], r["body"]["net_pnl"]) for r in results]),
        ),
        all_prediction_calibration=calibration(all_predictions),
        calibration_groups={k: calibration(v) for k, v in grouped.items()},
        pnl_groups={k: dict(n=len(v), net_pnl=sum(v)) for k, v in by_group.items()},
        cumulative_pnl=list(map(float, cumulative)),
        drawdown=list(map(float, drawdown)),
        limitations=[
            "Uncalibrated model",
            "Synthetic/demo runs are not evidence of edge",
            "Day-block intervals assume dependence between days is negligible; require at least 20 days",
        ],
    )


def lifetime_performance(results, fills):
    """Realized trade-close metrics and remaining filled inventory at entry cost."""
    ordered = sorted(results, key=lambda row: (row["timestamp"], row["id"]))
    pnls = [row["body"]["net_pnl"] for row in ordered]
    wins = sum(p > 0 for p in pnls)
    losses = sum(p < 0 for p in pnls)
    profit = fsum(p for p in pnls if p > 0)
    loss = -fsum(p for p in pnls if p < 0)
    equity = peak = drawdown = 0.0
    streak = longest_win = longest_loss = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        if pnl > 0:
            streak = streak + 1 if streak > 0 else 1
        elif pnl < 0:
            streak = streak - 1 if streak < 0 else -1
        else:
            streak = 0  # A break-even trade ends either streak.
        longest_win = max(longest_win, streak)
        longest_loss = max(longest_loss, -streak)

    def trade_key(row):
        return row["run_id"], row["market"], row["opportunity_id"]

    closed = {trade_key(row) for row in results}
    inventory = {}
    for row in sorted(fills, key=lambda row: (row["timestamp"], row["id"])):
        key = trade_key(row)
        if key in closed:
            continue
        body = row["body"]
        quantity, cost = inventory.get(key, (0.0, 0.0))
        if body["action"] == "buy":
            quantity += body["quantity"]
            cost += body["quantity"] * body["price"] + body["fee"]
        elif body["action"] == "sell" and quantity > 0:
            remaining = max(0.0, quantity - body["quantity"])
            cost *= remaining / quantity
            quantity = remaining
        inventory[key] = quantity, cost
    return dict(
        net_pnl=fsum(pnls),
        completed_trades=len(pnls),
        wins=wins,
        losses=losses,
        breakeven_trades=len(pnls) - wins - losses,
        win_rate=wins / len(pnls) if pnls else None,
        average_pnl=fsum(pnls) / len(pnls) if pnls else None,
        profit_factor=profit / loss if loss else None,
        max_drawdown=drawdown,
        current_streak=streak,
        longest_win_streak=longest_win,
        longest_loss_streak=longest_loss,
        open_exposure=fsum(cost for quantity, cost in inventory.values() if quantity > 0),
        open_trades=sum(quantity > 0 for quantity, _ in inventory.values()),
    )
