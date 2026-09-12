# Fractional passive-fill correction

This fixes the queue/quantity arithmetic in `PaperExecutor` against base commit
`30f99fb`. Previously, an eligible trade of 1.60 contracts against 1.50 contracts
of queue ahead could produce `0.10000000000000009`, causing `fill()` to reject a
valid 0.10-contract fill. The engine's invalid-data handler could then cancel the
paper order. This is a reproduced failure mode, not proof of the cause of any
particular overnight session.

## Matching policy

Queue construction and depletion now use Decimal arithmetic. Newly modeled queue
ahead is rounded **up** to the existing 0.01-contract increment. For example,
1.01 visible contracts times a 1.5 multiplier becomes 1.52 contracts ahead, not a
fractional executable queue of 1.515. This rounds conservatively against the bot.

Older checkpoints may still contain a sub-cent queue. After consuming that queue,
only the residual executable quantity is rounded **down** to 0.01 contracts. A
0.005-contract residual produces no fill and no error. Discarded sub-cent residuals
are not carried forward as reusable traded volume. A 0.015 residual fills 0.01,
never 0.02. Fills are still capped by the remaining order quantity.

The original source volume is validated before queue mutation: it must be finite,
positive, and aligned to 0.01. Malformed, non-finite, nonpositive, and off-grid
source quantities raise `ValueError("Invalid trade quantity")`. This intentionally
closes the old path that silently consumed or ignored malformed quantity data.
Direct `fill()` validation remains strict; arbitrary input is not silently rounded.

## Preservation and restart

Queue fields remain JSON numbers, and the checkpoint schema is unchanged. Existing
checkpoints are read without migration. First-fill evidence, fee carry, partial
positions, duplicate-trade protection, and SQL transaction rollback remain intact.
Entry thresholds, model probabilities, fees, latency/freshness checks, daily risk
budgets, one-attempt policy, and the live-submission block are not relaxed.

After deployment, stop cleanly and resume the same run with its original
configuration. Restart still cancels unfilled resting remainders; this patch does
not infer downtime fills, reopen cancelled attempts, clear writer leases, reset
risk budgets, or rewrite historical results. An unresolved crash still requires
the recovery procedure in `SAFETY.md`.

## Verification

`tests/test_fractional_passive_fills.py` adds 36 cases covering both sides, original
and moderate entry settings, exact repeated depletion, legacy queue checkpoints,
duplicate trades, quantity caps, invalid source volume, eligibility guards, partial
fills, first-fill evidence, settlement/fees, and a failed-write rollback/retry.

In the review environment, these tests ran with the actual executor, domain and
risk code and the hash-verified production Store on temporary SQLite databases.
Imports were isolated from the unavailable PyArrow and engine/model import chain;
synthetic market fixtures were used. All 36 passed after the patch. Against the
pre-fix executor, 29 failed and 7 passed. Python syntax checks also passed.

The full dependency-locked suite, Ruff, package build, PostgreSQL, complete engine
integration and authenticated/overnight replay were not run in that environment:
DNS failures prevented cloning and installing the missing dependencies. These
remain deployment checks, not claimed passing results. In a complete checkout:

```bash
uv sync --extra dev --locked
uv run pytest -q tests/test_fractional_passive_fills.py tests/test_execution.py tests/test_trade_recording.py tests/test_hardening.py
uv run pytest -q
uv run ruff check src/btc15/execution.py tests/test_fractional_passive_fills.py
uv run ruff format --check src/btc15/execution.py tests/test_fractional_passive_fills.py
uv build
```
