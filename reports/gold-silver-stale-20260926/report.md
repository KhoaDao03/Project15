# Gold and silver stale-market investigation

Checked September 26, 2026 at approximately 01:56 UTC.

Both collectors are connected and processing fresh Pyth reference ticks. Observed reference ages were 0.51–0.68 seconds, processing lag 2–3 milliseconds, and queue depth zero. Their current blocking reason is `NO_ACTIVE_MARKET`, rather than stale reference data or processing backlog.

Kalshi's public market API returned zero open contracts for both `KXGOLD15M` and `KXSILVER15M`. The most recent settled contract in each series closed September 25 at 21:00 UTC (Friday 5 PM EDT). Both series have 24 upcoming contracts; the earliest opens September 27 at 22:00 UTC (Sunday 6 PM EDT), closing at 22:15 UTC. This confirms the current gap in listed trading sessions.

The dashboard wording is misleading: `CollectorRecovery.check` treats the lack of an active tradable contract as a recovery blocker, and `operational_state.py` renders that condition as `RECOVERING` / “Rebuilding and checking fresh market data.” The saved recovery trigger still contains an earlier reference/book stream-stall reason, while the current `reasons` list contains only `NO_ACTIVE_MARKET`. The private dashboard also uses generic unavailable/stale wording when there are no current quotes.

The collectors continue refreshing discovery approximately every 15 seconds. Fresh metadata, a validated active market, reference data and sequenced book snapshots are still required before readiness resumes. No restart, code change, configuration change, or live-policy change was performed for this investigation. A clearer presentation would distinguish “No active market — waiting for next session” from an actual stale-data fault; a scheduled reopening time should come from current exchange metadata, not a hardcoded weekend rule.

[Captured market and collector evidence](market-status.json).
