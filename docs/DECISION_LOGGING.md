# Decision and execution audit records

These records explain paper decisions; they do not alter the strategy, price caps,
entry attempts, matching, risk limits, or accounting. Existing `INVALIDATION`
reason values remain compatible with historical analytics.

- Submitted `order` records include entry thresholds, conservative probability,
  decision/submission timestamps, and current execution conditions.
- `exit_intent.decision.trigger` distinguishes probability below the exit
  threshold from insufficient hold value. It records the actual calculation,
  thresholds, executable bid, stop/target, and source event ID. One snapshot is
  recorded when an exit is requested, not for every subsequent quote.
- Buy and sell `fill.observation` records contain quote/source age, reference
  receive age, processing lag, best prices and quantities. Buy fills retain
  submission and eligibility times; sell fills include exit eligibility time.
  Unavailable context is null. Processing lag here is wall-clock elapsed time
  since input receipt, unlike the collector's monotonic lag metric.
- `position_monitoring` marks an interruption and its resumption once each,
  rather than emitting the same warning for every event. Restarting with an open
  position records that fresh inputs are awaited. Controlled shutdown/failure
  records the interruption if the process can still write. A forced kill or disk
  failure cannot guarantee a final record; service logs and receipt gaps remain
  necessary. Freshness recovery does not mean the probability model is ready:
  price-only risk reduction may be available during warmup.
- `entry_rejection_summary` aggregates counts per market and reason for approximately
  60 seconds and stores the first compact decision sample in that interval.
  Counts refer to recorded decision evaluations, not every market event or unique
  opportunity. A decision can contribute multiple reasons. Shutdown flushes the
  remaining partial interval. Counters are in memory; abrupt termination can lose
  an unflushed interval.
- `hold_to_settlement_comparison` records the hypothetical outcome of holding the
  same purchases until official settlement, compared with actual realized P&L.
  It uses entry fees only, assumes no settlement fee, and never changes the ledger
  balance, position, win rate, or realized P&L. It is not an alternative execution
  backtest. Old markets already settled are not backfilled.

## Volume and compatibility

No new background queue or per-quote database transaction is introduced. Detailed
records are attached to existing order/fill/exit writes. Rejection summaries flush
at most once per minute, plus shutdown. Input tapes and all financial records are
preserved. Existing configuration fields in opportunity evidence remain because
replay and analytics currently depend on them; new audit records use the model
identity/configuration hash instead of copying the entire configuration.

The existing compressed input tapes and disk-reserve shutdown remain in effect.
No history is automatically deleted. Long-term raw-data archiving/retention still
requires a separate policy; the new logging does not make storage unlimited.

Use `scripts/check_collector_throughput.py` against the saved incident interval to
compare throughput, exact portfolio state, serialized ledger bytes, compressed
raw bytes, and peak process RSS. RSS is an OS high-water mark, not a leak test.

## Validation of this update

The saved 13,251-event incident interval replayed at 6,184 events/sec before and
5,926 events/sec after the update in a paired run (about 4.2% lower throughput).
Portfolio state and execution results matched exactly. This interval exercises
the held-position quote-processing path; it does not measure every entry/exit
workload. Serialized runtime ledger bodies increased by 972 bytes, including the
final rejection-summary flush; raw recording size stayed 865,703 bytes. Peak
process RSS increased by approximately 1.4 MiB. Startup source snapshots are
excluded from the ledger-byte comparison. Timing varies with host load; this is
not a multi-day soak or a bound on future log growth.

Received-reference entry waits are recorded as `entry_revalidation_wait`, with
`REFERENCE_UPDATE_PENDING`, order ID, reference event ID and receipt time. A new
submission blocked for the same reason uses `execution_rejection`. Neither a wait
nor a rejected submission is a fill. See [entry reference revalidation](ENTRY_REFERENCE_REVALIDATION.md).
