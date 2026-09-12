# Profit-value processing improvement — September 11, 2026

Developed and validated in `/home/devk/Project15-processing` on
`fix/profit-value-processing`, then transferred to `/home/devk/Project15` at the
operator's request after both services were confirmed inactive. The affected
original files still matched the saved baseline before transfer. Existing
uncommitted work was preserved. No service was started or restarted, and live
configuration was not changed. The improvement will load on the next authorized
start.

Post-transfer verification in the original checkout passed all 45 focused tests
in 7.89 seconds, plus lint and whitespace checks. The transferred Python files
matched the tested development copy byte-for-byte, and unrelated original files
matched their saved baseline.

The development measurements and test artifacts referenced below remain under
`/home/devk/Project15-processing/data/runtime/processing-fix/`.

## Change

Profiling identified the profit-value exit's per-quote atomic wrapper as the
dominant avoidable cost. `_profit_value_signal` now computes the decision without
mutating positions, then calls an atomic setter only when confirmation count or
reference actually changes. Nonqualifying quotes that need no reset and repeated
quotes with unchanged confirmation state avoid snapshots and transactions.

Every quote still checks current probability, freshness, executable depth, fees,
and consumed liquidity. No input updates or risk/exit checks are skipped. Changed
confirmation state retains full checkpoint/rollback protection. The shadow
executor inherits the same path. Configurations, checkpoint format, confirmation
rules, exit prices, and reference sequencing remain unchanged.

The existing burst harness now retains and compares shadow snapshots and records,
in addition to primary state, execution records, and retained audit records.

## Validation

New tests exercise primary and shadow executors, forbid snapshots/transactions
for unchanged qualifying and nonqualifying quotes, inject checkpoint failures
after writes for first/second confirmations, resets, gaps and same-second reference
changes, and verify depth/probability changes are rechecked within one reference
second. Existing profit-value tests cover both contract sides, partial exits,
committed intents, restore, timeout, and safety exits.

The focused exit, shadow, processing, and rollback run passed all 45 tests
(`data/runtime/processing-fix/focused.xml`). Ruff checks for `src` and `tests`,
format checks for changed Python files, and `git diff --check` passed.

Broader regression sweeps reached their 240/300-second limits in the slow strategy
reverification cases. Their only reported failure was
`tests/test_entry_fill_experiment.py::test_original_hash_and_opt_in`. Running that
test against the saved, unchanged baseline reproduced the same failure: its
expected legacy hash omits other optional settings but does not remove the
already-existing `profit_value_exit_enabled` field. This performance change does
not modify configuration hashing, and the unrelated test was left unchanged.
Remaining strategy/dashboard files were run separately rather than repeating the
completed prefix. Test-only numerical-library thread limits and low scheduling
priority do not change service environment or benchmark settings.
All 98 tests in the remaining strategy/dashboard files passed in 120.61 seconds;
their complete result is `data/runtime/processing-fix/remaining-regression.xml`.

The authentic 13,251-event historical burst is used with the previously documented
v8 held-position load scenario. Before and after use identical settings with
profit-value exits enabled and both primary and shadow processing. This is an
isolated load scenario, not an exact reconstruction of the latest incident or
evidence of sustained capacity. Baseline modules, fixtures, and JSON results are
retained locally in `data/runtime/processing-fix/`.

First timings were 14.75 seconds before (898 events/s) and 6.26 seconds after
(2,116 events/s). Both primary and shadow snapshots and records matched. Those
measurements overlapped focused tests on the shared host; use the later sequential
timings below for the final comparison. No profiler was enabled during timings.

Final sequential timings, with no test process running alongside the benchmark:

| Same configuration and captured inputs | Before | After |
| --- | ---: | ---: |
| Elapsed | 16.25 s | 6.58 s |
| Events/second | 816 | 2,014 |

This is a 2.47x throughput improvement. Primary portfolio, orders/fills/results,
retained primary audit records, shadow portfolio, and shadow records matched
exactly. Results are `before-final.json` and `after-final.json`. Rates vary with
host load and are still below the recent 3,976-event one-second receipt peak;
this fix does not replace backlog and recovery safeguards.

## Reproduce using retained local artifacts

From `/home/devk/Project15-processing`, select its source explicitly because the borrowed Python
environment has an editable installation pointing at the running checkout:

```bash
PYTHONPATH=src nice -n 15 /home/devk/Project15/.venv/bin/python \
  scripts/check_collector_throughput.py \
  --fixture data/runtime/processing-fix/burst.json \
  --checkpoint data/runtime/processing-fix/checkpoint.json \
  --config data/runtime/processing-fix/config.json \
  --shadow --output /tmp/processing-after.json \
  --compare data/runtime/processing-fix/before.json
```

For the baseline, use `PYTHONPATH=data/runtime/processing-fix/baseline-src` with
the same command and a different output path. Both write only temporary ledgers.
This uses installed dependencies without modifying the running environment.

The improvement does not establish 24/7 readiness: overload recovery, exact recent
incident reconstruction, and a monitored endurance session remain separate work.
Starting the services remains a separate operator decision. No overload recovery
implementation is included in this change.
