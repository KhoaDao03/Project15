# Active paper settings

Current exit policy: model probability exits are disabled for all four paper bots (`exit_probability=0.0`, hold-value exit disabled). The 55¢ stop remains active; ETH/SOL/XRP retain their 99¢ paper take-profit, and BTC retains its existing settlement/stop policy.

September 15 update: all BTC/ETH/SOL/XRP entry windows start at 8 minutes remaining, both confirmation counts are 1, and buy retry cooldown is zero. Live retries wait for a known empty result and revalidate the signal before another submission. Historical deployment notes below retain their original settings.

The installed collector and dashboard now select `settlement-both-models-80-v1`,
using `data/runtime/settlement-both-models-80-v1.json`. The matching tracked
configuration is `config/settlement-edge-active-paper.json`.

All four current paper bots use a 50/50 Project15–Bleep blend. Both entry windows
require Bleep probability ≥78%; the blended entry minimum is disabled. Project15 has no
individual veto. Sigma floors remain disabled; same-side sample confirmation
and quality checks remain. Latest deployment hashes and evidence:
`data/runtime/bleep78-all-20260916/`.
See [Bleep blend](BLEEP_BLEND.md) for formulas and component recording.
[Startup preload](REFERENCE_PRELOAD.md) restores validated saved BRTI history;
exchange seeding avoids the roughly 33-minute Bleep cold warmup when a provider
succeeds. Project15 quality checks still apply. Archived
deployment history follows below.

Current entry settings:

| Entry setting | Value |
| --- | --- |
| Minimum raw entry probability | 0.85 standard and late |
| Minimum purchase price | 0.85 |
| Maximum purchase price | 0.95 |
| Minimum net edge and EV | Disabled |
| Individual component probability | Bleep ≥80% standard and late; Project15 individual veto disabled |
| Standard entry window | 120 < seconds remaining <= 480 |
| Late entry window | 15 < seconds remaining <= 120 |
| Confirming official one-second samples | 1 standard; 1 late |
| Minimum normalized settlement lead | Disabled in both windows |

Both paths retain [sustained-lead confirmation](SUSTAINED_LEAD.md).
Entry probability deductions are disabled: the raw blended probability drives entry
pricing, probability floors and net EV at submission. Calibration, simulation,
rounding, reversal stress and disagreement deductions do not reduce entry
probability. Stress is still recorded as a diagnostic.
Late entries use the expected settlement average to select the favored side.

Current exit and re-entry settings:

| Setting | Value |
| --- | --- |
| Standard-entry cashout | Disabled |
| Adjusted held-side probability exit | Disabled (threshold 0) |
| Hard stop | Held-side bid ≤0.55, regardless of entry price |
| Separate take-profit target | Disabled |
| Pause after a position fully closes | 60 seconds before any new position, across markets |
| Unfilled-order retry cooldown | 0 seconds; previous outcome must be confirmed |
| Older profit-value and hold-value exits | Disabled |

The disabled standard cashout includes actual entry fees, any earlier sale proceeds and paid
fees, and estimated exit fees. It requires enough available bid depth for the full
remaining position at a supported price floor meeting the target. It uses no model
confirmation requirement. Post-latency IOC fills must respect that floor; a displayed
profit is not a guaranteed fill. The rule does not apply to late entries or at/below
120 seconds remaining. Entries near 0.90–0.95 cannot earn ten cents net per contract,
even at a winning settlement. See [paper exit execution](EXIT_EXECUTION.md#standard-entry-net-profit-cashout-paper).

Full-position orders buy all 10 contracts or none within the price cap and allow up to three submissions per market
(initial plus two retries, without an added cooldown). After submission,
`revalidate_entry_signal=false` commits the original side and price cap: probability,
lead and net-value changes cannot cancel it. Health/freshness checks, reference
receipt processing, market closure, timeout and risk limits still apply.
Exit rules still apply after a fill, so a committed entry can subsequently exit
if its holding conditions fail. Hold-value exits remain disabled. Broader eligibility is intended
to generate more paper execution evidence; no increase in fills or profitability
is guaranteed.

The following describes the original run launch; subsequent migrations preserve its ledger.
At launch there was one active collector, using a fresh ledger at the existing database path.
The previous ledger is archived with its configuration and source. This experiment
starts with zero daily exposure, submitted attempts and realized P&L, and a $1,000
paper bankroll. Settings for future sessions match the new frozen configuration.
Built-in historical controls and earlier frozen files remain available for
interpreting their recorded runs.

Daily attempt and cumulative daily exposure caps are disabled with
`daily_entry_limits_enabled=false`. The saved numeric thresholds are inactive,
not reset or replaced with large arbitrary numbers. Daily counts and exposure
continue accumulating for audit. The daily loss cap ($30), per-trade cap ($10),
open-exposure cap ($20), sizing limits, and three-attempt per-market limit remain.

Historical daily-cap deployment validation: 157 targeted regression tests passed, including
filling beyond the old daily caps, preserving counters, enforcing other risk limits, precision boundaries,
reference handling and settings compatibility. Configuration hash:
`69ec9f4357029f20`. That deployment preserved daily totals; the later fresh blend
experiment resets counters only after archiving them.

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

This deployment enabled the [profit-only value exit](EXIT_EXECUTION.md#profit-only-value-exit-paper)
(subsequently disabled on September 12):
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

## September 12: one filled trade per market

The current `settlement-convergence-v9` paper run enables `one_trade_per_market=true`.
After any filled position in a market closes, neither entry path nor the opposite
side can open another position there. Partial fills of one order remain one trade.
Unfilled orders retain their bounded retries. The existing 60-second cooldown still
applies before trading another market. The limit survives restart and does not reset
history, risk balances, or existing positions. See [entry limits](REENTRY.md).

## September 12: seven-minute standard entry window

The active paper configuration sets `entry_window_start=420`, so standard entries
require `120 < seconds_remaining <= 420`. Late entries retain
`15 < seconds_remaining <= 120`. The only configuration change is the upper
entry-window limit, from 480 to 420 seconds; all other settings are retained.
The new configuration hash is `49c03409f6e8844e`.

The collector and dashboard resume the existing `settlement-convergence-v8` run
after a clean stop and audited checkpoint version update. Trade history and risk
balances are preserved. Normal startup reference warmup applies. Deployment
evidence and the database backup are in `data/runtime/entry-window-420-20260912/`.

Validation: 82 targeted regression tests and 16 boundary checks passed. The running
collector and dashboard both report the new configuration hash after restart.

## September 12: 0.95 entry-price cap and profit-value exit disabled

The active configuration now sets `max_entry_price=0.95` and
`profit_value_exit_enabled=false`, hash `8eb9db7ebe0ad470`. The price cap is
inclusive and applies to both entry paths and IOC order limits. The profit-value
exit no longer submits exits. The separate take-profit target, probability exit,
hard stop, and all other settings are retained, including the 420-second standard
entry-window limit and conditional Bollinger entry filter.

The collector and dashboard resume the same paper run after a clean stop and
audited checkpoint version update, preserving trade history and risk balances.
Normal startup reference warmup applies. Deployment evidence and the database
backup are in `data/runtime/entry-price-95-no-profit-value-20260912/`.

Validation: 94 targeted regression tests and 33 checks using the new configuration
passed. Both running services loaded the new configuration, and the collector
health probe passed after restart. All 69 existing trade results and risk balances
were preserved.

## September 12: standard cashout, sixty-second pause and 60% probability exit

The paper configuration sets `standard_cashout_enabled=true`,
`post_close_cooldown=60` and `exit_probability=0.60`, hash
`a52d76b4ec934afe`. Cashout is restricted to standard entries with strictly more
than 120 seconds remaining and targets at least $0.10 net per purchased contract
after all fees. It uses supported price limits and available full-position depth,
with the existing simulated IOC delay. The sixty-second pause applies after a
full position closure before any new position in any market; unfilled-order retries
retain their separate five-second setting.

The 0.75 hard-stop multiplier, 0.99 take-profit target, 0.95 entry-price cap,
420-second standard-entry window, Bollinger filter, sizing and other entry gates
are retained. The older profit-value and hold-value exits remain disabled. These
changes define a new paper comparison; they do not establish improved returns.

Deployed September 12 at 02:17:40 EDT by restarting the existing v8 collector and
dashboard. Both services loaded the new configuration and passed connection,
freshness and recovery-readiness checks. All 76 recorded trades and the existing
risk balances were preserved. Normal reference warmup follows restart.

Validation: all 994 regression tests passed. The 23 cashout/cooldown tests also
passed after the final cashout price-search optimization. Independent price-floor
checks agreed with exhaustive supported-tick enumeration in 600 cases. Backup,
configuration audit, test reports and service verification are retained under
`data/runtime/cashout-60-cooldown-20260912/`.

## September 12: archived history and fresh v9 paper run

At the user's request, the main ledger (76 completed trades) and inactive
comparison ledger (20 completed trades) were archived under
`data/archive/before-v9-20260912T062301Z/`. Both portfolios were flat. The archive
includes the complete databases, configuration, service definitions, current
strategy source and an index to the retained raw recordings. Previous research
and raw files remain in their original locations.

The active services now select `settlement-convergence-v9` with empty ledgers,
zero realized P&L and fresh paper risk counters, using the configured $1,000
bankroll. Configuration hash `a52d76b4ec934afe` and all strategy rules are unchanged.
The comparison collector remains disabled. Normal startup warmup applies.
Archive/reset evidence: `data/runtime/fresh-v9-deployment.json`.

## September 12: cashout disabled and $0.80 minimum entry

At the user's request, the active v9 paper configuration now sets
`standard_cashout_enabled=false` and `min_entry_price=0.80`. The inclusive
maximum remains `max_entry_price=0.95` for both standard and late entries.
Configuration hash: `51d0c4695e64863e`. The $0.99 take-profit target, 0.75
hard-stop multiplier, 60% adjusted-probability exit and 60-second post-close
cooldown remain unchanged.

The collector and dashboard were cleanly stopped for a database backup and
audited checkpoint configuration update. All 47 completed trades, risk balances
and market-entry tracking were preserved; no positions or active orders remained
at migration. Runtime, saved and tracked settings match. The focused regression
suite passed all 56 tests. Normal reference warmup applies after restart.
Deployment and verification evidence: `data/runtime/no-cashout-entry-80-20260912/`.

## September 12: fresh Project15 / Bleep probability blend

The active run is `settlement-bleep-blend-v1`, configuration hash
`a5c705ce71910618`. It averages original Project15 and indicator-adjusted
Bleep Mode B YES probabilities equally, without Bleep's safety clamp. The
new probability also supplies the existing adjusted-probability exit. Both
component estimates are retained. See [the specification](BLEEP_BLEND.md).

All 48 completed trades and the inactive comparison ledger were archived at
`/home/devk/Project15/data/archive/before-bleep-blend-20260912T233510Z` with prior source, configuration, service definitions and a
raw-recording index. Both ledgers were flat before archiving. Raw recordings
and previous archives remain intact. The fresh portfolio begins at $1,000
with zero trades, realized P&L and risk counters. The comparison collector remains disabled.

The $0.80–$0.95 entry range, disabled standard cashout and other settings are
retained. Full Bleep indicators require roughly 33 minutes of fresh reference
history after restart. Regression evidence covers 1,029 unique tests after a
legacy-hash fixture correction, including 300 TypeScript formula parity cases.
Archive, service and fresh-ledger verification are under `data/runtime/bleep-blend-v1/`.

## September 12: validated reference-history preload

The same `settlement-bleep-blend-v1` run now preloads validated official BRTI
history at startup. Configuration hash `a5c705ce71910618`, balances,
positions and history are preserved. No paper reset was performed.

This restart restored 1028 one-second samples (about 17 minutes)
from the prior recording with no rejected inputs. Services passed connection,
freshness, recovery and same-run checks. The blended model still requires
33 usable contiguous candles; preloading cannot fill shutdown gaps or create
history that has not been collected.

A small reference cache is updated every 30 seconds and on shutdown. Samples
roll out of the one-hour history as new live samples arrive. First-event-only
preload recording preserves offline replay; new live confirmations are required.
See [reference preload](REFERENCE_PRELOAD.md). Evidence covers 314 unique
passing tests under `data/runtime/reference-preload-v1/`.

Active Bleep update: historical exchange candles seed only Bleep indicators; its
75% favored-side safety cap applies below 0.5 sigma before the 50/50 probability
average. See [Bleep blend](BLEEP_BLEND.md).

## Fresh $0.75 minimum-entry paper test

The user authorized reducing only `min_entry_price` from 0.80 to 0.75 and
resetting trading history. The previous ledger is archived intact before the
fresh `settlement-bleep-entry-75-v1` run starts with $1,000 paper bankroll and
zero positions, trades, realized P&L and risk counters. Maximum purchase price
remains $0.95; probability requirements, Bleep seed/clamp and all other rules
remain unchanged. Reference history and raw recordings are retained.
Deployment and archive details: `data/runtime/bleep-entry-75-v1/deployment.json`.

## Net-edge and expected-value entry filters disabled

For the current paper run, `entry_value_filters_enabled=false` bypasses both
minimum net-edge/EV checks in evaluation, order submission and IOC limit
validation. The effective price ceiling uses the configured maximum price,
subject to the probability floor and supported price ticks. Negative net edge
can now qualify if the other entry rules pass. Numeric `min_edge`/`min_ev`
values remain saved for re-enabling; probabilities, fees, slippage, sizing,
exit rules and reporting still operate normally. No history or balance reset.

## Net-edge and EV filters re-enabled

The current paper run again uses `entry_value_filters_enabled=true`, with
`min_edge=0.01` and `min_ev=0.01`. Evaluation, effective entry ceiling and
order-submission checks enforce these requirements. The $0.75–$0.95 purchase
range, other rules, trade history and balances are preserved. Deployment
evidence: `data/runtime/entry-value-filters-on-v1/`.

## Both-model 80% paper experiment

`both_models_80_enabled=true` requires each component’s raw probability for
the selected YES/NO side to be at least 80%, using Bleep after its safety clamp.
The blended probability still requires 80% for standard entries and 85% for
late entries. Missing or invalid component values block evaluation and order
submission. Net-edge/EV vetoes are disabled (`entry_value_filters_enabled=false`),
while the metrics and fees remain recorded. Other rules, history, balances
and exit behavior are preserved. Deployment: `data/runtime/both-models-80-v1/`.

## Fresh both-models-80% BTC paper ledger

The user requested clearing current BTC trade history. Three completed trades
were archived intact under `data/archive/before-both-models-80-fresh-20260913T041703Z/`.
The fresh `settlement-both-models-80-v1` run keeps config `d89413d10b9b6230`
and starts with $1,000 paper bankroll and zero trades/P&L/positions. Other asset
ledgers and dashboard members are preserved. Verification and deployment are
in `data/runtime/both-models-80-fresh-v1/`.

## Bollinger entry veto disabled across all assets

BTC, ETH, SOL and XRP now use `bollinger_entry_filter_enabled=false`.
An entry is no longer blocked solely because the reference is outside the
side-specific Bollinger band. Bollinger indicators and Bleep indicator lean
remain calculated. All other per-asset settings, histories, balances and
positions are preserved. Backups and runtime verification are under
`data/runtime/bollinger-filter-off-v1/`.

## BTC standard-entry 85% update

Standard entries now require both selected-side component probabilities ≥85%,
with more than 2 and at most 8 minutes remaining. The run ID is retained to
preserve history and balances. Late entries, other assets, pricing and exit rules
are unchanged. Net-edge and EV filters remain disabled. Deployment evidence:
`data/runtime/standard-both-85-v1/`. Earlier sections record prior experiments.

## BTC Bleep gate and fixed 49¢ stop

Only Project15’s individual probability veto is disabled. The 50/50 blend,
blended floors (80% standard / 85% late), Bleep individual floors (85% standard /
80% late), and settlement-lead checks remain. The stop triggers at a held-side
bid ≤49¢. Paper buys target 10 contracts at lowest available asks within the
existing price cap; sells consume highest available bids for the full position.
Actual depth, latency and partial fills still apply; 49¢ is not a guaranteed exit
price. History and balances are preserved. Evidence: `data/runtime/bleep-gate-stop49-v1/`.

## BTC hard-stop-only exits

Only the fixed held-side bid ≤49¢ hard stop remains enabled. `exit_probability=0`
disables the probability exit; `take_profit=null` disables the profit target.
Cashout, hold-value and profit-value exits remain disabled. Any remaining position
settles at expiration. Entry rules, history and balances are preserved.
Deployment evidence: `data/runtime/hard-stop-only-v1/`.

## BTC Bleep-only probability

The current BTC preset disables the equal blend and enables
`bleep_probability_only_enabled=true`. Bleep alone supplies reported probability,
entry probability floors and estimated EV. Standard and late entries effectively
require Bleep ≥85%; all existing thresholds are preserved. Project15 probability
remains diagnostic only, with its settlement-lead checks still required. No
history or balance reset is part of this change. Other assets retain their settings.
Deployment evidence: `data/runtime/bleep-only-v1/`.

## Fixed 62¢ hard stop across all assets

BTC, ETH, SOL and XRP now use `fixed_stop_price=0.62`: the hard stop
triggers when the held-side bid is at or below 62¢, regardless of entry price.
This replaces BTC’s 49¢ stop and the other assets’ entry-price multiplier.
Other per-asset entries, exits and probability settings are preserved.
The trigger is not a guaranteed execution price. Paper history and balances
are retained. Deployment evidence: `data/runtime/stop62-all-v1/`.

## BTC 85% blend / 80% Bleep deployment

The BTC proposal restores equal model weights, raises the standard final floor to
85%, and lowers the standard Bleep floor to 80%. Late floors already match.
Project15 has no individual veto. The fixed 62¢ stop, settlement-lead checks,
sizing, timing and other assets retain their existing settings. Paper trade history,
positions and risk counters are preserved. Deployment backup, configuration audit,
test results and runtime verification are in `data/runtime/blend85-bleep80-v1/`.

## Full-position execution

All four active assets now use [full-position execution](FULL_POSITION_EXECUTION.md).
New entries fill exactly 10 contracts or none. Exit decisions stay committed for
the entire held quantity, selling highest bids first as liquidity permits.
Existing price caps, risk limits, probability rules and 62¢ stop triggers remain.

## Fixed 55-cent stop

All four paper assets now trigger the hard stop at a held-side bid of 55¢.
All other settings and portfolio state are preserved. Deployment evidence:
`data/runtime/stop55-all-v1/`. Earlier sections retain historical settings.

## Bleep-only 85% across all assets

The blend is disabled for BTC, ETH, SOL and XRP. Bleep alone supplies probability,
with an 85% final entry floor in both windows and no Project15 probability veto.
The standard component floor is also 85%; the inherited 80% late component check
is superseded by the 85% Bleep-only final floor. The 55¢ stop, full-position
execution, lead checks, other asset-specific settings and paper history remain.

## Purchase range 85–95 cents across all assets

BTC, ETH, SOL and XRP use a minimum entry ask of 85¢ and maximum entry price
of 95¢. Bleep-only probability remains ≥85% in both entry windows, with no blend
or Project15 probability veto. Full-position execution and the 55¢ stop remain.
Latest active hashes, backups and runtime verification:
`data/runtime/price85-95-all-v1/`.

## Modeled-sigma floors disabled

All four active paper bots set `min_lead_sigma=0` and `late_min_lead_sigma=0`.
Zero disables the sigma-distance requirement in evaluation, sample confirmation,
and submission. Same-side consecutive sample confirmation remains (3 standard,
2 late), including gap/side-change resets. Late side selection still uses the
modeled settlement average. Bleep-only 85%, 85–95¢ entry range, full-position
execution and the 55¢ stop remain. Latest deployment evidence and hashes:
`data/runtime/no-sigma-floor-all-v1/`.

## Restored blend: 85% final / 80% Bleep

All four paper assets restore equal model weights and Bleep's 80% individual
floor. Final probability remains 85% in both windows. Project15's individual veto
stays disabled, as do modeled-sigma floors. All other settings and ledgers remain.
The manual real-money buy/sell controls are separate and are unaffected.

## September 15: directional Bollinger entry veto enabled

All four assets now set `bollinger_entry_filter_enabled=true`. The shared entry
evaluation rejects an upward entry above the upper Project15 Bollinger Band and
a downward entry below the lower band. Equality at the boundary is allowed.
Missing or stale bands do not independently block entry; other checks still apply.
This applies to both entry windows and the strategy decisions consumed by live
automation. All other current settings and portfolio history are preserved.
Deployment and verification: `data/runtime/bollinger-on-all-20260915/`.

## September 15: purchase minimum reduced to 80 cents

BTC, ETH, SOL and XRP now use an inclusive 80–95 cent entry range. Only
`min_entry_price` changed from 0.85 to 0.80; existing Bollinger filtering,
probability thresholds, sizing, exits and ledger state are preserved. This
applies to the loaded paper strategy and decisions used by live automation.
Evidence: `data/runtime/price80-95-all-20260915/`. Earlier sections describe
historical configurations.

## September 15: blended entry floor raised to 87%

All four assets require selected-side blended probability ≥87% for standard and late entries. Bleep remains ≥80%; the equal blend and Bollinger filtering are retained. Existing run histories and portfolio state are preserved. Deployment evidence: `data/runtime/blend87-all-20260915/`.

## September 15: blend 85%, Bleep 82%

All four bots now require blended selected-side probability ≥85% and Bleep ≥82% for both standard and late entries. The late component floor is explicitly configurable; older configurations default to 80%. Project15 has no individual veto. Live take-profit remains 99¢. Deployment evidence: `data/runtime/blend85-bleep82-all-20260915/`.

## September 15: restored blend 87%, Bleep 80%

All four markets require blend ≥87% and Bleep ≥80% in standard and late windows. The 99¢ live take-profit and 96¢ live buy limit remain in place. Deployment evidence: `data/runtime/blend87-bleep80-restored-20260915/`.

## September 15: restored blend 85%, Bleep 80%

All four markets require blend ≥85% and Bleep ≥80% in standard and late windows. Other entry and exit settings are preserved. Deployment evidence: `data/runtime/blend85-restored-20260915/`.

## September 15: model probability exits disabled

Set `exit_probability=0.0` for ETH, SOL and XRP; BTC already used zero. Retain all other entry and exit settings. Deployment evidence: `data/runtime/probability-exit-disabled-20260915/`. Live/Kalshi already had no model-probability exit; its resting 99¢ sale, below-70¢ cancellation and 55¢ stop remain in place.

## September 16: blended entry minimum disabled

At the user’s request, both standard and late blended probability floors are zero across BTC, ETH, SOL and XRP. Bleep remains ≥80% in both windows. Both models and the equal blend remain calculated and recorded; only the blended minimum is disabled. Other entry, sizing and exit settings remain unchanged. Deployment evidence: `data/runtime/no-blend-floor-20260916/`.

## September 16: Bleep settlement average, reference volatility and seven-minute start

All four bots enable `bleep_settlement_model_enabled` and set `entry_window_start=420`. Bleep uses observed settlement samples and the discrete-average uncertainty of future samples, with the higher of floored ATR-based volatility and recent official-reference volatility. Bleep ≥80%, the disabled blended floor, entry prices, Bollinger filter and exits are retained. See [Bleep model details](BLEEP_BLEND.md). Evidence: `data/runtime/bleep-average-volatility-7m-20260916/`.

## September 16: Bleep entry minimum lowered to 78%

The user requested a 78% live entry threshold. The shared standard and late Bleep component floors are now `0.78` across BTC, ETH, SOL and XRP, including deployed configs and saved presets. The Bleep v2 calculation, 420-second entry start, 15-second cutoff, disabled blended floor, 55¢ hard stop and 99¢ live take-profit remain unchanged. Evidence: `data/runtime/bleep78-all-20260916/`.
