# Collector overload detection and recovery

Initially implemented in the original checkout while the collector and dashboard
were stopped. The follow-up reports below record later fixes and deployments.

The subsequent [overnight failure fix](OVERNIGHT_FAILURE_FIX_20260911.md) corrects
entry into drain mode for established books and adds verified dead-owner lease
recovery. Its actual-tape checks supersede the earlier limited drain validation.
The [connection setup fix](CONNECTION_HANDSHAKE_FIX_20260911.md) prevents
heartbeat/metadata events from introducing a replacement connection before it opens.
The [reliability audit](RELIABILITY_AUDIT_20260911.md) adds sustained stream-stall
recovery and bounded diagnostic-history lookup during portfolio restoration.

## Detection and entry gate

The receiver tracks outstanding events, including the worker's in-flight batch,
and the monotonic age of the oldest outstanding event. This avoids hiding lag
when a batch has left the queue but is still processing.

| Condition | Action |
| --- | --- |
| Oldest event age at least 0.5 seconds, or outstanding events at least 50% of capacity | Log a backlog warning and expose it in health/status |
| Age at least `min(2 seconds, reference_max_age, book_max_age)`, or outstanding events at least 80% of capacity | Block entries and request an ordered drain/reconnect |
| Transport interruption, rejected book sequence, or invalid input | Block entries and request recovery |
| Required reference or active book remains missing/stale for ten continuous seconds on an established, clock-valid connection to an open exchange | Keep entries blocked and request an ordered drain/reconnect; report the stalled stream |

The queue remains 20,000 events. The thresholds are implementation constants,
not new strategy settings. Recovery does not change the frozen configuration hash.
The entry gate is also checked in submission and pending-buy fill handling.
An overload arriving during a readiness check cannot be overwritten by that
check's earlier result. Existing strategy enablement, risk limits, HALT,
quarantines, and venue pauses remain enforced.

## Ordered recovery

1. **DRAINING:** stop receiving from the old WebSocket. Persist and apply every
   already-enqueued frame in receipt order, including every book delta. Suspend
   expensive strategy and shadow book evaluations for drain batches. Cancel
   pending entry remainders through the normal engine invalidation path.
2. Wait for the socket to close and the worker to finish the old queue. Do not
   mix replacement-snapshot deltas with the old connection's queued deltas.
3. **RECOVERING:** request fresh REST discovery/fee/exchange metadata, establish
   a new connection identity, and subscribe normally to receive new snapshots and
   official reference data. Positions, held contract identity, settlement polling,
   trade history, consumed liquidity, and risk accounting remain in the same engine
   and ledgers. No portfolio reset or liquidation is performed.
4. **READY:** release the collector entry gate only after the checks below pass.
   Normal model warmup, reference-gap, quality, entry-window, and risk checks still
   decide whether any entry may actually occur. Recovery does not force a trade.

Heartbeat and metadata events keep the previous connection identity while the
replacement WebSocket handshake is pending. Once the socket opens, the receiver
publishes its new identity and `connected` boundary without an intervening await.
A failed handshake therefore cannot publish a partial replacement generation.

Ready requires a connected current generation; valid metadata requested after the
recovery began and received within 30 seconds; an accepted official reference tick
from the new connection; clock and exchange health; an unhalted risk state; and
valid, fresh, sequenced snapshots for all currently tradable markets. Each such
market must also pass receive/source freshness and quarantine/pause checks.
Lag must be at most 0.5 seconds and queued work at most 20% of capacity. Shutdown
prevents release of the gate.

Source sequence numbers are not invented. Live book snapshots and deltas require
integer subscription and sequence identities. A delta must match its snapshot's
subscription, and the existing per-subscription sequence checks reject gaps,
duplicates, and reversals. A new connection invalidates old books; deltas cannot
establish a book before its snapshot. An integrity failure stays entry-blocking
even if later frames in that same recorded batch look fresh.

Validated reference history is retained. The latest accepted reference must come
from the replacement connection; a duplicate of an old tick cannot establish
freshness. Existing model checks can require additional uninterrupted history
before trading after a gap. Safe position monitoring uses the existing freshness
checks while recovering; no position evaluation occurs on suspended drain batches.
Validated settlement events continue through the normal settlement path.

## Visibility and replay

`recovery` appears in collector status and market/reference display snapshots,
including state, cause, blocking reasons, warning flag, oldest outstanding lag,
and outstanding count. State/reason changes are retained as `collector_recovery`
records. Logs report warnings and recovery requests. The dashboard displays
recovery reasons; `paper-health` reports `COLLECTOR_RECOVERING` and
`BACKLOG_WARNING`, rather than declaring the bot healthy solely because it is
connected and responding.

The raw envelope records `analysis_suspended` and `collector_entries_blocked`.
JSONL, compressed JSONL, and Parquet retain both controls. Replay still validates
and applies the ordered source frames, while respecting suspended analysis.
Older tapes without these optional controls retain their previous behavior.

## Validation and limits

The affected regression run passed 122 tests, covering collector lifecycle,
shutdown, sequencing, entry/reference safety, shadow execution, position handling,
profit-value rollback, and recording/replay. Additional final regressions cover
an integrity failure inside a batch and an overload arriving during readiness.
The final recovery/collection/operation/processing-safety run passed all 51 tests
in 15.84 seconds (`data/runtime/overload-recovery/final-tests.xml`). Dashboard
JavaScript syntax validation passed, and both services were confirmed inactive
after implementation.
The synthetic overload integration test records every emitted frame, drains the
old connection, and reaches READY using a new sequenced snapshot and fresh
metadata/reference data. Other tests verify held portfolio/risk preservation,
blocked pending buy fills, missing/mismatched/duplicate/gapped sequences, stale
inputs, and recording-format round trips.

A 13,251-event legacy held-position replay still matches the previous primary
portfolio, execution/audit records, shadow portfolio, and shadow records exactly.
Local evidence is under `data/runtime/overload-recovery/`. Ruff and whitespace
checks passed. These tests do not establish 24–48-hour endurance or prove recovery
against every live venue/network failure.

The existing hard queue-overflow stop remains a final fail-closed fallback if
producers exhaust the physical queue despite early backpressure. Disk reserve,
write failures, authentication/subscription denial, and writer ownership continue
to stop or block operation; recovery does not bypass them. A stalled or failed
recorder cannot be made healthy by reconnecting. Supervisor restart policy is
unchanged. Subsequent deployment and startup verification are recorded in the
[reliability audit](RELIABILITY_AUDIT_20260911.md).
