# Ten-minute early entry deployment — 2026-09-28

Deployed and verified all seven crypto markets with entry_window_start=600. For 420 < remaining <= 600 seconds, minimum capped confidence is BTC 85%, ETH 90%, SOL 88%, XRP 85%, DOGE 83%, BNB 86%, HYPE 84%. At exactly 420 seconds, all return to 83%; existing final-second cutoff and other filters remain.

The source presets and probability helper already contained the requested ten-minute values when inspected. Updated two stale regression expectations, then 621 focused tests passed. Scoped Ruff and diff checks passed.

Migrated frozen runtime configs into new collector runs, changing only entry_window_start and early_min_probability. Copied reference history and historical settlements with provenance before starting execution. Old and new per-market realized histories matched exactly before startup. Historical runs remain intact.

Production checks confirm new config/run identities, thresholds, fresh feeds, ten assets in both dashboards, and active services. Contract quantities and enable states are preserved. ATR multipliers are unchanged, including XRP 1.10. The global loss guard remains untriggered and its realized P&L agrees with the dashboard (+150.4627 at verification).

Backups, scripts and prior configurations: data/deployments/ten-minute-20260928/.
