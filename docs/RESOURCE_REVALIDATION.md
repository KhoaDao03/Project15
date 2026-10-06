# Resource optimization revalidation

Deployment update: these changes were deployed on 2026-10-05 UTC. See
[deployment verification](RESOURCE_DEPLOYMENT.md). Staging status below is historical.

Status: isolated checkout only; deployment remains on hold. No service was
stopped/restarted and no production database was migrated or modified.

## Findings

The extended checks found no new trading/accounting regression. They did expose
a resource-lifetime problem in the existing fallback reader: Python's SQLite
connection context manager commits/rolls back but does not explicitly close the
connection. Repeated history rebuilding/reopening under WAL retained additional
file descriptors. The staged reader now uses `contextlib.closing` to release
each short-lived history connection deterministically. The concurrent WAL test
failed before this correction and passes afterward. The persistent revision
reader retains its existing lock and explicit shutdown handling.

This correction does not change SQL, result ordering, trading policy, or polling
cadence. Only temporary databases were involved in testing it.

## Evidence

- Extended regression run: **503 passed, one failed**, in 96.75 seconds. Coverage
  includes execution, one-trade constraints, positions, reconciliation, loss
  guards, writer leases, stalled-stream recovery, recorder backpressure,
  settlement recovery/restarts, dashboards, and the optimization regressions.
- The sole failure is
  `test_processing_safety.py::test_reference_display_continues_while_analysis_is_blocked`.
  A fresh run against `/root/Project15-execution-query-opt/src` reproduces the same
  `reference_5hz is None` failure. This fixture sends a timestamp of 1 millisecond
  since epoch. It remains a pre-existing failure, not a passing validation case.
- Added five bounded endurance/crash cases. Together with accounting revision
  tests, **11 passed** after the reader correction:
  - Three simultaneous readers and 300 committed writes, separately with DELETE
    and WAL journals; final history matches the final committed quantity and
    `PRAGMA integrity_check` returns `ok`.
  - Reopening during concurrent reads plus 100 additional close/rebuild cycles;
    file-descriptor counts stay bounded after garbage collection.
  - Abrupt subprocess exit before and after commit: order values and asset
    revision counters recover together; reader reopening preserves the result.
  - 10,000 derived-cache retirements with delayed duplicate cleanup: six derived
    dictionaries remain empty and one latest result remains. This exercises
    cleanup, not 10,000 full end-to-end live settlements.
- Final post-fix dashboard/accounting suite: **71 passed**, recorded in
  `reports/resource-optimization/post-fix-tests.txt`.
- Ruff and whitespace checks pass.

Raw logs are in `reports/resource-optimization/`. The broad run preceded the
explicit connection-close fix; the post-fix run covers its affected consumers.
Test-run counts overlap and must not be added as unique tests.

## Performance and limits

Repeated offline benchmarks on synthetic data still return identical read
bodies/order and identical feature values:

| Component | Before CPU | After CPU |
| --- | ---: | ---: |
| Seven asset history reads, 8,694 orders | 168.450 ms | 57.637 ms |
| Three identical feature calculations | 27.334 ms | 9.233 ms |
| Committed order update | 0.685 ms | 0.810 ms |

That is about 66% less CPU for each of the first two workloads, not whole-bot
CPU. Synthetic order writes add about 0.125 ms CPU here. Wall-time measurements
vary with VPS scheduling and storage activity.

A separate 100-sample interleaved single-market feature comparison measured
7.074 ms before and 7.592 ms with the new deep copy (about 0.518 ms extra CPU).
There is no reuse benefit when only one market needs calculation. The measured
multi-market benefit must not be described as a speedup for every event.

Tracked allocations for 300 retired derived caches remain 803,748 bytes before
versus 26,606 bytes after. Books, contract identities, research links, unresolved
obligations, and durable history remain retained; this is not proof of bounded
whole-process RAM or disk usage. Signal-only engines that deliberately skip
settlement do not run settlement cleanup; the live-signals engine enables its
settlement path. Existing lifecycle semantics were preserved.

These checks improve confidence in continuous operation but are **not an actual
24-hour soak**, exhaustive fault injection, or a guarantee of uninterrupted
operation. They do not test a real exchange outage, disk exhaustion, host reboot,
or production end-to-end order latency. The repository's full-suite check and
live observation remain necessary around a future authorized deployment.

During that deployment, observe order/exit latency, cycle lag, database lock
errors, restart counts, RSS/file descriptors, and disk growth through multiple
market transitions. Keep deployment on hold until explicitly authorized.
