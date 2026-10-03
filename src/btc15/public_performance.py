"""Daily public performance derived only from sanitized, recorded trade history."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = "America/New_York"


def trade_day(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value, ZoneInfo(TIMEZONE)).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def empty_stats():
    return dict(realized_pnl=0.0, completed_trades=0, wins=0, losses=0,
                breakeven_trades=0, unknown_pnl=0, win_rate=None, opened_trades=0, open_positions=0)


def record_close(stats, row):
    stats["completed_trades"] += 1
    pnl = row.get("net_pnl")
    if type(pnl) not in (int, float) or not math.isfinite(pnl):
        stats["unknown_pnl"] += 1
    else:
        stats["realized_pnl"] += pnl
        stats["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven_trades"] += 1


def finish(stats):
    if stats["unknown_pnl"]:
        stats["realized_pnl"] = None
    else:
        stats["realized_pnl"] = round(stats["realized_pnl"], 8)
        if stats["completed_trades"]:
            stats["win_rate"] = stats["wins"] / stats["completed_trades"]
    return stats


def build_performance(histories, assets, now):
    today = trade_day(now)
    daily = {}
    all_time = {}
    excluded = 0
    timestamps = []
    missing = []
    stale = False
    for asset in assets:
        data = histories.get(asset)
        if data is None:
            missing.append(asset)
            continue
        timestamps.append(data["updated_at"])
        stale = stale or bool(data.get("stale")) or not 0 <= now - data["updated_at"] <= 120
        total = empty_stats()
        all_time[asset] = total
        for row in data["rows"]:
            opened = trade_day(row.get("opened"))
            if opened and "2000-01-01" <= opened <= today:
                total["opened_trades"] += 1
                daily.setdefault(opened, {}).setdefault(asset, empty_stats())["opened_trades"] += 1
            if row.get("status") == "OPEN":
                total["open_positions"] += 1
                continue
            if row.get("status") != "CLOSED":
                continue
            record_close(total, row)
            closed = trade_day(row.get("exit_timestamp"))
            if not closed or not "2000-01-01" <= closed <= today:
                excluded += 1
                continue
            record_close(daily.setdefault(closed, {}).setdefault(asset, empty_stats()), row)
    # Explicit zero-trade days make adjacent-day comparisons meaningful.
    first = min(daily, default=today)
    day = datetime.fromisoformat(first).date()
    end = datetime.fromisoformat(today).date()
    days = []
    while day <= end:
        key = day.isoformat()
        stats = daily.get(key, {})
        days.append(dict(date=key, assets={asset: finish(stats.get(asset, empty_stats())) for asset in all_time}))
        day += timedelta(days=1)
    return dict(timezone=TIMEZONE, today=today, updated_at=min(timestamps, default=now),
                generated_at=now, stale=stale or bool(missing), missing_assets=missing,
                excluded_undated_trades=excluded, all_time={a: finish(s) for a, s in all_time.items()}, days=days)
