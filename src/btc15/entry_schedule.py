"""Operator-requested Eastern-time blackout windows for automatic live crypto entries."""

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def entry_blackout(now: float) -> str | None:
    """Return the active window; starts are inclusive and ends exclusive."""
    local = datetime.fromtimestamp(now, EASTERN)
    weekday = local.weekday()
    minute = local.hour * 60 + local.minute
    windows = [(705, 780, "Weekdays 11:45–13:00 ET")] if weekday < 5 else [
        (675, 750, "Weekends 11:15–12:30 ET")
    ]
    if weekday == 1:
        windows.append((1200, 1275, "Tuesday 20:00–21:15 ET"))
    elif weekday == 3:
        windows.append((1185, 1260, "Thursday 19:45–21:00 ET"))
    return next((label for start, end, label in windows if start <= minute < end), None)
