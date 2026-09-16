# Partial-exit processing optimization

The September 13 four-asset audit found a SOL take-profit that filled 3.98 of 10 contracts, then waited for more liquidity. The first eligible replenishment event arrived at 01:44:50.614 EDT but was processed 1.136 seconds later. Between the two fill-source events, 168 book updates showed no additional executable depth above the amount already consumed.

After a partial persistent exit, `_apply_monitor` now returns before the rollback snapshot and SQL transaction when:

- The reason remains HARD_STOP, TAKE_PROFIT, or INVALIDATION.
- No warning or new price extremum needs recording, and no entry order needs canceling.
- No valid price level offers at least 0.01 contracts after consumed-depth accounting and the take-profit price floor.

New executable quantity, changed/cleared triggers, price extrema, and active-order cancellations retain the existing transactional path. Reference and book freshness, sequencing, risk checks, and committed hard-stop behavior are unchanged. PROFIT_VALUE and STANDARD_CASHOUT IOCs are excluded because their no-fill/remainder expiry is a required action.

Skipped empty checks no longer advance `last_exit_event` or create empty POSITION_OPEN → EXITING → POSITION_OPEN transitions. They consumed no quantity. Events which actually execute still record their identity and consumed depth, preserving duplicate-fill protection. Continuous observed depletion and later replenishment still use the existing liquidity accounting path.

## Measurement

The isolated executor benchmark uses 169 book states reconstructed from the recorded SOL source tape, a fixed partially filled position, consumed-depth state, and deterministic processing clock. The starting account history and fee seed are controlled benchmark inputs, not an exact historical portfolio replay. It excludes receipt, recorder, probability computation, and full engine scheduling; it does not predict a counterfactual live fill time or profit.

- Before: 169 checkpoints, 2.035 seconds unprofiled.
- After: 1 checkpoint, 0.169 seconds unprofiled.
- Both variants produced identical final portfolio snapshots and execution records.
- Baseline profile spent approximately 1.14 seconds in SQLite commits and 0.45 seconds in deepcopy out of 2.60 profiled seconds. This supports avoiding empty checkpoint writes rather than increasing queue capacity.

Evidence and runnable benchmark: `data/runtime/partial-exit-fix/benchmark.py`, `books.json`, `execution-before.py`, `baseline-profile.txt`, and `comparison.json`.

## Validation and deployment

177 tests passed: 10 focused partial-exit tests plus 167 existing pending-exit, displayed-depth, replenishment, fractional-fill, profit-value, standard-cashout, execution, and collector recovery tests. Coverage includes YES/NO sides, persistent exit reasons, minimum quantity, new extrema, replenishment, duplicate events, and rollback after a replenishment checkpoint failure. Existing stream-gap tests preserve the rule that a reconnect cannot manufacture replacement liquidity. Ruff and whitespace checks passed.

Each of BTC, ETH, SOL, and XRP is deployed when its portfolio is flat, using a clean collector stop and a database backup before startup. The shared dashboard is not restarted. Per-asset deployment and verification evidence is stored under `data/runtime/partial-exit-fix/`. This benchmark does not complete 24–48-hour endurance acceptance; live post-partial-fill latency still needs monitoring after deployment.

Deployment completed: BTC, ETH, and SOL restarted at approximately 02:12:38 EDT; XRP restarted at approximately 02:14:09 after its position closed. All four were flat at their individual deployment points. Verification at 02:14:31 confirmed the expected source hash, fresh READY status, no new health errors, and risk balances matching the respective deployment checkpoints. The shared dashboard stayed running.
