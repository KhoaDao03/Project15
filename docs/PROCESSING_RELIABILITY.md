# Processing backlog fixes and validation

The September 9 collector failure was local processing overload. Its maximum queue
reached 20,000 events and recorded processing lag exceeded 33 seconds. The dashboard
previously obtained its BRTI display only after those events passed through analysis.

The corrections preserve ordered, durable strategy input:

- A market enters settlement-pending once. Subsequent quotes no longer repeatedly
  read its database state and snapshot a nonexistent or already-cancelled order.
  Settlement messages still reach the executor.
- Best-ask and momentum top-level liquidity checks no longer sort the full book.
  State reads reuse a parameterized SQL statement; they still read current state.
- Momentum observations reuse unchanged reference/history calculations. Cache keys
  change on reference updates, minute rollover, confirmation-window boundaries,
  and reference-gap eligibility changes. Candle age updates on every lookup.
- Live processing checks wall-clock freshness before evaluating and again before
  execution, including source timestamps. Old queued events cannot fill orders or
  execute position exits merely because they were fresh when received. Historical
  backtests continue to use event time.
- Abnormal collector exit cancels remaining paper orders. Overflow retains the
  rejected frame and subsequent shutdown frames in receipt order, flushes raw data,
  and does not acknowledge a clean shutdown. Disk write failures remain errors.
- The 5 Hz display uses a separate receipt projection. It never authorizes trades
  or replaces the durable 1 Hz settlement reference. The UI reports stale reference
  age and processed-quote lag separately. Publication is awaited during shutdown.

## Reproducible offline load check

From the project directory:

```bash
.venv/bin/python scripts/check_processing_throughput.py \
  data/raw/48229c91-b9a7-49e0-b3fb-0bc7c8602f00.jsonl --warm-history
```

This uses a temporary database, all three models, an hour of synthetic reference
warmup, real recorded event timing, and durable JSONL/Parquet writes. It does not
connect to an exchange. The warmup is a workload fixture, not research evidence.

The measured 60,000-event segment spans 110.99 seconds of captured traffic. Before
observation caching, the warmed-history run took 228.82 seconds. With the fixes,
it took 67.17 seconds (1.65 times the recorded input rate), and all 60,000 raw events
were retained. These figures are a local replay measurement, not a guaranteed
production capacity or a worst-case latency bound. The test does not simulate
all network, disk, dashboard-query, or host-contention conditions.

Regression tests cover expired-market settlement, fresh versus delayed passive and
aggressive fills for all three models, delay introduced during model computation,
cache equivalence at fractional timestamps and gaps, a blocked analysis worker,
overflow record ordering, and clean-shutdown acknowledgement rules.

Live execution remains disabled. Sustained paper operation across market rollovers
and busy periods is still required before assessing readiness for real money.
Any future live adapter must enforce its own submission freshness, cancellation,
and reconciliation rules; paper-only checks do not establish live-trading safety.

## Follow-up: reducing per-quote work

The later `10848d3f-e2af-4cd2-ada4-976835246d5d` capture still exhausted the
20,000-event queue. Profiling its recorded traffic identified repeated whole-book
validation, momentum reference-history filtering, and database state reads as
remaining costs. The processing path now:

- Validates the changed book level on each delta and still checks for a crossed
  or locked book. Snapshots retain full validation. Invalid prices, non-finite
  quantities and negative depth invalidate the book.
- Reuses the momentum reference list once every tick in it is causal. Each new
  reference list recomputes its eligibility bound; future observations and clock
  reversals retain the exact source-and-receipt timestamp filter.
- Reads market state without committing a read-only transaction. Reads inside an
  existing transaction continue to see that transaction's uncommitted changes.

The three-second execution freshness gate, ordered raw recording, strategy
thresholds and independent portfolios are preserved. All 208 tests passed,
including new randomized book equivalence, invalid-delta, causal-reference and
transaction rollback checks. Ruff lint passed; the changed Python files pass
format checking. The repository-wide format check still flags the pre-existing
formatting in `tests/test_dashboard_markets.py`.

These code changes take effect when the collector is restarted. This follow-up
does not start, stop or activate trading services.

Measured before and after with the same command (no profiler attached):

```bash
.venv/bin/python scripts/check_processing_throughput.py \
  data/raw/5dcd198f-2ad2-4311-9041-0c372217cf45.jsonl --limit 60000 --warm-history
```

Both runs processed all 60,000 events from a 149.74-second recorded interval,
using three models, an hour of synthetic warmup and a temporary database with
durable raw recording. Processing time fell from 78.38 to 33.15 seconds (58%
less time, 2.36 times the throughput). Capacity relative to the recorded average
input rate rose from 1.91 to 4.52 times. These sequential local measurements are
not a live latency guarantee; production dashboard load, burst timing, storage
and host contention remain outside this benchmark.
