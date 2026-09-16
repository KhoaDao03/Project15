# Collector diagnosis — September 11, 2026

The held-position replay identifies per-quote portfolio snapshots in the new
profit-value exit check as the first targeted performance fix. This investigation
did not deploy a fix, restart services, or change the live ledger/configuration.

## Deployment and incident evidence

The collector service runs from `/home/devk/Project15/.venv`, importing this
checkout's `src/btc15` package. Its command selects `settlement-convergence-v8`,
`data/runtime/settlement-convergence-v8.json`, and `--stop-confirmation-shadow`.
The dashboard runs separately with `--no-collect`. Both select PAPER mode with
live trading disabled. The observed configuration hash is `75d8de22c3ccaf5f`.

HEAD is `f437414`, with pre-existing uncommitted engine, execution, shadow,
configuration, documentation, and test changes. Relevant source modification
times precede the collector's 01:14:57 EDT start and the dashboard's 01:08:51
start. Service commands, import resolution, timestamps, and reported config
agree with this deployment; no running-process bytecode attestation is available.
File hashes are retained in the local artifacts below.

The service journal reports queue-overflow failures at 01:12:06, 01:13:26, and
01:14:27 EDT, followed by automatic restarts. The capacity remains 20,000.
Two completed incident tapes contain:

| Tape prefix | Events | Receipt interval | Mean events/s | Peak one-second count |
| --- | ---: | ---: | ---: | ---: |
| `2b6dc85d` | 31,016 | 29.35 s | 1,057 | 3,976 |
| `7d873628` | 40,996 | 48.89 s | 839 | 2,168 |

These are captured receipt rates, including shutdown events, rather than exchange
production rates. A queue absorbs bursts only if processing subsequently catches up.

## Measurements

The existing 13,251-event authentic burst fixture was replayed into temporary
primary and shadow databases, with compressed recording enabled. Its historical
held checkpoint was copied and assigned the current configuration hash **only in
an explicitly constructed load scenario**. The shadow was seeded with the same
exposure. This is not an exact replay of the v8 incident or evidence of trading
performance. Production restore compatibility checks were not changed.

| Unprofiled scenario | Elapsed | Events/s |
| --- | ---: | ---: |
| Current v8 settings, primary plus shadow | 13.08 s | 1,013 |
| Same load, profit-value exit disabled in isolated control | 5.60 s | 2,366 |

The control is a diagnostic behavior change, not a deployable fix or an assertion
of result equivalence. Its approximately 2.3x speed difference supports targeting
the profit-value path. Both runs use a low scheduling priority on the bot's host;
absolute rates can vary with host contention and fixture characteristics.

The separate cProfile run took 53.47 seconds; profiling overhead makes that rate
unsuitable as a capacity measurement. Cumulative timings were:

| Path | Time | Calls |
| --- | ---: | ---: |
| Primary ingest, including strategy and position work | 27.11 s | 13,251 |
| Shadow processing, including shadow position work | 26.03 s | 13,251 |
| Primary and shadow position `monitor` combined | 39.85 s | 24,026 |
| Atomic wrapper across decorated operations | 38.65 s | 24,028 |
| `deepcopy` across the replay | 33.33 s | 156,179 outer calls |
| Features | 0.114 s | 42 |
| Probability | 0.489 s | 84 |
| Compact raw append, including its flush work | 0.258 s | 52 |

Rows are nested and must not be added. In particular, position work belongs to
both primary and shadow timings. `_profit_value_signal` executes 24,026 times.
Its body accounts for only 0.443 seconds cumulatively; its `@atomic` wrapper
copies the entire portfolio before checking whether any confirmation state changes,
opens a transaction, and constructs another snapshot afterward. This restores
expensive per-quote work ahead of the existing unchanged-quote fast return.

The receive coroutine and processing worker are already separated, and the primary
probability model already caches results. The processing worker nevertheless runs
recording, engine ingest, shadow processing, and display/status publication serially.
Thus a separate HTTP service does not remove shadow or publication costs from the
collector. Publication cost was inspected in code but is not separately timed by
this fixture.

Thirty one-second rounds of read-only HTTP requests measured median/max latency:
health 2.99/7.81 ms, evaluation 5.47/8.08 ms, strategies 8.01/9.84 ms, and trades
6.71/26.72 ms. During that sample, processing lag ranged from 0.004 to 3.00 seconds,
queue depth from 0 to 1,431, and reference age up to 3.41 seconds. That sample
overlapped isolated replay on the same host; it cannot establish baseline lag.
The endpoints were responsive in this sample, but it does not prove absence of
database contention or measure all dashboard work.

After replay ended, a separate 20-second sample measured median/max processing
lag of 11/786 ms, queue depth of 3.5/607, and reference age of 0.824/1.304 seconds.
The collector retained PID 234638 and restart count 3. Service memory was 82.9 MiB
with an 83.2 MiB peak. These brief observations do not establish memory stability
or sustained capacity.

## Next targeted change and acceptance criteria

Separate read-only profit-value decision calculation from committing changed
confirmation state. Retain atomic checkpoint/rollback protection for real changes,
and share/reuse calculations only when all relevant inputs match. Apply equivalent
care to the shadow path. Do not simply remove transaction protection or disable
the live feature. Preserve quote sequencing, reference confirmations, exits,
positions, and risk state.

Before deployment, compare identical-config portfolio, execution, and audit results;
test failed-checkpoint rollback and unchanged-quote behavior; repeat these timings;
then test an exact recent incident fixture with compatible initial primary/shadow
state. The current diagnostic replay's profiled and unprofiled runs already match
primary portfolio, execution, and retained audit results. Shadow processing passed
its failure check, but shadow result equivalence is not asserted by this harness.
Ruff lint and format checks passed for the harness extension.

## Local evidence and reproduction

Local artifacts are under `data/runtime/collector-profile-20260911/`: deployment
hashes, incident traffic counts, live HTTP samples, a second sample without replay,
the explicitly constructed checkpoints/configuration, timing JSON, and cProfile
statistics plus extracted function timings. These are local research artifacts,
not bundled fixtures. The only executable change is two optional benchmark flags:
`--shadow` and `--profile-output`.

```bash
nice -n 15 .venv/bin/python scripts/check_collector_throughput.py \
  --fixture data/runtime/throughput-burst.json \
  --checkpoint data/runtime/collector-profile-20260911/held-v8-load-scenario.json \
  --config data/runtime/settlement-convergence-v8.json \
  --shadow \
  --output /tmp/collector-timing.json
```

Add `--profile-output /tmp/collector.pstats` for call profiling. Do not use its
instrumented throughput as the unprofiled processing rate. No endurance validation
or recovery implementation was performed in this diagnostic step.
