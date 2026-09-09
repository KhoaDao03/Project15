# Safety and recovery

LIVE is structurally blocked. Settings reject LIVE or ENABLE_LIVE_TRADING=true,
and LiveTrader.submit always raises. The HTTP client exposes only GET methods;
the dashboard cannot submit trades. Its strategy settings endpoint can save
validated configuration for new sessions, but cannot modify a running engine. Read-only reconciliation and V2 payload
construction do not authorize execution. User-configured credentials were used for authenticated read-only validation;
no exchange orders were submitted.

Paper safety: one writer lease; unique entry/settlement claims; explicit state
transitions; recheck actual backend time immediately before submission; stale
reference/book rejection; source/local clock skew checks; monotonic clock jump
checks; unknown metadata/fee/sampling rejection; sequence-gap invalidation and
WebSocket reconnect; depth-limited fills/exits; worst-case sizing including fees;
daily loss/trades/gross exposure and concurrent exposure; operator kill file.

The **Strategies** enable switch applies only to new sessions and is not an
emergency stop. Settings saves require JSON and reject a supplied foreign Origin;
the dashboard remains intended for localhost, without multi-user authentication.
See [configuration controls](STRATEGY.md#edit-settings-in-the-dashboard).

## Operating the kill switch

`btc15 halt` writes DATA_DIR/HALT. The collector observes it on its receive loop,
cancels resting paper entries and blocks further entries. Open positions continue
to carry risk; the switch is not a guaranteed liquidation order. The flag stays
latched for that process. Review logs and positions before removing the file and
restarting. On loss of connectivity, no inferred stop fill is reported.

## Crash recovery

1. Confirm the old writer is stopped. Preserve its journal and database.
2. Inspect `leases`, orders/fills and official settlements. Never clear a lease
   while another writer is alive.
3. Replay the original journal in an isolated BACKTEST database to understand
   the interrupted decision sequence. Repair a torn final JSONL line only in a
   copy, with an audit of what was excluded.
4. Inspect the run's atomic `paper_checkpoints` projection and configuration.
   Legacy runs without checkpoints require forensic recovery; do not invent queue
   or fee state, or delete immutable records to bypass unresolved-position guards.
5. After confirming the crashed writer is stopped, clear only its exact lease in
   a database transaction. There is no automatic lease expiry that could create
   competing writers. Resume a checkpointed run with `btc15 paper --resume RUN_ID`
   and the same configuration. Resting remainders are cancelled, filled exposure
   stays reserved, and feeds must become healthy before further execution.

Unexpected data errors invalidate books and cancel paper entries. Database or
recorder errors stop the process instead of continuing without an audit trail.
GET retries are bounded for 429/5xx; order writes are never retried because no
production submission path exists. WebSocket recovery requires fresh snapshots;
missed standard reference samples are not filled with exchange proxy prices.

Paper execution actions now use atomic SQL ledger/checkpoint transactions with
in-memory rollback. Socket receipt is independent of the ordered processing worker.
A bounded queue stops capture on overload. Date-header skew remains a coarse check,
not a replacement for host synchronization. On this WSL installation, Ubuntu's
timesyncd was disabled to stop competing with WSL's system-distribution chronyd;
see [the repair and rollback instructions](COLLECTION_RECOVERY.md). Keep one clock
controller active. Any subsequent backward host-clock steps remain recorded and
explicitly block clean-dataset acceptance. UI freshness must not
be used to decide submission timing. Live reconciliation remains unvalidated.
