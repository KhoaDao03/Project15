# Pending-exit processing improvement

The September 11 paper-run audit found that repeated unchanged stop checks copied the entire accumulated portfolio before discovering that execution was still waiting for eligible data. In the recorded 20:42 EDT incident, 493 of 527 atomic monitor calls could return without changing state.

`PaperExecutor._apply_monitor` now checks those waits before entering the atomic `_commit_monitor` operation. It returns early only when the nonempty exit reason is unchanged, there is no warning, the mark does not extend either saved extremum, and the event is already consumed or execution is still waiting for the modeled clock/book eligibility boundary. Both ordinary stops and pending profit-value exits use this path.

Actual mutations retain the existing atomic snapshot, transaction, checkpoint, and rollback behavior. Trigger changes and cancellations, changed extrema, eligible executions, fees, and consumed liquidity continue through that path. The profit-value timeout check still runs before this optimization. No order-book updates are skipped and no freshness, sequence, or queue limits change.

## Validation

Focused tests cover clock waits, book waits, duplicate events, exact eligibility, depth consumption, and rollback after checkpoint failures during extrema updates, trigger cancellation/replacement, and fills. Existing profit-value timeout, execution, and collector recovery regressions are also exercised.

The actual implementation processed the audit's 6,616-event reconstruction in 1.685 seconds versus the pre-change baseline's 3.028 seconds (approximately 44% less elapsed time). Final portfolio state and all execution records matched, ignoring ordering of records sharing a timestamp; neither run rejected a sequence update. Evidence is under `data/runtime/trade52-health-audit/implemented.json` and `implemented-comparison.json`.

This is a bounded offline performance comparison using an earlier historical checkpoint and an interpolated processing clock, not an exact live scheduling replay or a guarantee that the original stop would have filled at an earlier price. Repeat sustained paper validation after deployment to measure whether overload interruptions decrease.

Implementation is prepared on disk. The running collector was not restarted as part of this change and does not yet use it.

Validation completed: 225 distinct tests passed across the focused pending-exit, execution/profit-value, recovery/status, liquidity/fractional-exit, shadow compatibility, and storage/replay suites. Ruff checks and diff whitespace checks passed. Test output contained existing Starlette/httpx deprecation warnings.
