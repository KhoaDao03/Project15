# Execution control query optimization — deployment held

Deployment update: these changes were deployed on 2026-10-05 UTC. See
[deployment verification](RESOURCE_DEPLOYMENT.md). Staging status below is historical.

Prepared October 4, 2026 in `/root/Project15-execution-query-opt`, an isolated
checkout of the current working source. Do not deploy or restart services until
the user explicitly requests it. The active checkout's execution module and the
production database were not modified by this task. Existing, unrelated working
changes were copied into the isolated checkout for test compatibility; they are
not part of the optimization patch.

## Change and behavior

`sync_markets()` looks up only the tickers published by each enabled asset's
current collector. Existing controls retain their switches, revision, pause
state, quantity, and other fields. Wrong-run snapshots, disabled policies, and
expired markets still cannot create new controls.

`cycle_controls()` uses an expression index on asset, descending close time, and
descending ticker to retrieve the five newest controls per recorded asset.
Assets are enumerated from the controls index, not only configured fleet members,
so legacy assets remain represented. Only that small result is sorted in Python
to reproduce the original global processing order.

The existing close-time index supplies unexpired controls. Order reads retain
the existing bot-origin filters and descending journal order. Older unresolved
obligations use primary-key control lookups; no unresolved market is dropped
because it falls outside the five-market window. Decimal net-fill arithmetic,
expiry checks, and stable exit-first ordering are unchanged. No history is
removed, no polling interval is changed, and no authorization is cached.

Recent and active control reads share a short read transaction. It ends before
opening separate order readers, avoiding a nested-reader lock hazard in SQLite
rollback-journal mode. Older unresolved control lookups read current rows. As
before, selection does not authorize an order: per-market processing rechecks
current control state. Concurrent commits can become visible between these
reads; the function does not claim an atomic snapshot of controls and orders.

The new `live_controls_asset_recent` index is created with `IF NOT EXISTS` when
`LiveAutomation` initializes after a future authorized deployment/start. It has
only been built in temporary test databases so far. Other full-history paths,
including initialization and reconciliation when pending orders exist, remain
unchanged; this is not a claim that the whole executor now avoids history scans.

## Validation

- 26 new selection/query regression cases pass, including 20 seeded mixed-history
  comparisons against the original implementation, exact processing order,
  timestamp ties, legacy assets, disabled/paused controls, expired unresolved
  orders, netted partial fills, missing controls, expiry between clock reads,
  query plans, bounded JSON decoding, and releasing the control reader before
  separate journal operations.
- A synthetic history with 9,183 controls (9,176 archived and seven active) decodes
  42 controls per cycle when there are no older unresolved obligations.
- The initial broader run passed 462 tests and failed four existing BTC entry
  probability cases. All four fail identically with the saved original module;
  those cases expect 0.83 while the current BTC early threshold is 0.85.
- The additional stop/resting-order run passed 60 tests and failed three existing
  resting-stop cases. All three also fail identically with the original module.
  The cases use a 0.55 bid for their expected stop. These tests and strategy
  configuration were not changed as part of this optimization.
- Ruff and whitespace checks pass. Async suites ran outside the sandbox, using
  temporary databases and mocked exchanges. No live exchange requests were made.

Commands used (from the isolated checkout; the existing environment supplies dependencies):

```bash
PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /root/Project15/.venv/bin/python -m pytest -q \
  tests/test_live_control_queries.py tests/test_live_cycle_window.py \
  tests/test_live_automation.py tests/test_manual_trading.py tests/test_execution_service.py

PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /root/Project15/.venv/bin/python -m pytest -q \
  tests/test_live_control_queries.py tests/test_live_cycle_window.py \
  tests/test_stop_worker.py tests/test_resting_take_profit.py tests/test_hard_stop_only.py

PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  nice -n 15 /root/Project15/.venv/bin/python scripts/benchmark_control_queries.py
```

## Synthetic benchmark

Thirty interleaved, warmed samples against one temporary database: 9,176 controls,
seven active markets, 21 order records, seven old unresolved orders. Both versions
select the same 42 controls in the same order. Median process CPU time:

| Function | Original | Optimized |
| --- | ---: | ---: |
| `cycle_controls()` | 74.248 ms | 23.529 ms |
| `sync_markets()` | 67.596 ms | 2.441 ms |
| Sum of medians | 141.844 ms | 25.970 ms |

This is about 82% less CPU time for these two functions combined. It is not a
measurement of whole-executor or VPS CPU savings. The synthetic index build took
27.323 ms; production startup cost can differ. Exact results and source hashes
are in `reports/execution-query-optimization/benchmark.json`.

## Future deployment

The standalone patch is `/root/Project15-execution-query-opt.patch`. It contains
only the execution module change, new regression tests, offline benchmark, and
this document. It has been checked for applicability to the active working tree
without applying it. Recheck against any intervening edits before deployment.

No deployment approval is implied by preparing this patch. After the user asks
to deploy, reconcile all intended staged changes, account for live positions and
unresolved orders using the project's normal operational procedure, and validate
service health after the authorized restart. Do not stop collectors merely to
apply this execution-only patch. Rolling back the code can leave the additional
index in place; no data deletion or index removal is required.
