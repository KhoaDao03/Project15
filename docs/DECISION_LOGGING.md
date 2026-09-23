# Paper decision and execution audit records

These are paper-ledger records. For every received input, model calculation,
check and real-order event, use the separate opt-in [research log](RESEARCH_LOGGING.md).
Ledger sampling and portable capture have different completeness guarantees.

| Record | Meaning |
| --- | --- |
| `order` | Entry thresholds/probability, decision/submission times and execution conditions |
| `exit_intent.decision.trigger` | Actual exit calculation, thresholds, bid, stop/target and source event; one snapshot per requested exit |
| `fill.observation` | Quote/source/reference ages, processing lag, prices/depth and submission/eligibility clocks; unknown fields null |
| `position_monitoring` | One interruption/resumption record, including fresh-input wait after restart; forced failure may prevent a final record |
| `entry_rejection_summary` | About 60 seconds of counts per market/reason plus the first compact decision; shutdown flushes the partial interval |
| `hold_to_settlement_comparison` | Hypothetical hold result using entry fees and no settlement fee; does not change realized P&L or accounting |
| `entry_revalidation_wait` | Pending newer reference, with order/reference IDs and receipt time |
| `execution_rejection` | Submission blocked, including by a pending reference; never a fill |

A decision may contribute several rejection reasons. Counts are recorded evaluations,
not unique opportunities or all market events; abrupt termination can lose an
unflushed summary. Hypothetical hold comparisons do not backfill already-settled
markets and are not an alternative execution backtest.

Fill processing lag is wall-clock time since input receipt, unlike the collector's
monotonic lag metric. Freshness recovery need not imply model readiness; price-based
risk reduction can be possible during warm-up. See
[entry revalidation](ENTRY_REFERENCE_REVALIDATION.md) and [recording modes](TRADE_RECORDING.md).

## Volume and compatibility

Detailed ledger audits attach to existing execution writes, with rejection summaries
flushed once per minute plus shutdown. Financial records and required input tapes
remain intact. Historical `INVALIDATION` reasons stay compatible; opportunity
config fields remain for replay, while new audits reference model/config identity.
Portable research queues and retention are documented separately.

## Historical validation

A paired replay of a saved 13,251-event held-position interval measured 6,184 versus
5,926 events/sec (about 4.2% slower), with identical portfolio/results. Serialized
ledger bodies grew 972 bytes, including final summary flush; raw data stayed
865,703 bytes and peak RSS grew about 1.4 MiB. Source snapshots were excluded.
These are interval/host measurements, not endurance or future growth bounds.
Use [collector benchmarks](VALIDATION.md#development-scripts) for a current comparison.
