# Paper recording

Paper execution (dashboard, paper and paper-service) keeps actual
filled-trade evidence and one compressed input tape for its Settlement Edge engine.
Strategy rules, evaluation cadence, freshness gates, fees, risk limits and portfolio
isolation are unchanged.

## Retained data

- `data/raw/<session>.jsonl.gz` stores the source event envelopes: receive and
  monotonic timestamps, event IDs, connections, sequences and original payloads.
  Payloads include reference prices, market metadata and fee schedules, live quotes,
  order-book snapshots/deltas, trades/volume, lifecycle and health events. Preserving
  ordering and these inputs is necessary to recalculate decisions and simulated fills.
- Each compressed batch is flushed and fsynced before analysis. There is no duplicate
  Parquet archive in paper mode. A truncated final batch fails replay explicitly.
  The run's `raw_source` record points to that collection session's tape.
- Orders and cancellations are retained for duplicate prevention and daily risk
  budgets, including attempts that did not fill.
- Entry evidence stays in the pending order checkpoint. The first fill atomically
  saves its original opportunity ID, timestamp, probability, features, book,
  configuration and model identity with the fill. Later partial fills reuse it.
  An unfilled cancellation discards the pending evidence without creating an
  opportunity-history entry. Database failures roll back both evidence and fills.
- Fills, exits, fees, realized results, settlement, relevant transitions, market
  metadata, exceptional health events and checkpoints remain available for recovery.
  Run records retain configuration and source versions for interpreting the tape.
- Latest evaluation and status are replaceable projections. The dashboard shows
  current decisions without keeping every update. No-trade state cycles do not
  write permanent transition history. Replaying the tape can regenerate evaluations.

## Replay and recovery

The existing audit and backtest readers accept compressed tapes directly:

```bash
uv run btc15 audit data/raw/<session>.jsonl.gz
uv run btc15 --config config/original.json backtest data/raw/<session>.jsonl.gz
```

Use the original strategy configuration, model implementation and complete ordered
inputs, including earlier warmup sessions where applicable. A fresh backtest of a
later resumed session alone cannot reconstruct the earlier portfolio or warmup.
Actual paper fills and committed checkpoints remain the authority for what the
running bot did: offline replay does not reproduce host scheduling delays, downtime,
manual activation changes or unavailable inputs automatically. Trade explanations
retain entry evidence and execution timelines; continuous derived charts require
recalculation. Paper outcomes are simulated, not exchange fills.

Observation-only `btc15 collect` (or `dashboard --observe-only`) still saves full
JSONL/Parquet research recordings and evaluation history. BACKTEST retains full
evaluations. Run only one writer per database. A crash requires reviewing the
writer lease; it is never stolen, and recovery does not invent downtime fills.

## Database and dashboard

Two compound indexes serve historical lookups by kind, mode, run and timestamp;
startup installs them on existing databases too. Run-list responses omit large
configuration/source snapshots. Strategy cards batch latest-evaluation queries and
cache lifetime calculations until fills, results or model/run membership change.
Current evaluations and feed freshness continue updating on each request.

The authorized reset on 2026-09-09 deleted 230,318 research records, old states and
checkpoints, while preserving four model definition/activation records, registry
claims, strategy files and credentials. SQLite was vacuumed from 663,937,024 to
86,016 bytes. Existing raw files were outside the table reset and remain on disk.
Normal startup does not delete history.

## Historical multi-strategy validation

The measurements below predate the single-strategy scope change. They are not new
performance measurements of this version. See [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md).

## Validation

Regression tests check compact input round trips, identical deterministic replay
predictions/fills/results, torn-file detection, cache invalidation, batched queries,
and compact run responses. The load script replays captured events at their original
arrival pace into a temporary database while an actual local HTTP server handles
dashboard polling and SSE. Three models receive synthetic warmup history to exercise
calculation load; this is a performance fixture, not trading evidence.

```bash
.venv/bin/python scripts/check_dashboard_load.py data/raw/<session>.jsonl --limit 30000
```

It reports request latency, processing lag, queue depth and compressed tape size,
and fails on request errors, missing tape/stream data or processing lag of three
seconds. This bounded local workload is not a guarantee against every live burst.

On 2026-09-09, 30,000 captured events spanning 83.85 seconds completed in 83.88
seconds with three warmed models, 355 HTTP requests and 1,032 SSE messages.
Processing lag was 354 ms at p95 and 605 ms maximum; peak queue depth was 701
events and the queue drained. Endpoint p95 latency ranged from 10 to 62 ms with
no request errors. The compressed tape was 3,060,639 bytes and decoded all 30,000
events. This used the freshly reset database workload, not a large historical
backtest archive. All 226 regression tests passed; strengthened probability-output
replay checks also passed in a subsequent targeted run.
