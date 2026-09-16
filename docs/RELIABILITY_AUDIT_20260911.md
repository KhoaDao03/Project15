# Reliability audit — September 11, 2026

Operating priority: preserve order-book and portfolio correctness, keep collection,
monitoring and recovery available where possible, and block new entries whenever
required data cannot be trusted. READY clears the collector gate; all strategy,
reference-history, risk and contract checks still apply.

CAP availability is not the same as process uptime. The
[Gilbert–Lynch result](https://groups.csail.mit.edu/tds/papers/Gilbert/Brewer6.pdf)
does not allow both linearizable consistency and successful responses to every
request during a network partition. This single-writer bot is not a replicated
database, and its input-freshness rules are application safety rules. Uninterrupted
new trading decisions during a market-data outage are not an acceptance criterion.

## Confirmed gaps and fixes

### Connected socket with a stalled required stream

The receiver's one-second receive timeout continued waiting on an open socket.
The collector correctly blocked entries on stale/missing data, but its recovery
controller only requested a reconnect for transport errors, integrity failures or
processing overload. A socket carrying only display data could leave the bot
blocked indefinitely without repairing its required reference/book subscriptions.

The controller now tracks sustained stalls separately for the official reference
and each active market's order book. Existing freshness limits block entries
immediately. Ten continuous seconds of failed freshness/snapshot checks on an
established, clock-valid connection to an open exchange request the existing
ordered drain/reconnect procedure. Fresh data, a replacement connection, or a
market leaving the active set resets the corresponding timer. Elapsed time uses
the monotonic clock.

With the current three-second reference and five-second book limits, an otherwise
healthy stream generally triggers repair about 13 or 15 seconds after its last
accepted update, plus scheduling and connection setup time. Missing startup
evidence gets the same ten-second grace. Brief gaps do not force a reconnect.
Quiet books can also need a fresh snapshot; their original age limits are not
relaxed. A provider outage can cause repeated retries while entries remain blocked.

`REFERENCE_STREAM_STALLED` and `BOOK_STREAM_STALLED:<ticker>` identify the cause in
recovery records and dashboard reasons. Display-feed traffic cannot satisfy the
official-reference requirement. Metadata refresh, HALT, contract quarantine,
venue pauses, and strategy entry filters keep their existing behavior; a paused
or quarantined market does not trigger a book-stall retry. Shutdown cannot
release the entry gate.

Received old frames are still durably recorded and applied in sequence before a
new connection is established. Fresh REST metadata, an accepted reference sample
from the new generation, and validated sequenced snapshots are all required to
resume. Recovery does not reset positions, results or risk accounting.

### Historical diagnostic flood inflated every restart

`restore_contract_history` loaded every historical health and invalid-market row
before checking whether any open position or pending order required that evidence.
The production snapshot contained 263,654 health rows, including the prior false
integrity-error flood. Restart repeatedly decoded all of them into Python objects.

Startup now queries only whether relevant integrity evidence exists for each held
or pending market from its entry time onward. It returns at most one ID, without
materializing diagnostic bodies. Flat/completed exposure skips this lookup. The
history remains intact, and changed contracts, invalid metadata and RULES_CHANGED
evidence still quarantine affected exposure. Legacy contract reconstruction and
ambiguous/missing-history failures retain their existing checks.

Measurements used separate copies of one consistent 175.8 MB production snapshot,
the same frozen configuration and cProfile on the Engine resume path:

| Measurement | Before | After |
| --- | ---: | ---: |
| Engine resume elapsed time | 3.249 s | 0.759 s |
| Peak process RSS | 405.8 MiB | 124.3 MiB |
| Health records decoded by the bulk list call | 263,654 | 0 |
| Time in the bulk health list call | 2.521 s | Removed |

This is a startup benchmark, not sustained-memory evidence. The snapshot had no
filled position and one pending order. Both versions cancelled that order on
resume as designed; restored checkpoints matched apart from cancellation time.
All 116 retained fill/result records compared identically. The new existence
query took approximately 35 ms on this snapshot; parsing retained market metadata
is now the larger history-restoration cost. No index, retention policy, or strategy
setting was changed.

## Validation

Six deterministic stale-stream cases failed against the previous recovery code
and pass after the fix: missing reference, stale reference receipt/source time,
missing snapshot, and stale book receipt/source time. They keep held positions
and the executor/risk snapshot unchanged. Further checks cover grace-period
reset, replacement generations, non-stream blockers, scoped integrity evidence,
pending orders, retained positions, transaction rollback, and flat restart.

The collector integration replay now includes a connected socket that continues
sending 5 Hz display data while withholding either the official reference or the
book snapshot. Both cases request repair, reach READY on a replacement generation,
drain their queues, and replay to identical books, sequences, portfolio/risk state,
fills and results. These two scenarios contain no held exposure; held-position
preservation is checked separately. Existing overload, disconnect and real
sequence-gap scenarios remain in the same integration test.

The retained 13,251-event held-position burst replayed with shadow processing
disabled in 2.880 seconds (about 4,602 events/second). Its primary portfolio,
execution and audit records matched the retained pre-change oracle exactly. The
oracle's shadow-only fields were excluded because shadow is disabled in this run.
This short legacy scenario produces no new fill/result and does not establish
sustained production capacity.

The full suite finished with 922 passing tests and one pre-existing stale
configuration-hash expectation. That test omitted the newer disabled-by-default
`profit_value_exit_enabled` field when reconstructing the historical control hash.
Its expectation was corrected and an opt-in hash-change assertion added; no
production strategy/hash behavior changed. The targeted rerun passed all 15
cases, including the added transaction-rollback check. Across both reports, all
924 distinct tests have a passing latest result. The complete run took 410.84
seconds; the targeted rerun took 4.45 seconds. Two existing dependency-deprecation
warnings remain. Ruff lint/format and whitespace checks passed.

Evidence is retained in `data/runtime/reliability-audit-20260911/`, including the
database snapshot, before/after profiles, replay output and test reports.

## Deployment

After the open paper position closed and no pending orders remained, the collector
was restarted at 13:29:40 EDT (PID 40811) and the dashboard at 13:29:45 (PID 40880).
Both resumed `settlement-convergence-v8`; the loaded collector source hash matched
the tested checkout:
`1b7467131e92c396315d338411f6a9d30616c5c42e8c811ac311ae68878f08d1`.
The frozen configuration remains `75d8de22c3ccaf5f`, and the collector command
continues to omit the shadow-comparison flag.

The full primary checkpoint, retained primary/shadow trade hashes, shadow checkpoint
and configuration file hash matched before/after deployment. Five consecutive
dashboard API samples confirmed a fresh collector status and READY recovery gate.
The dashboard correctly showed COLLECTING while the model rebuilt reference
history. Processing delay was 5–52 ms; the observed queue fell from 53 events to
1–2, and reference age stayed below 0.21 seconds. These are bounded startup checks,
not an endurance result.

## Remaining endurance acceptance

These changes do not certify 24/7 operation. A fresh monitored 24–48-hour paper
session must cover multiple market rollovers and verify:

- Queues repeatedly drain, normal processing stays within freshness limits, and
  recovery completes when required source data becomes available again.
- Process RSS stabilizes after warmup; service-cgroup memory is interpreted
  separately because it includes filesystem cache. Disk growth and the 10 GiB
  reserve remain visible.
- No unexplained collector/status gaps, restart loops, sequence failures, duplicate
  fills, or changes to positions, consumed liquidity and realized results occur.
- Shutdown/checkpoint/resume preserves the same run and frozen configuration.

The installation still has one host, one writer and one database. Host sleep,
power loss, permanent credential/subscription denial, failed storage, or an
unresponsive process cannot be made continuously available by reconnecting its
socket. The existing supervisor restart limits and fail-closed disk/ownership
checks remain in effect. No replica, automatic risk-halt reset, log deletion or
new live-trading path was introduced.
