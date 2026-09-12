# Entry policy versus existing-position management

## Scoped review and implementation plan

Baseline: `55ab6811eb2ff96bd0b6331d7e5d75c7037e739e` on the Settlement Edge-only branch.
The engine previously used one `healthy` gate for resting entry fills and sell
monitoring. `KILL_SWITCH` and `MODEL_INACTIVE` made that gate false; any quality
reason (including warmup or a shock) did the same. `monitor()` also required a
probability even for its independent hard-stop and take-profit branches.
This could leave already-filled inventory unmanaged before expiry.

The approved correction is limited to `engine.py`, `execution.py`, regression
tests and these operating notes. Keep the entry rules, fee/risk configuration,
quote matching, fractional arithmetic, state machine and storage schema intact.
Use two explicit permission checks, not a new framework or setting. Test the
former failure first, then policy separation, unsafe inputs, durable results and
rollback with the actual engine/executor/store. No dependency or UI rewrite is
needed.

## Current policy

A blocked entry is not a request to liquidate. Exits still require an existing
position, an applicable configured exit trigger, modeled latency and available
executable depth. They never increase inventory or reactivate entries.

| Condition | New entries/resting entry fills | Price-based stop / take-profit | Probability/hold-value exit |
| --- | --- | --- | --- |
| Risk kill switch or entries inactive | Blocked; active remainders cancelled | Allowed when common safety checks pass | Allowed with a usable model and common safety checks |
| Coordinated stop / execution off | No further entry fills or submissions | Existing position may follow ordinary exit rules while queued events drain | Same; this is not forced liquidation |
| Disabled strategy configuration | No entries | Does not itself disable position management | Does not itself disable position management |
| Entry window or entry sizing budget exhausted | No new entries | Not an exit blocker before market close | Not an exit blocker before market close |
| Warmup, shock, reference-history gap, unavailable model | Entry checks unchanged | Allowed with safe current data | Suppressed until model quality recovers |
| Missing/invalid/stale reference or book; processing delay; clock problem; feed failure; exchange pause; unverified fees | Blocked | Blocked | Blocked |
| Contract state `HALTED` / `ERROR`, inactive market, or close reached | Blocked | No new pre-expiry sell execution | No new pre-expiry sell execution |

Daily sizing limits keep their existing reservation semantics: this change does
not revoke already-budgeted orders merely because their submission consumed the
last daily attempt, and does not reset budgets or free risk before trade close.
Entry filters still govern revalidation of those orders.

### Common safety checks are not bypassed

Both execution paths retain valid/fresh book and reference requirements, including
source and receive times, synchronized clock, healthy feed, verified fees and an
active exchange. Freshness is checked again **after model computation** using the
processing clock in PAPER mode (virtual event time in BACKTEST). Management also
checks that the market is still tradable at execution time, including a close that
occurs during model computation. Reference-free exits are **not** introduced.

Only matching new order-book events can initiate/execute a sell. A ticker update,
heartbeat, reference tick or replayed trade does not invent executable depth.
Unknown health/quality reasons fail closed. Explicit model-only exceptions are
`WARMUP`, `SHOCK`, `REFERENCE_GAP` and `MODEL_UNAVAILABLE`; stale-data reasons are
not exceptions.

### A missing model is not a probability of zero

The engine supplies no probability to the executor when model quality is blocked.
The executor can still evaluate the configured hard stop and take-profit from
current executable prices. Probability-based invalidation requires a finite
numeric conservative probability in `[0, 1]`. Missing, malformed, non-finite or
out-of-range values do not create an invalidation signal. They also do not suppress
an independently triggered price-based exit.

### Kill switch versus contract quarantine

`executor.risk.halted` is an entry-policy latch. It is preserved in checkpoints
and remains set even after a successful exit. Removing a HALT file does not
reactivate a halted portfolio. Do not edit checkpoint flags to bypass this.

A market's state `HALTED` or `ERROR` is different: it may indicate changed contract
rules or invalid metadata. This change does not bypass that quarantine, infer a
settlement, or fix the separately identified metadata/settlement recovery issue.
Official settlement routing is unchanged and does not depend on model quality.

## Recording, P&L and compatibility

Sells still use the existing atomic transaction for fills, position/risk updates,
terminal `trade_result` and checkpoint. A full exit records realized P&L and
releases the reservation; a partial exit retains its unclosed exposure. A failed
write rolls back and the same event can be retried without duplicate fills.
Normal history/analytics/replay endpoints read the same records and mode/run IDs.
There are no new tables, schema migrations, API removals or history rewrites.

Checkpoint configuration/mode checks and legacy group guards are unchanged. After
a clean stop and backup, resume the same run with the same frozen configuration;
resting remainders are cancelled rather than filled during downtime. After feed
integrity and fresh current data recover, price-based exits may operate while the
model rebuilds its history. Old fractional-position corruption, metadata recovery
and observed-liquidity replenishment are separate work, not silently repaired here.

## Verification

`tests/test_position_management.py` exercises the real Engine, PaperExecutor,
SQLite ledger/checkpoints and FastAPI history/analytics/replay handlers. Quotes,
reference samples and model outputs are deterministic synthetic test inputs; model
functions are controlled to isolate execution permission, not demonstrate edge.
Existing Monte Carlo/synthetic replay tests remain part of the complete suite.

Coverage includes PAPER/BACKTEST, YES/NO, entry latches and budgets, valid-model
invalidation, price-only exits during model problems, unsafe data and clock cases,
post-computation expiry, same-market book-event routing, latency, duplicate events,
halted checkpoint resume, rollback/retry and reopened database history/P&L.
These offline tests are not an authenticated live-feed or profitability validation.

### Executed verification on September 9, 2026

Implementation commit: `c46d349bcf634afcdff48da8926dad3422a09eb2`.
[Completed implementation validation](https://github.com/KhoaDao03/Project15/actions/runs/34402299244)
(job `102636860780`) installed the unchanged locked environment on Ubuntu 24.04 /
Python 3.12.3, tested the working tree, then committed and published that tested
code. The completed log was inspected. The temporary write-enabled workflow
removed itself; ordinary validation workflows remain read-only.

- Baseline reproduction: 36 selected regression cases failed against the old shared
  gate as expected, with zero setup/collection errors.
- Targeted suite: **156 passed**, comprising 151 new position-management cases and
  five existing operation tests.
- Full offline suite: **362 passed**, with two existing Starlette/httpx and AnyIO
  deprecation warnings.
- Ruff lint, formatting of the four changed Python files, Python compilation,
  JavaScript syntax, source/wheel builds, diff-whitespace checks and protected-file
  comparisons all passed. No separate static type checker is configured or claimed.

The first implementation attempt passed all 151 new cases but its full suite
exposed a timing-dependent shutdown test: it stopped after a fixed 0.15 seconds,
before the fake socket delivered all 50 expected messages on that runner. In
`tests/test_operation.py`, shutdown now starts after all 50 frames are delivered,
with a 10-second failure timeout. The recorded-frame count, final disconnect and
writer-release assertions are unchanged. Production collector/shutdown code was
not modified. The complete suite was then rerun successfully; the failed attempt
did not publish runtime changes.

These measurements precede the documentation-only commit recording them. Later
PR/branch runs should be checked against their own exact head SHA; this historical
result is not a substitute for final-commit CI. No authenticated feed, actual
market recording, PostgreSQL execution, full browser-rendering test or deployed
laptop process was exercised by this change.

See [Safety](SAFETY.md), [Strategy](STRATEGY.md) and [Validation](VALIDATION.md).

## Subsequent metadata recovery change

The separately scoped metadata/settlement recovery work is now described in [Settlement recovery](SETTLEMENT_RECOVERY.md). It does not relax this document's pre-expiry exit safety checks or contract-quarantine trading block.


## Persistent venue pause

A fresh quote cannot undo an exchange deactivation. Venue restrictions are saved
in the paper checkpoint and block both entries and pre-expiry exits. After a
pause or activation hint, a parser-valid active REST observation from a request
started after that notification, followed by a fresh book, is required. See
[market-pause handling](MARKET_PAUSE.md). Settlement and metadata recovery remain
separate; never delete a checkpoint or clear a risk halt to bypass a venue pause.
