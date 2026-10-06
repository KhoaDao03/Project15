# Hourly ETH/XRP implementation report

Prepared on October 6, 2026, on `cloud-deploy`, starting from clean commit
`1976928`. No production service was restarted, no order was submitted, and no
hourly live-buy policy was enabled. Commits remain local.

## Behavior and files

- `assets.py`, `api.py`, `hourly.py`, `domain.py`: separate ETHD/XRPD identities,
  underlying reference feeds, verified above/below contracts and next-top-of-hour
  discovery. Actual public API fixtures are committed for both series.
- `bleep.py`, strategy config, `bleep_seed.py`, new presets: hourly multipliers
  1.00/1.10, premiums 6pp/10pp, 83% probability throughout the last 10 minutes
  excluding the last 60 seconds, 80–96¢ asks, 10 contracts, no stops/take-profit,
  no late path, and no blackout. Sustained-lead confirmation is inherited.
- `execution.py`, `engine.py`, `live_automation.py`, `manual_trading.py`: two-strike
  event cap in paper/live, including pending and partial fills, durable filled
  slots across restart, volume priority within a pass, event serialization and
  indexed journal reads. The existing global unresolved-order reservation and
  final authorization recheck remain in force.
- `runner.py`, `storage.py`, `hourly.publish_candidates`: each qualifying hourly
  strike gets `evaluation:<run>:<ticker>`. Lost qualification publishes an
  invalidation. A complete candidate index and decisions commit atomically.
  The executor cycles all indexed candidates, prioritizes first qualification
  then volume, and still rereads its own decision with the existing **2-second**
  decision/model freshness limits. Existing 15-minute publication is unchanged.
- Live fills carry signal ask, sent limit, exchange fill price, fill-minus-ask,
  seconds remaining, strike, event and model/capped probabilities in the durable
  order and immutable execution journal. Missing fill costs stay unknown.
- Series-based history and research prefixes, private routes, public server,
  viewer/performance lists, preview and preparation scripts include the new assets.
  Hourly results stay separate from 15-minute ETH/XRP results.

## Compatibility and rollout

All **196 existing preset/frozen configuration versions** match the pre-change
snapshot ([hashes](config-hashes-before.json), [verification](compatibility.json)).
The original 15-minute `parse_market` body is byte-for-byte unchanged. Its
rounding, ATR-finish formula, parameters, stops and blackout behavior are retained.
The optional `entry_limit_offset` defaults to `null` and is excluded from the hash
when null; a set value is validated and hashed. It only affects hourly live limits.

Hourly comparison uses the **unrounded** final 60-sample mean and strict `>`.
The one settlement-distribution boundary adjustment selects the exact strike for
these contracts; the 15-minute rounded boundary is unchanged. There are no changes
to the ATR-finish probability formula or the indicator lean. This small
contract-specific boundary adjustment is necessary to honor the explicitly
chosen hourly comparison precision.

The captured XRP API timer is **1800 seconds**, contrary to the task's 60-second
metadata example. Its rules still require a 60-second average, which the parser
uses. The exact captured secondary wording is validated and unknown wording
fails closed; a future wording change may require another reviewed fixture.

Both new presets and frozen `data/cloud/{ETHD,XRPD}.json` ship with
**`entry_limit_offset: null`**. Two rows were added to the existing runtime manifest
without changing its other ten rows; portable additions are tracked in
`deploy/cloud/hourly-manifest.json`. Frozen hourly versions are ETHD
`3efb5d90d16f9d9b` and XRPD `e546a5ad1fc8175d`. The runtime journal had **no ETHD/XRPD
live policies** when checked. Services and the isolated public installation await
the owner's deployment, followed by separate dashboard enablement at 10 contracts.
See [the go-live guide](../../docs/HOURLY.md).

The existing −$50 fleet guard includes hourly members automatically through
manifest membership and series-separated histories. Its implemented basis is
**all recorded dashboard realized P&L, latched**, not the task's description of a
daily reset. That existing policy and any latch were left unchanged.

## Resource measurements

Offline synthetic benchmarks use temporary databases, 3,600 reference ticks,
full event ladders and fresh evaluations plus per-strike publication. Seven
uninstrumented passes measure CPU/wall time; a separate traced setup/first pass
measures Python allocations. No production database or exchange order endpoint
is used.

| Ladder | Median wall | Median CPU | Maximum wall | Retained / peak traced allocation | Process peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| ETHD, 300 strikes | 610.11 ms | 600.60 ms | 693.83 ms | 6.93 / 7.01 MiB | 68.85 MiB |
| XRPD, 75 strikes | 177.82 ms | 176.72 ms | 227.45 ms | 2.91 / 3.57 MiB | 69.70 MiB |

At one full evaluation/second, this is about 60% and 18% of one CPU core,
respectively, for this measured work. RSS is the benchmark process high-water
mark, not a prediction of production collector size. Production networking,
book bursts, research logging and executor load are not simulated. These results
fit the evaluation/freshness budget, so **all current-event strikes remain
subscribed**. Monitor actual processing lag before enabling buys.

[Full-ladder measurements](hourly-benchmark.json),
[existing optimization benchmark](existing-benchmark.json).
The existing resource-use regression suite passed **13/13**. The existing
benchmark retained identical history/features and reduced its 300 retired-market
cache allocation from 803,748 to 26,606 bytes.

## Validation

Baseline full suite, before source changes:
**314 failed, 2,411 passed, 3 skipped**, 642.98 s.
All baseline failure IDs are retained in [baseline-failures.txt](baseline-failures.txt).
Many live tests use the wall clock and were run during an ET entry blackout;
other existing failures assert stale preset stops, entry windows or take-profit
settings. Those unrelated presets/tests were not changed to make this feature pass.

Hourly acceptance tests: **87 passed**. Targeted integration run: **125 passed**,
including control-selection parity across legacy histories, deployment preparation,
config hash compatibility, research groups and public browser interactions at
mobile/tablet/desktop sizes. Existing tests that needed new asset counts or null
hash exclusion were updated; unrelated preset assertions were retained.

Final full suite: **63 failed, 2,751 passed, 3 skipped**, 637.52 s.
**All 63 failure IDs also failed in the baseline; there are zero new failing
IDs.** The 251 baseline failures no longer reproduced are largely affected by
wall-clock entry blackouts; this is not a claim to have repaired those tests.
[Final failure list](after-failures.txt), [machine-readable comparison](validation.json).
The full suite is therefore **not green** because of retained pre-existing failures.
The new hourly, integration and resource acceptance checks are green.

Core commits are `4db1db6` (contracts/presets) and `62102ef` (guarded execution).
The following focused commit contains dashboard/deployment integration and this
validation report. No commits were pushed.

Reproduce with `.venv/bin/python -m pytest -q`. Run the ladder benchmark with
`.venv/bin/python scripts/benchmark_hourly_ladder.py` and the existing benchmark
with `.venv/bin/python scripts/benchmark_resource_optimizations.py`.
The full suite needs local socket access for its mocked HTTP/WebSocket servers;
no real trading credentials or live orders are used by these checks.
