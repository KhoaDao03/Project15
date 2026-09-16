# Replay and failure recovery validation — September 11, 2026

Validated the current working checkout offline. No bot or dashboard service was
started or restarted. Tests use fake transports and temporary SQLite ledgers;
historical inputs and saved baselines are read only. This task adds regression
coverage and documentation, with no production-code or strategy-setting changes.

## Results

| Check | Result |
| --- | --- |
| Authentic 13,251-event burst with primary and shadow processing | Exact match against the saved pre-performance-fix baseline for both portfolio snapshots, execution records, retained primary audit records, and shadow records. |
| Injected overload | Old received frames recorded in order, analysis suspended while draining, replacement connection reaches READY, final queue depth zero. |
| Injected transport loss | Replacement snapshot and fresh metadata/reference restore READY; final queue depth zero. |
| Injected sequence gap | Gap detected; corrupted delta does not change depth; replacement generation restores READY from a sequenced snapshot. |
| Replay of each captured failure stream | Book state, subscription sequences, portfolio/risk snapshot, fills, and completed results match the isolated collector. |
| Three consecutive recovery cycles with held exposure | Position and full executor/risk snapshot unchanged; no spurious fill or result; missing fresh metadata keeps the gate closed. |
| Selected replay/recovery regression suite | 166 passed in 83.23 seconds; two existing dependency deprecation warnings. |
| Additional repeated-recovery regression | 1 passed in 0.21 seconds. |
| Static checks | Ruff, changed-test formatting, JavaScript syntax, and whitespace checks passed. |

The selected suite includes raw recording/Parquet round trips, complete synthetic
replay through fills and settlement, entry freshness, pending-buy blocking, shadow
execution, processing rollback, position settlement/restart reconciliation,
shutdown, disk-write failure, subscription denial, and operational status. It is
not a claim that the entire repository suite was run.

The new failure-stream tests are observation-mode simulations with no held position
or fills. Held exposure is covered separately by the historical replay, repeated
recovery test, and existing position/restart tests. Replay initializes the same
empty daily risk bucket that live status reporting initializes; no nonzero risk
values or accounting records are removed from comparisons.

## Historical replay measurements and limitations

The retained burst took **5.915 seconds**, approximately **2,240 events/second**,
with **106.7 MiB peak process RSS**. This is one short offline measurement, not a
memory stability test or a promise of sustained capacity. The burst retains one
held primary and one held shadow position and produces no new fills/results.
Its held-position setup is the previously documented load scenario, not an exact
reconstruction of the latest production incident or evidence of trading returns.

The failure simulations use a 32-message test queue to exercise production
threshold fractions quickly; the production capacity remains 20,000. They prove
the asserted recovery paths, not every real network, disk, or host failure. Hard
queue exhaustion and failed recording remain fail-closed conditions.

Local artifacts: `data/runtime/replay-recovery-validation/historical-burst.json`,
`regression-tests.xml`, and `repeated-recovery.xml` in that same directory.

## Reproduce

From the original checkout, with installed dependencies:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src .venv/bin/python \
  scripts/check_collector_throughput.py \
  --fixture /home/devk/Project15-processing/data/runtime/processing-fix/burst.json \
  --checkpoint /home/devk/Project15-processing/data/runtime/processing-fix/checkpoint.json \
  --config /home/devk/Project15-processing/data/runtime/processing-fix/config.json \
  --shadow --output /tmp/btc15-replay-check.json \
  --compare /home/devk/Project15-processing/data/runtime/processing-fix/before-final.json

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_collector_recovery.py tests/test_collection.py tests/test_shutdown.py \
  tests/test_storage_replay.py tests/test_compact_recording.py \
  tests/test_settlement_recovery.py tests/test_settlement_recovery_restart.py \
  tests/test_processing_safety.py tests/test_reference_entry_revalidation.py \
  tests/test_stop_shadow.py tests/test_profit_value_processing.py \
  tests/test_operational_state.py tests/test_operation.py
```

The combined command now includes the additional repeated-recovery test (167
cases). The retained XML reports reflect the two actual validation runs.

## Remaining acceptance

A monitored **24–48-hour paper session** still needs a later authorized start.
Measure queue/oldest-event lag and memory over time, automatic recovery transitions,
market rollovers, position/result continuity, and clean shutdown. Both services
remain stopped; this offline validation does not complete that endurance session.
