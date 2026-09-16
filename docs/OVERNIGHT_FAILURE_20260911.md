# Overnight collector failure investigation — September 11, 2026

Investigation used read-only SQLite connections, service journals, process state,
the captured source tape, and a disposable reproduction database. No service was
started, stopped, or restarted; no lease, checkpoint, trading configuration, or
production source was changed. All times below are America/New_York (EDT).

## Findings

There are two distinct failures: a bug in the overload drain path during the
overnight session, and a persistent writer lease preventing startup after the
morning WSL shutdown/reboot. The dashboard's stale-status warning is accurate.

| Time | Evidence |
| --- | --- |
| 02:26:43 | Collector started on the previous WSL boot. |
| 02:26:44 | Run `settlement-convergence-v8` resumed using config hash `75d8de22c3ccaf5f`. Recorded source hash `09e17cc0068b493e3c12f2155b28ead679353cb1b63abf3ae0ff9c357e12d0e3` matches the current source. The new recovery code was loaded. |
| 02:32:51 | First overload request: approximately 2.05 seconds of oldest-event lag and 2,429 outstanding events. |
| Overnight to 11:39 | 145 overload requests, 85 integrity recovery requests, 4,042 backlog warning logs, and 263,179 recorded `Delta subscription does not match its snapshot` errors. The collector continued running/reconnecting; this was not an overnight process exit on a full 20,000-message queue. |
| 11:39:44 | WSL shutdown stopped the user manager and requested collector/dashboard shutdown. The collector was draining another overload. |
| 11:39:47.702 | Last worker status: disconnected, entries inactive, zero open primary positions, 9.756 seconds processing lag, 1,519 queued messages; session maximum queue 5,735. These are historical values. |
| 11:39:49 | Previous WSL boot's journal ended at system poweroff. There is no collector `shutdown_complete` for this session; cleanup did not release the writer lease. The exact Windows/WSL shutdown trigger is not established. |
| 11:40:49–11:41:51 | New boot attempted collector startup three times. Each failed in `Store.acquire()` with `UNIQUE constraint failed: leases.key` and `A writer owns this database`. |
| 11:42:21 | systemd's three-starts-per-five-minutes limit stopped further retries. |

At investigation time the collector was `failed`, with `MainPID=0`. The dashboard
was active as PID 290 with `--no-collect`. Process inspection found no other BTC15
collector. The remaining `collector` lease owner is
`6ef04b33-a83c-46a7-bd54-1c7644d84ea0`. It was left unchanged.

## Reproduced drain defect

`Engine.ingest()` calls `invalidate(..., "collector_overload")` when analysis first
becomes suspended. `invalidate()` clears `_collector_book_sids` and invalidates
the books before the queued old-connection deltas have been applied. The next
delta then fails the subscription-identity check even though its subscription and
sequence are correct. Each rejected delta adds a durable health record and another
log message, increasing processing work while the queue is supposed to drain.

The actual recording confirms the transition:

- Source tape: `data/raw/610f2428-415d-4711-b973-47877f489f9f.jsonl.gz`.
- Connection: `fac16fd6-82ab-4a16-b2ef-470d85862186`; subscription 3.
- The book for `KXBTC15M-26SEP110245-45` had an earlier snapshot on that connection.
- Row 86,596: delta sequence 75,666, analysis not suspended.
- Row 86,597: delta sequence 75,667, analysis suspended, received 02:32:49.635315.
- The first recorded subscription-mismatch error has exactly that receipt time.
  The later overload request time reflects the processing delay, not reordered
  receipt timestamps.

A three-frame isolated reproduction on the deployed source accepted a snapshot
and ordinary delta (book quantity 10 to 12), then rejected the contiguous delta
that began draining. Subscription identity changed from 3 to absent; quantity
remained 12 instead of becoming 14. No exchange connection was involved.

Relevant code: `src/btc15/engine.py:246`, `:267`, and the delta subscription check
in `:419`. This violates the intended ordered-drain behavior. It is a regression
in the recovery implementation, not evidence of a venue sequence gap.

Earlier tests missed this transition: the suspended-analysis test begins with a
snapshot already inside drain mode, and the overload integration test floods
ticker messages before reconnecting to a snapshot. Neither tests a populated,
sequenced book entering drain mode on an ordinary delta. Replay equality also
cannot prove correctness when both paths reproduce the same defect.

The immediate overload trigger still warrants measurement after removing this
error amplification. This investigation does not attribute all pre-drain cost to
one strategy function or establish long-run memory stability.

## Why the dashboard reports stale status

The API reads the latest collector heartbeat stored in `market_display` under
`status:settlement-convergence-v8`. That heartbeat is from 11:39:47.702 and is no
longer advancing. The freshness threshold is five seconds. Refreshing the page
does not create new collector status.

The independently running viewing dashboard does not receive the standalone
systemd collector's startup exception. Consequently `collector_startup_error`
is null and the operational panel reports `STALE_STATUS`/unknown entry readiness,
rather than the specific writer-lease failure. Its old DRAINING state and queue
metrics must not be interpreted as current processing.

## Preserved state and limits

- Both primary and shadow databases passed SQLite `quick_check`.
- Both saved checkpoints have zero open positions and zero reserved exposure.
  Primary risk accounting remains present; no reset was performed.
- The full 1,015,162,918-byte gzip tape decoded successfully (5,091,229,489 bytes).
  This establishes compression integrity, not complete capture of every frame
  received before shutdown. Its tail is around 11:39:38.557 receipt time, while
  the final worker status still reported queued work.
- Existing completed trade results remain queryable. This was not a complete
  trade-by-trade reconciliation or a certification that no queued input was lost.

## Required next work

1. Preserve the old book's subscription/sequence continuity while blocking entries
   and draining queued deltas. Invalidate for the replacement generation after
   the old stream is drained. Keep genuine sequence-error handling fail-closed.
2. Add the established-book-to-drained-delta regression and replay actual overload
   intervals, then measure drain cost and latency again.
3. Address interrupted shutdown and dead-owner lease recovery without bypassing
   single-writer exclusion. Before a later start, inspect/recover the orphaned
   lease with the collector confirmed absent and preserve the existing run.
4. Expose standalone startup failures to the dashboard, so an orphaned lease has
   an explicit operational reason instead of only a stale heartbeat.

Simply restarting currently repeats the lease failure; removing the lease alone
does not fix the overnight drain defect. No restart or production repair was
performed as part of this investigation.
