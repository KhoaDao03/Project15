# BTC and ETH collector throughput — 22 September 2026

The executor's generic “Collector health blocks automatic entry” message was
caused by processing backlogs. Rejection traces included `COLLECTOR_RECOVERING`,
`BACKLOG_WARNING`, and, during larger bursts, `PROCESSING_LAG` and
`STALE_REFERENCE`. Both services were running; the problem was keeping up with
incoming events, rather than a stopped service.

A 15-second BTC stack sample attributed 314 of 975 CPU-owning samples to strategy
processing and 196 to research serialization/admission (overlapping categories).
The collector repeated entry checks and serialized their results on every depth
update, even when executable prices and entry eligibility were unchanged.

## Change

Only signal collectors suppress redundant depth-triggered entry checks. They still
apply, validate and record every received frame, including exact quantities and
sequence numbers. Best prices, liquidity below the larger of maximum order size
and minimum liquidity, freshness, health gates, fees and entry-window changes
remain comparison inputs. Official reference updates, snapshots and the regular
one-second evaluation schedule still trigger checks. Paper order/position
processing and the live executor's preflight are unchanged.

No health threshold, strategy parameter, order size, loss guard or checkpoint hash
was relaxed or rewritten. The exact task patch is [engine.patch](engine.patch).

A second defect prolonged some health blocks after processing had recovered:
the executor reads the status projection, which was only refreshed once per second,
while recovery transitions were already written to a separate journal. In the
five-minute throughput-only observation, 23 of 75 blocked BTC samples had a newer
READY transition with no warning. The runner now publishes status in the same batch
as **either** a new block **or** its clearance. The thresholds are unchanged.
See [the status patch](runner.patch) and [stale-status evidence](stale-health-evidence.json).

## Offline evidence

| Synthetic 5,000-event scenario | Before | After | Book/input result |
| --- | ---: | ---: | --- |
| Mostly deep updates, first pass | 1,782 events/s | 3,007 events/s | Same final depth; zero recording drops |
| Excess quantity at best price, final refinement | 1,638 events/s | 3,064 events/s | Same final depth; zero recording drops |

These benchmarks use a deterministic model output, temporary databases and real
input/processing recording. They measure the repeated-check path, not market
profitability or worst-case live capacity. See [first benchmark](benchmark-first-pass.json)
and [top-quantity benchmark](benchmark-top-quantity.json).

The final full suite passed **1,828 tests with 3 skipped**, in 425 seconds.
The final refinement also passed 42 focused tests covering full input capture,
below-threshold liquidity changes, freshness failures, entry boundaries, cached
models and paper behavior. Ruff and whitespace checks passed. Full suite output
is in [tests-final.txt](tests-final.txt).

After the small status-publication change, collector/recovery/operation tests passed
89 tests (1 skipped), and live executor/research/display integration tests passed
143 tests (1 skipped). The new same-batch publication regression fails against the
old runner with a missing transition status and passes with the fix. See
[collector tests](status-tests.txt), [integration tests](status-integration-tests.txt),
and [pre-fix regression](status-regression-before.txt).

## Live rollout and observation

The first pass was deployed to ETH at approximately 02:29:41 UTC and BTC at
02:29:51 UTC. Each restart used the executor's asset-specific shutdown guard,
which checks managed exposure and unresolved orders before disabling entries.
Previous policies were restored after collector health passed: 10 contracts,
unchanged config hashes and stop prices. Other collectors and the shared executor
were left running. Captured source snapshots confirmed the loaded engine change.

The initial 45-second sample, taken while regression tests also consumed CPU,
contained 59 blocked BTC observations out of 90; ETH had 0/90. After the first
pass, a three-minute sample contained 14/360 blocked BTC observations and 0/360
for ETH. BTC median lag was 3.3 ms, p95 397 ms, maximum 1.15 s; ETH median lag was
2.3 ms, p95 12 ms, maximum 162 ms. These are polling observations, not missed trade
counts, and the windows are not a controlled live benchmark.

The remaining BTC bursts motivated the excess-top-quantity refinement. The final
throughput version started on BTC at 02:46:26 UTC and ETH at 02:46:31 UTC, using the same guarded
restart and policy-restoration procedure. Both loaded source snapshots match the
tested engine. [Deployment evidence](deployment.json) confirms unchanged policy
settings and BTC/ETH sigma multipliers (0.8 and 1.1 respectively).

That version's five-minute observation had 75/600 blocked BTC health samples and
0/600 for ETH. BTC median lag was 8.5 ms, p95 775 ms, maximum 2.03 s; ETH median lag
was 2.4 ms, p95 16 ms, maximum 313 ms. The windows vary in market traffic and CPU
load, so the synthetic benchmarks, not these different live windows, establish
the processing speedup. The remaining stale-status observations led to the
additional publication fix. The throughput repair alone did not eliminate BTC's
burst-related health pauses.

The status-publication fix was deployed after the existing resting exits cleared:
BTC restarted at 02:59:27 UTC and ETH at 02:59:32 UTC. Attempts while those exits
were outstanding were correctly refused by the shutdown guard; the services and
position management continued running. Both original entry policies were restored
after health checks. [Final deployment evidence](status-deployment.json) verifies
both `engine.py` and `runner.py` against the running processes' source snapshots.

The final five-minute observation included market rollover:

| Asset | Samples | Backlog-related blocks | No-active-market blocks at rollover | Blocked after an already-recorded recovery |
| --- | ---: | ---: | ---: | ---: |
| BTC | 600 | 31 | 94 | 0 |
| ETH | 600 | 2 | 72 | 0 |

BTC median processing lag was 6.4 ms, p95 508 ms and maximum 1.19 s; ETH median was
2.2 ms, p95 24 ms and maximum 743 ms. At the final check both collectors were READY,
with no backlog warning, fresh status/decisions and a one-second maximum reference
gap. Strategy rejections were ordinary entry-window/price/probability checks.
The final observation therefore verifies the status-clearance repair and current
health, **not elimination of all genuine burst-related entry pauses**. These are
sampled health checks, not counts of otherwise-eligible trades missed.

See [final health samples](status-health.json), [reason breakdown and final state](status-summary.json),
and [the throughput-version CPU profile](btc-final-profile.txt).

Existing portable-recorder `OperationalError` capture failures also occurred after
restart. They are recorded as gaps rather than hidden; this change does not claim
to repair that separate recording issue. The restart's reference gap cleared
through the existing five-minute continuity window, without synthetic backfill.

Evidence: [before health](before-health.json), [first-pass health](after-health.json),
[before profile](btc-before-profile.txt), [first-pass profile](btc-after-profile.txt).
