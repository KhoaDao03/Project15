# Independent momentum research models

This checkout contains **BTC15 Settlement Edge**, not a Strong Momentum bot.
The existing Settlement Edge rules, thresholds, seeded probability model, entry
cadence and exits remain the control. The two new models are independent research
portfolios, initially inactive and permanently restricted to paper/backtest execution.
Paper results and backtests do not establish future profitability.

## Models and hypotheses

| Model ID | Name | Version | Initial state | Starting bankroll |
| --- | --- | --- | --- | --- |
| settlement-edge | BTC15 Settlement Edge (control) | v1 + effective config hash | Existing configured state | Existing config ($1,000 default) |
| conservative-confirmed-momentum | Conservative Confirmed Momentum | v1 | Inactive | $1,000 |
| volatility-regime-momentum | Volatility-Regime Momentum | v1 | Inactive | $1,000 |

The control's hypothesis is settlement-aware edge at high contract prices. Its
existing default 85–99¢ entry range, 120 < seconds left <= 480, conservative
probability/quality requirements, risk limits and exit rules are unchanged.
Its configuration is still managed by the existing Strategies dashboard/`--config`.
It is not renamed Strong Momentum and no absent Strong Momentum parameters are invented.

Conservative Confirmed Momentum hypothesizes that reference displacement plus
recent confirmation can identify fee-adjusted edge at moderate entry prices.
Volatility-Regime Momentum tests that same hypothesis restricted to ordinary
short-term volatility. Neither hypothesis has been validated or tuned to results.

Both new models require:

- Ask price **52–80¢**, inclusive; spread **<= 3¢**; at least one contract at the best ask.
- **3–10 minutes** remaining, inclusive; absolute spot/strike return **>= 0.15%**.
- Nonzero recent **3-minute return** in the same direction as spot/strike return.
- A valid completed minute candle no older than **60 seconds**.
- Conservative side probability minus ask, the shared taker fee upper bound and
  configured slippage **>= 0.05**. This is net expected dollars per $1 contract,
  not probability minus ask alone and not return divided by invested capital.
- The proxy edge threshold is **0.15** if the reference-source field is a proxy.
  The production collector supplies official BRTI only; no Coinbase adapter or
  permission to substitute proxy prices for settlement truth has been introduced.
- Existing feed freshness, valid market/book, clock, exchange, fee schedule,
  warmup, reference-gap, shock, kill-switch and bankroll/risk checks also pass.

YES and NO use symmetric movement/confirmation logic for the current `>=` BTC15
contract. If a validated contract uses the opposite comparison operator, the
existing settlement specification maps the upward/downward movement to its correct
outcome side. Equality has no momentum signal.

The volatility model additionally permits only **NORMAL**. Rejected evaluations
include structured codes, actual values and thresholds where available. Codes
include `PRICE_TOO_LOW`, `PRICE_TOO_HIGH`, `SPREAD_TOO_WIDE`, `INSUFFICIENT_MOVE`,
`CONFIRMATION_FAILED`, `INSUFFICIENT_EDGE`, `PROXY_EDGE_FAILED`, `STALE_CANDLE`,
`TOO_EARLY`, `TOO_LATE`, `INSUFFICIENT_LIQUIDITY`, `VOLATILITY_LOW`,
`VOLATILITY_HIGH`, `VOLATILITY_EXTREME`, `VOLATILITY_HISTORY_INSUFFICIENT`,
`POSITION_ALREADY_OPEN`, `DUPLICATE_EVENT`, `MODEL_INACTIVE`, `FUTURE_REFERENCE`
and the shared safety codes.
There is no new side-balancing restriction: no such policy exists in this control.

## Execution assumptions

New portfolios simulate **one contract**, use aggressive matching against the next
eligible book event after the existing 250 ms latency, and hold fills to official
settlement. Visible depth limits fills; missing depth cannot fabricate a fill.
They use actual simulated taker fee accounting. Their initial extra slippage
allowance is zero: the hypothesis is execution at the recorded ask after latency,
not a calibrated claim that live execution has zero slippage. These explicit new
model choices avoid importing the control's passive queue and early-exit hypotheses.
The control retains its original execution settings.

Each model/version has independent reservations, daily limits, P/L, orders,
positions, duplicate claims and checkpoints. Different paper models may enter the
same event independently; no liquidity is shared between their counterfactual
portfolios. Event claims also prevent a single momentum portfolio entering two
tickers belonging to the same event. New models have no live activation option.

## Volatility calculation and data quality

No additional data API calls are made. The collector already retains one hour of
standard 1 Hz BRTI ticks. The 5 Hz display stream is never mixed into these samples.
For each completed UTC minute, calculate

```
r_i = log(price_i / price_(i-1))
minute_volatility = sqrt(60 * sum(r_i^2) / sum(delta_time_i))
```

This uses the same realized-return convention as the existing volatility features,
expressed per square-root minute. Existing `sigma`, `ewma` and `rv_*` fields remain.
Minutes need at least 58 observations, endpoints within two seconds of their
minute boundaries, strictly increasing timestamps and no internal gap > 2 seconds.
Incomplete/gappy minutes are excluded, not joined across gaps. Prices must be finite
and positive. Only samples both sourced and received by the evaluation time enter
features; later-arriving or future observations cannot rewrite earlier decisions.

The **current** sample is the latest completed minute. Compare it with valid
minutes in the preceding **59-minute** historical interval, excluding the current
minute. The one-hour retained buffer permits up to 58–59 complete historical
minutes depending on evaluation alignment. Require **at least 30** valid historical
samples; a cold start therefore needs roughly 31–32 minutes. No history is silently
backfilled at startup or resume. Replay warms from recorded causal events.

Use empirical midrank:

```
percentile = 100 * (count(history < current) + 0.5 * count(history == current)) / N
```

Tied constant volatility ranks at 50. Configurable boundaries `[20, 75, 90]` mean:

| Percentile | Regime |
| --- | --- |
| [0, 20) | LOW |
| [20, 75) | NORMAL |
| [75, 90) | HIGH |
| [90, 100] | EXTREME |

`allowed_volatility_regimes` initially equals `["NORMAL"]`. Future versions may
change it without changing rule code. Invalid names, unordered/out-of-range
boundaries and invalid history lengths are rejected. Insufficient history or an
invalid latest minute blocks the volatility model. Conservative Momentum requires
its candle and confirmation history but does not require 30 volatility samples.

Returns for 1, 3 and 5 minutes use a sample at or immediately before the target
time (within two seconds), requiring a gap-free intervening segment. Candle age,
reference source/receipt timestamps, volatility sample end, history count/window,
percentile and regime are recorded. A fresh candle does not waive the existing,
stricter three-second reference or five-second book freshness gates.

## Architecture and reproducibility

`strategies/momentum.py` contains new configuration and decision rules. Both models
share those common momentum rules; their identity/configuration determines whether
the regime filter applies. `models.py` registers immutable definitions, applies
separate activation state, coordinates engine instances and produces comparisons.
The existing engine, fee helpers, probability implementation, paper executor,
settlement accounting, event store and raw journal are reused.

`model-paper` creates **one collector/network client and one raw journal**. It
freezes each received event as canonical JSON and dispatches that same event to all
models. A cache shares feature/probability calculations when calculation parameters
and evaluation time match. Each engine has its own portfolio/run ID. This is shared
infrastructure, not three copies of the bot.

This repository is event-driven. The control keeps its one-second cadence and cached
intermediate quote checks. New models have a nominal 60-second calculation interval;
material quote/position events recheck their rules for safe execution. Probability
recalculations on these events are limited to once per second (or a shorter explicitly
configured interval), while price, spread and freshness checks still use each quote. Snapshot IDs,
snapshot timestamps and model calculation timestamps/ages are saved so different
cache ages remain visible. Do not interpret repeated event evaluations as independent
trials. Differences in explicitly configured probability parameters may require
separate calculations, but never separate market-data fetches.

Every new run/evaluation contains the full effective configuration. Orders, fills
and outcomes carry model ID/name/version and the full SHA-256 configuration hash;
`opportunity_id`/`trade_id` link to the immutable entry evidence. This includes market
open/close, event/ticker/side, book prices/depth/timestamps, probability, net EV,
reference/spot/returns and volatility. Fills retain simulated price, fee and debit;
results retain settlement result/time, net P/L and return on capital. Raw-source
records for every child point to the shared journal.

## Commands

Use the **same database** for registration, activation, running and reporting.
The following examples assume the default database; supply global `--database`
before the subcommand for an isolated experiment. No command below was activated
or started automatically during implementation.

```bash
uv run btc15 models list
uv run btc15 models activate conservative-confirmed-momentum:v1
uv run btc15 models activate volatility-regime-momentum:v1
uv run btc15 model-paper --run-id momentum-research
# Optional bounded run: add --seconds 900
# The model-paper service also supports --min-free-gb (default 10).
uv run btc15 paper-health --run-id momentum-research
uv run btc15 dashboard --no-collect
```

Run one writer per database. Existing `paper`/`paper-service` commands retain their
single-control behavior; use **model-paper** to evaluate active new models together
with the control. The existing user service is not modified/enabled automatically;
its reviewed `ExecStart` can use `model-paper --run-id ...` with the same environment.
SIGINT/SIGTERM drain collection and preserve checkpoints through the shared service
lifecycle. `btc15 halt` applies to every portfolio in the group.

```bash
uv run btc15 models deactivate conservative-confirmed-momentum:v1
uv run btc15 models deactivate volatility-regime-momentum:v1
```

Deactivation is read at processing-batch boundaries. It blocks entries and cancels
resting entries on revalidation; already filled positions still settle. It does not
liquidate positions. Group membership and configuration are pinned in a manifest.
Restarting the same named group resumes every original member, including deactivated
members with positions; no downtime fills are invented. Activation of an additional
model requires a **new group run ID** after existing exposure has settled and the
previous writer is stopped. This deliberately avoids silently changing experiment
membership. Per-model paper P/L/daily risk is carried forward for its exact identity.
Legacy control risk restoration retains its existing behavior.

Create a future configuration under a **new version**, never overwrite v1:

```json
{"model_id":"conservative-confirmed-momentum",
 "model_name":"Conservative Confirmed Momentum",
 "model_version":"v2", "min_move":0.002}
```

```bash
uv run btc15 models create --file /path/to/new-version.json
# Creation does not activate the version.
```

Unknown keys and invalid values fail validation. The CLI `--config` and existing
Strategies dashboard configure the control only; `models create --file` configures
new model versions. Saved full effective definitions are immutable even after later
code/default changes. Changing parameters requires a new version, not an activation
change. No strategy parameters were fitted or optimized during implementation.

Replay both new models plus the control against the same event tape, without paper
activation or credentials:

```bash
uv run btc15 model-backtest data/raw/capture.jsonl
# Explicit versions:
uv run btc15 model-backtest data/raw/capture.jsonl --models conservative-confirmed-momentum:v1
```

Replay uses the existing audit, chronological deduplication, source hashes and
input-change checks. Every member receives the audit/provenance record. Backtest
bankrolls reset per replay; reports keep separate backtest runs rather than summing
their ROI against one fictitious starting bankroll.

## Comparison and interpretation

```bash
uv run btc15 model-comparison --days 1
uv run btc15 model-comparison --days 7
uv run btc15 model-comparison --days 30
uv run btc15 model-comparison
uv run btc15 model-comparison --mode BACKTEST
# Calibration and detailed replay diagnostics for a particular model run:
uv run btc15 analytics --mode PAPER --run <group-run-id>/conservative-confirmed-momentum:v1
```

JSON output separates model ID, version, configuration hash and mode. Report fields
include bankroll/equity at cost, open debit, completed and officially settled trades,
wins/losses/win rate, realized net/average P/L, entry price, conservative probability,
edge, spread, minutes remaining, volatility, profit factor, realized maximum drawdown
and ROI. `paper_equity_at_cost` is starting capital plus completed realized P/L;
open positions are carried at cost, not assigned hypothetical wins/losses. It is not
available cash. `open_entry_debit` excludes already completed trades. It is not a
full cash reconciliation for legacy control positions with partial early exits.

Period P/L/outcome statistics use completion timestamps; entry averages and signal
counts use entry/evaluation timestamps. Open counts describe current unresolved
positions regardless of the selected period. This avoids estimating open outcomes,
but entry and completion cohorts can differ. Empty statistics are `null`, not
fabricated estimates. ROI uses configured starting capital; drawdown is realized
trade-close drawdown only. Profit factor is null when there are no losses.

Regime rows contain evaluations observed, trades entered, wins, losses, win rate and
P/L, including UNKNOWN for missing regime data. The control's legacy regime labels
are not silently reclassified as the new percentiles. Rejected evaluations can be
joined to official settlement by run/ticker for later analysis; hypothetical rejected
fills/P&L are not fabricated. More evaluations do not mean more independent markets.

The legacy analytics endpoint suppresses pooled performance when multiple model or
configuration identities are present and directs users to a specific run or the
comparison report. Existing histories are never rewritten to assign invented model
identities or to merge versions.

## Database changes and limitations

No SQL schema migration or destructive rewrite is needed. New immutable record kinds
are `model_definition`, `model_activation` and `model_group`; existing claims namespace
registration/event uniqueness. Existing records, states and paper checkpoints store
separate child run IDs. New JSON fields are additive. Legacy run hashes are retained
and used to separate old results; original checkpoints remain readable.

The one-hour volatility baseline measures local relative conditions, not a daily
or weekly regime. The selected completed minute can lag an abrupt change by nearly
one minute; the existing fresh-reference/shock gates still apply. Restart warmup,
missing ticks, delayed metadata, book staleness and insufficient history can produce
long no-trade intervals. A high modeled probability is not demonstrated calibration.
Reports currently load matching event records in memory, as existing analytics do;
large multi-day datasets will need measured storage/query work. Portfolio transitions
are atomic individually, not as one cross-model transaction. Raw journaling precedes
all decisions; a crash midway through a group event can leave uneven observations.
Use the snapshot IDs to audit matching cohorts or replay the shared journal.


## Paper activation on 2026-09-09

The user explicitly activated both v1 momentum models in the default database.
The local `momentum-paper` group now runs all three portfolios; the dashboard on
port 8001 uses `--no-collect` to view that single writer. Historical/inactive
configurations are hidden from the strategy overview, with their immutable records
retained in run history. Process IDs and logs are in `data/runtime/`; no systemd
startup service was installed. The initial group startup hit the queue limit because
momentum probabilities were recomputed on every quote. Quote-burst calculation
caching was corrected and the same group resumed with unchanged configuration hashes.
The interrupted capture should be treated as gappy research data, not a continuous
acceptance sample.
