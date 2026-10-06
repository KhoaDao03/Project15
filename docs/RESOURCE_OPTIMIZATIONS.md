# Resource optimizations — staged, not deployed

Deployment update: these changes were deployed on 2026-10-05 UTC. See
[deployment verification](RESOURCE_DEPLOYMENT.md). Staging status below is historical.

Checkout: `/root/Project15-resource-opt`, branch `perf/resource-optimizations`.
Extended verification and the history-reader cleanup are documented in
`RESOURCE_REVALIDATION.md`.
No production database migration, deployment, service restart, or process shutdown
was performed. The existing development/editor sessions remain running.

This checkout carries forward the previously staged dashboard and execution-loop
work along with the existing local changes. The standalone resource patch is
scoped to the optimizations below and the preceding execution-query optimization;
it excludes unrelated configuration, strategy, and dashboard edits.

## Changes

1. **Indexed history reads.** A case-insensitive ticker expression index makes
   each asset's prefix query targeted. Explicit rowid ordering preserves the
   previous traversal order, including equal-time orders.
2. **Per-asset history revisions.** Transactional SQLite triggers invalidate the
   affected asset's fallback history when its accounting/history fields change.
   Other assets, control updates, and diagnostic-only updates do not invalidate
   it. Fill corrections, fees, settlements, reset markers, deletes, and moving
   orders between assets remain visible. REPLACE is covered even when recursive
   triggers are disabled. Local paper-history revisions still participate.
   Readers fall back to database-wide invalidation on unmigrated databases or
   if required triggers are missing. The global loss guard uses the same history
   projection; an integration regression verifies that accounting corrections
   still refresh its totals.
3. **Targeted reconciliation controls.** Pending orders fetch only their market
   controls. The per-order reread inside the market lock remains intact, as do
   missing-control handling, processing order, and manual-origin reconciliation.
4. **Retire settled derived caches.** Successful, obligation-free settlements
   release model features, lead history, decision keys, quote inputs, management
   gaps, and evaluation bookkeeping. Positions, active orders, and quarantines
   prevent cleanup. Keep the last visible evaluation until a newer one replaces
   it. Authoritative books, contract identities, research linkage, and database
   history remain available for late/conflicting settlement evidence. This bounds
   the settled derived state; it does not promise constant total process memory.
5. **Reuse identical event features.** Markets evaluated in one engine event
   share one feature calculation for that event's reference ticks, clock, and
   configuration. Each receives a deep copy before market-specific decoration.
   No features are reused across events. Numerical-equivalence cases cover seeded
   models, gaps, duplicate ticks, insufficient data, and reversed event clocks.

The prior `sync_markets()` / `cycle_controls()` query optimization is included.
See `EXECUTION_CONTROL_QUERY_OPTIMIZATION.md` for its original validation. Its
statement that reconciliation remained unchanged describes that earlier patch;
item 3 above now optimizes reconciliation too.

## Validation

- Broad regression suite: **372 passed, one failed**. The failure,
  `test_processing_safety.py::test_reference_display_continues_while_analysis_is_blocked`,
  reproduces with the preceding checkout's source: `reference_5hz` is `None` in
  that test's old-timestamp fixture. It is not introduced by this patch.
- After refining diagnostic-only invalidation: **85 passed**, covering manual
  trading, fallback history, reconciliation, targeted controls, and global loss
  guard behavior.
- Final feature/cache and accounting regression run: **19 passed**. Test-run
  counts overlap; they are not a total of unique tests. Ruff and whitespace
  checks pass.
- All tests and benchmarks use temporary databases and synthetic/mocked data.
  No trading or production database writes occur.

Reproduce the offline measurements:

```bash
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  nice -n 15 /root/Project15/.venv/bin/python scripts/benchmark_resource_optimizations.py
```

Results: `reports/resource-optimization/benchmark.json`. These are synthetic
component measurements, not whole-bot or VPS savings:

| Measurement | Before | After |
| --- | ---: | ---: |
| Seven history prefix reads, 8,694 orders, median CPU | 151.967 ms | 43.615 ms |
| Three identical feature calculations, median CPU | 19.284 ms | 6.409 ms |
| One committed order update, median CPU | 0.679 ms | 0.820 ms |
| One committed order update, median wall time | 2.110 ms | 2.769 ms |
| Tracked Python allocations for 300 retired market caches | 803,748 bytes | 26,606 bytes |

History-read CPU fell about 71%; feature CPU fell about 67% in these workloads,
with identical outputs. Cleanup released about 97% of the tracked derived-cache
allocations, not 97% of process RAM. Per-asset invalidation savings depend on write
and viewer activity and are not yet measured in production.

The index and triggers add write work: approximately 0.141 ms CPU and 0.659 ms wall
time per synthetic committed update here. The deployed workload must be observed
before claiming an end-to-end latency improvement. Trading thresholds, polling
cadence, and risk checks are unchanged.

## Deployment is held

Standalone patch: `/root/Project15-resource-opt.patch`. It includes the earlier
execution query optimization, so it supersedes the execution-only patch for
these files; do not apply both blindly. It does not deploy itself. Recheck its
applicability against any intervening production edits before the user-authorized
deployment. Keep unrelated working-tree changes intact.

The new index, revision table, and triggers are created at `ManualTrading`
initialization after a future authorized deployment/start. Index construction
uses a database write lock, so account for it during the deployment window; it
has only run against temporary test databases so far. Read-only consumers do
not perform schema changes and continue to work against the old schema.

Rolling back to the previous code can leave the added index, table, and triggers
in place without losing data. The triggers would still impose write overhead;
removing them is a separate, deliberate migration, not part of this held change.
