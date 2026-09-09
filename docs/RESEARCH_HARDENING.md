# Research capability hardening — 2026-09-08

The requested scope is authenticated collection, backtesting and conservative paper
simulation, before autonomous production paper operation or LIVE. Existing project
documents were reviewed as evidence of gaps. Strategy entry thresholds, model
family, queue assumptions and the one-entry policy were not optimized.

## Changes implemented

- **Collection:** independent socket receipt, REST refresh and one ordered analysis
  worker; bounded 20,000-frame queue; batched durable journal writes before analysis;
  streaming Parquet flushes; processing lag and queue high-water telemetry. Slow
  analysis does not assign late receipt timestamps to already-arrived frames.
  Overload/storage failure stops processing. Already received queued frames are
  retained when possible; a disk failure cannot guarantee persistence.
- **Clock handling:** raw wall/monotonic/source timestamps remain unchanged. Backward
  wall-clock steps with forward monotonic receipt cancel pending entries and block
  trading pending clock revalidation. Ordered frames still update the book;
  reconnecting solely for these steps was creating avoidable reference gaps. Actual
  ordering/sequence faults still invalidate state. No timestamp sorting hides gaps.
- **Empty books at rollover:** authenticated snapshots sometimes omit both price
  arrays. They now clear stale levels and represent an empty, non-executable book.
  Missing liquidity blocks entries; it no longer triggers a reconnect loop.
- **External failures:** denied subscriptions fail visibly; malformed JSON is retained
  as an error; producer/analysis failures drain received frames where storage permits.
  Recorder failure still closes the client, stops the worker and releases its lease.
  Abrupt process death intentionally leaves a lease for operator review.
- **REST load:** local admission budgeting at 50 read tokens/second, including
  retries: ordinary reads cost 10, CF passthrough reads 50. Bounded 429/5xx backoff
  remains. This does not allocate a shared account budget across other processes.
- **Backtesting:** streaming JSONL/Parquet, disk-backed duplicate-ID checks across
  inputs, conflicting-ID rejection, streaming hashes, input-mutation detection,
  preserved synthetic/recorded provenance, dataset audits and failed-experiment
  records. Audits expose sequence/reference gaps, clock steps, target-market
  settlements and complete windows; unrelated lifecycle settlements do not qualify.
- **Held-out research:** every fold is checked before selection. Time overlap,
  repeated train/test markets, overlapping holdouts, optional embargo violations,
  failed dataset audits and mixed synthetic/authentic folds are rejected.
  Insufficient training samples are persisted as negative findings.
- **Statistics:** deterministic UTC-day cluster bootstrap intervals for market-level
  Brier and per-trade P&L, with an explicit 20-day minimum. Repeated predictions do
  not inflate independent market counts. Interday dependence remains an assumption.
- **Paper accounting:** ledger records, duplicate claims, transitions and checkpoint
  commit together for each execution action. Failed writes restore in-memory
  positions/risk/fee carry. Settlement claims and payout accounting are in the same
  transaction, allowing an actual retry after rollback. PostgreSQL conflict-safe
  claims use returned rows rather than driver-dependent insert row counts.
- **Paper recovery:** `paper --resume RUN_ID` restores committed positions, fee carry,
  duplicate trade IDs, risk, exit depth consumption and order projections; cancels
  resting remainders; starts with unhealthy feeds; polls old markets for settlement.
  It never invents downtime fills. Configuration/mode mismatch is rejected. Legacy
  pre-checkpoint runs still require forensic recovery, since missing state cannot
  be reconstructed reliably by guessing.
- **Fees:** scheduled series/event fee changes are checked at their effective times.
  Unit-multiplier quadratic and quadratic-with-maker-fees are supported; other
  schedules block execution. Metadata-backed quadratic makers have zero trading
  coefficient. Fill records expose the coefficient and balance precision. Direct
  account precision defaults to `0.0001`; applicable FCM accounts use `0.01`.
  This corrects prior conservative defaults; historical configurations are unchanged.
  Configured coefficients and unobservable account discounts remain explicit inputs.
- **Exit safety:** an unsupported take-profit tick does not prevent independent
  stop evaluation. Stale order references and fills above a limit are rejected.

## Tests and measured evidence

See [VALIDATION.md](VALIDATION.md) for final counts and capture results. All
artifacts, raw journals, API responses, SQLite databases and detailed logs are local
ignored files under `data/hardening/` and `data/hardening-*.log`. They contain no
persisted API credentials. Each research run preserves its source/config snapshot;
therefore captures made while fixes were being developed retain their actual code
version rather than being presented as evidence for code they did not run.

The first authenticated smoke retained 12,224 events with zero sequence gaps and
exposed two backward clock adjustments. A 1,000-monotonic-second development soak
retained 403,404 events, observed rollover and official settlement, and exposed
avoidable reconnects under earlier clock/empty-book handling. It is **rejected as a clean
research dataset**, with 29 backward clock adjustments, 21 reference gaps and no
complete settlement window. These negative findings are retained.

A later 90-second check after the clock fix retained 41,123 events, one socket
connection, zero sequence gaps and zero reference gaps despite three backward clock
steps. Maximum observed queue was 475 frames and maximum sampled processing lag was
0.140 seconds. A final settlement-window capture validates the corrected behavior
through rollover; its audited outcome is in VALIDATION.md. That capture also
exposed omitted empty-book arrays, fixed afterward and checked against the
authentic tape plus a regression test. It is not a clean-data certification.

Authenticated historical BRTI requests succeeded. A completed hour returned 18,000
intra-second observations; a current-hour response was incomplete. This is not a
tradable order-book tape or a replacement for missing receipt-time history. CF
explicitly documents up to 15 minutes of history publication delay and requires
query timestamps aligned to the requested timespan. No row-cap conclusion is
inferred from the incomplete current-hour result.

## Remaining acceptance gates

**Authenticated collection: READY for authenticated research collection on this host.**
The [collection recovery follow-up](COLLECTION_RECOVERY.md) resolved competing WSL
clock controllers and rollover reconnects. Its fresh 1,000-second capture passed
with 453,202 events, one socket connection, no clock/sequence/reference gaps and a
complete settlement window matching the published average. Earlier failed captures
above remain rejected; each future recording must pass its own data-quality audit.

**Backtesting: READY for audited research. Paper simulator: READY for conservative
simulation with explicit assumptions.** These are software readiness statements.
Neither calibrated probabilities nor exact counterfactual fills can be established
by implementation tests or one captured settlement. Consecutive multi-day data,
held-out calibration, cost sensitivity and external execution measurements are
still required. The bootstrap reports insufficiency rather than a fabricated interval.

No automatic repricing, new probability ensemble or strategy threshold change was
introduced. Those are predeclared strategy experiments, not repairs to accounting.
No maker queue position, hidden liquidity, market impact or attainable latency is
claimed known from aggregate public data. FCM charges and special account discounts
are not inferable from public series metadata.

**Autonomous production paper: NOT READY. LIVE: NOT READY and blocked.** The final
two capabilities were not advanced. The clock/collection repair closes those specific
gaps; multi-day operation and independent empirical research requirements remain.

## Primary references checked

- [Kalshi standard BRTI feed](https://docs.kalshi.com/websockets/cfbenchmarks-value):
  final-minute window excludes its start and includes the close; the separate 5 Hz
  feed must not multiply settlement sample count.
- [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) and
  [scheduled series changes](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes).
- [Kalshi CF passthrough](https://docs.kalshi.com/cfbenchmarks/rest-passthrough):
  entitlement, signing and read-token cost.
- [CF historical values](https://docs.cfbenchmarks.com/api/rest/historical-values/):
  time alignment and possible publication delay.
