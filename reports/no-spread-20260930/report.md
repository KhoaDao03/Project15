# Spread filter removal — 2026-09-30

Disabled spread filtering globally in signal generation, paper submission and live entry. Zero spreads now pass book validation; crossed, missing and invalid quotes remain rejected. The bid/ask-based confidence cap remains unchanged. The legacy max_spread configuration field is retained for saved-config compatibility and stable identities but is not enforced; its dashboard label now says so. Decision/status metadata reports spread_filter_enabled=false.

511 distinct relevant checks passed, including zero and wide spreads for all ten assets on both sides, independent paper/live entry checks and crossed-book rejection. One new snapshot test initially used the wrong NO quote convention; corrected the fixture and reran all 41 tests in that file successfully. Scoped Ruff, JavaScript syntax and diff checks passed.

Restarted all ten existing collectors, execution and private dashboard during a safe early market window. Kept existing databases and run identities. All services are active, trading policies are unchanged, and the executor and seven crypto evaluations report spread filtering disabled. Gold, silver and WTI are connected with fresh feeds but their model evaluations await 33 contiguous one-minute candles; their restarted collectors use the same updated source. No orders or trading control requests were submitted by deployment/verification.

Backups and scripts: data/deployments/no-spread-20260930/.
