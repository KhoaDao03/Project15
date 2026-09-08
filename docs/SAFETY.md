# Safety and recovery

LIVE is structurally blocked. Settings reject LIVE or ENABLE_LIVE_TRADING=true,
and LiveTrader.submit always raises. The HTTP client exposes only GET methods;
the dashboard exposes no trading writes. Read-only reconciliation and V2 payload
construction do not authorize execution. No credentials were provisioned or
orders submitted during development.

Paper safety: one writer lease; unique entry/settlement claims; explicit state
transitions; recheck actual backend time immediately before submission; stale
reference/book rejection; source/local clock skew checks; monotonic clock jump
checks; unknown metadata/fee/sampling rejection; sequence-gap invalidation and
WebSocket reconnect; depth-limited fills/exits; worst-case sizing including fees;
daily loss/trades/gross exposure and concurrent exposure; operator kill file.

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
4. This release does not resume open paper positions automatically. Starting
   paper execution over unresolved prior fills fails closed. Do not delete records
   to bypass the guard. Use a separate research database for a new experiment,
   with the interrupted exposure explicitly reported as unresolved.
5. Only after reviewing a crashed collection-only run, clear that exact lease
   using a database transaction. There is deliberately no automatic lease expiry
   that could create competing writers.

Unexpected data errors invalidate books and cancel paper entries. Database or
recorder errors stop the process instead of continuing without an audit trail.
GET retries are bounded for 429/5xx; order writes are never retried because no
production submission path exists. WebSocket recovery requires fresh snapshots;
missed standard reference samples are not filled with exchange proxy prices.

Remaining limitations: no atomic SQL ledger transaction spanning every paper
fill/position update; no validated live reconciliation; synchronous recording and
simulation can delay application receipt; local Date-header skew is only a coarse
check, not a replacement for host clock synchronization; UI status has its own
freshness indicator and must not be used to decide submission timing.
