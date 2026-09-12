# Active paper settings — September 11, 2026

The installed collector and dashboard now select `settlement-convergence-v8`,
using `data/runtime/settlement-convergence-v8.json`. The matching tracked
configuration is `config/settlement-edge-active-paper.json`.

The user requested this as a direct replacement for the active bot settings:

| Entry setting | Value |
| --- | --- |
| Minimum raw entry probability | 0.80 standard; 0.85 late |
| Minimum purchase price | 0.70 |
| Minimum net edge and EV | 0.01 each |
| Standard entry window | 120 < seconds remaining <= 600 |
| Late entry window | 15 < seconds remaining <= 120 |
| Confirming official one-second samples | 3 standard; 5 late |
| Minimum normalized settlement lead | 1 standard; 2 late |

Both paths retain [sustained-lead confirmation](SUSTAINED_LEAD.md).
Entry probability deductions are disabled: raw model probability drives entry
pricing, probability floors and net EV at submission. Calibration, simulation,
rounding, reversal stress and disagreement deductions do not reduce entry
probability. Stress is still recorded as a diagnostic. Exit rules are unchanged.
Late entries use the expected settlement average to select the favored side.

IOC orders use 10 contracts and allow up to three submissions per market
(initial plus two retries, at least five seconds apart). After submission,
`revalidate_entry_signal=false` commits the original side and price cap: probability,
lead and net-value changes cannot cancel it. Health/freshness checks, reference
receipt processing, market closure, timeout and risk limits still apply.
Exit rules still apply after a fill, so a committed entry can subsequently exit
if its holding conditions fail. Hold-value exits remain disabled. Broader eligibility is intended
to generate more paper execution evidence; no increase in fills or profitability
is guaranteed.

There is one active collector, using the existing database. The internal run
revision preserves configuration attribution and checkpoint compatibility; old
history remains available. Daily exposure, submitted attempts and realized P&L
carry forward rather than resetting. Settings for future sessions were aligned
with this configuration. Built-in historical controls and earlier frozen files
remain available for interpreting their recorded runs.

Daily attempt and cumulative daily exposure caps are disabled with
`daily_entry_limits_enabled=false`. The saved numeric thresholds are inactive,
not reset or replaced with large arbitrary numbers. Daily counts and exposure
continue accumulating for audit. The daily loss cap ($30), per-trade cap ($10),
open-exposure cap ($20), sizing limits, and three-attempt per-market limit remain.

Validation: 157 targeted regression tests passed, including filling beyond the old
daily caps, preserving counters, enforcing other risk limits, precision boundaries,
reference handling and settings compatibility. Configuration hash:
`69ec9f4357029f20`. Historical daily totals are preserved; no reset is needed to
resume eligibility. Normal startup reference warmup still applies.

## Clean current-run history views

The dashboard automatically selects the current collector run for Trade History,
Results & Accuracy, and overview queries. Without a matching collector it selects
the newest available run in the selected mode. This keeps the current configuration's
results separate from earlier settings. An explicit run selection, including
“All runs (combined history),” remains selected during refreshes. Previous records
and portfolio-wide daily risk totals are preserved. Refresh an already open
browser page to load this behavior.

## September 10: minimum liquidity filter disabled

The active v7 paper configuration now sets `min_liquidity=0` at the user's request.
A displayed ask with fewer than five contracts no longer blocks entry solely for
quantity. Available depth still limits actual fills, and missing quotes still fail
price/spread checks. Other entry/exit thresholds are unchanged.

The primary and stop-observer checkpoints retain all risk, inventory and tracking
state under the new configuration hash; historical trade records are not rewritten.
Deployment backup and configuration change evidence are in
`data/runtime/liquidity-disabled-deployment.json`.

The dashboard now distinguishes an old evaluation for a completed trade from an
unexpectedly stale evaluation. The engine deliberately stops evaluating a completed
market. Its last decision remains historical while the collector follows subsequent
markets; the display does not refresh its timestamp to imply a new evaluation.

## Fresh v8 run: eight-minute standard entry window

The active run is now `settlement-convergence-v8`, configured by
`data/runtime/settlement-convergence-v8.json` (hash `64380ebbd40287af`).
Standard entries require `120 < seconds_remaining <= 480`; late entries retain
`15 < seconds_remaining <= 120` and their existing stricter conditions.
Other strategy settings remain as in the preceding run, including zero minimum
liquidity and sequential same-market re-entry.

At the user's request, primary and shadow ledgers were archived and replaced with
empty ledgers, clearing displayed trade history, results and accuracy for this run.
No open inventory or active orders were present at reset. Prior ledgers and service
files are in `data/archive/before-v8-20260911T034058Z`; the deployment manifest is
`data/runtime/fresh-v8-deployment.json`. Paper risk balances also start fresh.

## September 11: two-reference late confirmation deployed

The existing v8 paper run now uses `late_lead_confirmation_samples=2`, configuration
hash `90009ca577a3c010`. This deploys the two-confirmation timing control from the
historical late-entry experiment. It does not deploy the stronger conditional
lead-3 fast path. Both consecutive fresh reference samples must meet the existing
late lead requirement; repeated book events do not count as reference confirmations.

Standard entry still requires three confirmations within 2–8 minutes remaining.
Late entry still requires 15–120 seconds remaining, lead >=2 and raw probability
>=85%, plus the existing price/value/quality/spread/health/risk checks. All exits,
sizing, and same-market re-entry rules remain unchanged. The stop-confirmation
observer remains enabled and is a separate exit experiment.

Primary and observer checkpoint configuration versions were updated after stopping
the services. Inventory, risk, trade history and performance were preserved. Exact
before-state backup and config-change evidence:
`data/runtime/late-two-confirmations-deployment.json`. Future records carry the new
configuration hash so these results can be separated from the five-confirmation phase.

The active run now enables the [profit-only value exit](EXIT_EXECUTION.md#profit-only-value-exit-paper):
$0.20 estimated total net profit, 1 cent per contract advantage over raw settlement
probability, and two fresh-reference confirmations. Submitted IOC exits keep their price
floor even if confidence recovers. Entry rules and existing safety thresholds are unchanged.

## September 11: shadow comparison disabled

The installed collector now omits `--stop-confirmation-shadow`, at the user's
request. It resumes the same `settlement-convergence-v8` paper run and frozen
configuration, with primary entries, exits, recording and recovery checks intact.
The optional comparison no longer processes incoming messages. Its historical
results and `data/stop-confirmation-shadow.db` remain available for inspection.

## September 11: conditional Bollinger entry filter

The active v8 paper configuration enables `bollinger_entry_filter_enabled=true`,
with the existing 20-minute period and two-standard-deviation bands. Configuration
hash: `ebc936c466e8f0bf`. A new YES entry is rejected above the upper band; a new NO
entry is rejected below the lower band. Equality passes. If fresh bands are
unavailable, the original entry checks determine eligibility. This applies to
both standard and late entry paths, using the causal model reference.

The same run, trade history, sizing, risk balances and exits are retained. The
disabled stop-confirmation observer is not restarted or migrated. New records
carry the new configuration hash. See [Bollinger entry filter](BOLLINGER_ENTRY_FILTER.md)
for exact candle requirements, audit records, startup behavior and evaluation limits.
Deployment evidence is saved under `data/runtime/bollinger-entry-20260911/`.
