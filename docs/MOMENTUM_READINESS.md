> **Historical archive — not executable in the current single-strategy bot.**
> For legacy recovery and the compatible revision, see [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md).

# Momentum implementation/readiness review

## Outcome

Implemented independent Conservative Confirmed Momentum v1 and Volatility-Regime
Momentum v1 research models with separate immutable settings and paper portfolios.
Both remain inactive. Project15's existing **BTC15 Settlement Edge** remains the
control: this checkout contains no Strong Momentum implementation. No Strong Momentum
strategy was invented or substituted under that name.

The new models share market-data acquisition, feature/probability and fee functions,
execution, settlement and storage. They do not own network clients or live adapters.
The [model guide](MOMENTUM_MODELS.md) documents exact filters, safeguards, commands,
causality, restart behavior and assumptions.

## Safety and current model list

Local settings inspection returned `mode=PAPER` and `enable_live_trading=false`.
The settings guard and `LiveTrader.submit` still fail closed. The paper executor
additionally rejects non-PAPER/non-BACKTEST modes; momentum configuration rejects
any mode other than its paper-only designation. BACKTEST is an offline execution
mode, not a live activation setting. No real orders, authenticated captures, service
activation, credential edits or model activation were performed for this task.

`btc15 models list` returned:

| Model | Version | Configured state | Initial paper capital |
| --- | --- | --- | --- |
| BTC15 Settlement Edge | v1 + current config hash | Enabled control | $1,000 |
| Conservative Confirmed Momentum | v1 | Inactive | $1,000 |
| Volatility-Regime Momentum | v1 | Inactive | $1,000 |

Enabled configuration does not mean a service is running. Existing services were
not started, stopped or reconfigured. All activation commands are explicit and
paper-only. Use the new `model-paper --run-id NAME` workflow for shared comparisons;
existing `paper`/`paper-service` retain their control-only workflow.

## Architecture and persistence

Each model uses a separate instance of the same engine/executor, with distinct run,
claim, state and checkpoint identities. A group freezes each fetched raw event once
and supplies the same immutable contents/ID/time to all members. Compatible model
calculations reuse a cycle cache; probability calculation times/ages remain recorded.
The control's one-second cadence and intermediate cached checks are preserved.

The group manifest freezes membership/configurations for resume. New definitions
are inactive; changes require new versions. Deactivation blocks entries and retains
settlement handling. A shared halt reaches every member. Resuming restores every
original member even if later deactivated, and never fabricates downtime fills.

No SQL table migration, data deletion or historical rewrite is required. Definitions,
activation events and group manifests use new immutable record kinds in the existing
ledger. Existing claim uniqueness and atomic checkpoint infrastructure are reused.
Entry evidence stores full configurations; fill/order/result records carry model
identity/version/hash and links to entry records. The comparison report separates
hashes and versions; legacy aggregate analytics suppress misleading pooled results.

## Volatility decision

Use the existing one-hour standard BRTI buffer. Measure the latest complete UTC
minute as `sqrt(60 * sum(log returns squared) / elapsed sample seconds)`. Rank it
against valid complete minutes in the preceding 59-minute interval, excluding the
current sample. Require 30 historical samples; actual available count is recorded.
Midrank percentile is `100 * (below + 0.5 * equal) / N`. Thresholds are configurable:
LOW [0,20), NORMAL [20,75), HIGH [75,90), EXTREME [90,100]. v1 allows only NORMAL.
This requires roughly 31–32 minutes of cold-start warmup and no extra API calls.
Incomplete minutes, gaps, future/unreceived ticks and invalid prices cannot create
invented history. See the guide for endpoint tolerance and exact gap rules.

## Verification

- Full offline suite: **160 passed**, including original control tests, boundary,
  symmetry, volatility, immutable versioning, independent trades/bankrolls,
  shared-snapshot/cached-computation, future-reference rejection, collector/resume/halt
  and reporting tests.
- The integrated synthetic test exercises actual features/probability through
  momentum entry, aggressive fill and official settlement accounting.
- Ruff lint: passed for `src tests`.
- Ruff formatting: passed for all ten Python files changed by this task. The full
  repository format check reports existing formatting differences in
  `tests/test_dashboard_markets.py`; that unrelated file was left untouched.
- Python compile check and `git diff --check`: passed.
- Two existing third-party Starlette/AnyIO deprecation warnings remain.

The sandbox stalls asyncio thread notifications (also reproducible with a minimal
`asyncio.to_thread` example). The full offline suite ran successfully outside that
sandbox using mocked network services. This was not a live connectivity or order
submission test. No profitability or multi-day operational readiness claim follows
from these tests.

## Sample model comparison

Actual output generated against an isolated **empty review database**, not fabricated
trading performance; the full JSON is [MOMENTUM_REPORT_SAMPLE.json](MOMENTUM_REPORT_SAMPLE.json).

| Model | Version | State | Initial capital | Settled | Open | Win rate | Net P/L |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC15 Settlement Edge | v1 | control | $1,000 | 0 | 0 | null | $0 |
| Conservative Confirmed Momentum | v1 | inactive | $1,000 | 0 | 0 | null | $0 |
| Volatility-Regime Momentum | v1 | inactive | $1,000 | 0 | 0 | null | $0 |

`model-comparison --days 1`, `--days 7`, `--days 30`, and no period option provide
24-hour, seven-day, thirty-day and all-time views. No open position is assigned an
estimated result. Entry and completion cohorts use their respective timestamps;
open counts are current. Drawdown is realized only; profit factor is null without
losses. Per-regime rows retain observed signals and entered/completed trade metrics.

## Exact files changed by this task

This working tree already contained substantial user changes. Those were preserved;
the full `git diff` includes earlier work and should not be attributed to this task.

| File | Change |
| --- | --- |
| `README.md` | Link new independent research workflow; remove obsolete single-strategy claim |
| `src/btc15/strategies/momentum.py` | New momentum definitions, validation, causal observations and rules |
| `src/btc15/models.py` | New immutable registry, activation, shared group and comparison report |
| `src/btc15/engine.py` | Rule dispatch, compatible calculation cache, snapshot/identity evidence |
| `src/btc15/execution.py` | Portfolio identity isolation, paper-only guard, event claims, result/debit evidence |
| `src/btc15/runner.py` | Optional multi-model collection, shared raw provenance, halt/checkpoint/status integration |
| `src/btc15/operation.py` | Reuse managed shutdown/disk reserve for multi-model service |
| `src/btc15/research.py` | Shared-tape multi-model replay and per-member audit records |
| `src/btc15/analytics.py` | Suppress mixed-model/config pooled analytics |
| `src/btc15/cli.py` | Model creation/activation/list, paper/replay and comparison commands |
| `tests/test_momentum_models.py` | New offline strategy, integration, isolation and reporting tests |
| `docs/MOMENTUM_MODELS.md` | Rules, assumptions, operation, versioning and methodology guide |
| `docs/MOMENTUM_READINESS.md` | This review |
| `docs/MOMENTUM_REPORT_SAMPLE.json` | Isolated empty-database report example |

No dependency, credential, live-limit or deployed-service changes were made. The
existing control's strategy configuration/model/rules modules were not changed.

## Limitations and requested-rule review

IMPLEMENTED AS REQUESTED:
The new thresholds and NORMAL-only hypothesis are retained without optimization.
Models are independently configurable/versioned and paper-only. Settlement Edge
remains the actual available control. Shared BRTI replaces the request's assumption
that this project already has Coinbase ingestion; no proxy feed is added.

RECOMMENDED FUTURE CHANGE:
After observing enough independent markets, consider a longer, explicitly recorded
volatility baseline under a new version and compare actual signal-matched cohorts.

WHY:
One hour is available reliably in the existing causal architecture, but it measures
local conditions; a whole high-volatility hour can still produce locally NORMAL
minutes. The latest completed minute can lag a new shock. The control and new
models also retain different evaluation/cache cadences for control preservation.

EXPECTED BENEFIT:
Better distinction between local variation and sustained volatility, and clearer
attribution of differences between model choices and observation timing.

POSSIBLE DOWNSIDE:
Longer warmup, more missing-history risk/storage, fewer comparable observations,
and another hypothesis requiring independent validation. No longer-baseline variant
was implemented or optimized.

Other explicit limitations: new models assume one-contract taker fills at the next
eligible recorded ask with zero additional slippage and hold to official settlement.
Paper fills do not establish live execution quality. Proxy edge is tested but there
is no Coinbase ingestion path. Reports load records in memory. Rejected signals
retain official-outcome join keys, but hypothetical fills/P&L are not invented.
Cross-model commits are not one transaction; journal replay/snapshot matching is
needed after a crash midway through group processing. Existing early-exit control
portfolios have more complex cash accounting than the new settlement-only portfolios;
report equity at cost is not spendable cash or a full partial-exit reconciliation.
The control's historical daily-risk carry-forward policy is preserved, while reports
partition performance by exact configuration hash.

## NEXT RECOMMENDED EXPERIMENTS

Only after enough paper data; no additional strategies were implemented:

1. **Probability calibration and net EV:** compare predicted probabilities with
   official outcomes on held-out markets; use market/day cohorts instead of treating
   repeated scans as independent evidence.
2. **Fill and cost sensitivity:** replay the same captures with predeclared adverse
   slippage/latency assumptions under new versions; measure fill rate, ROI and drawdown.
3. **Regime stability:** compare a predeclared longer history against this one-hour
   baseline, using matched snapshots and adequate counts per regime.
4. **Rejection analysis:** join rejected signals to official results, inspect the
   highest-frequency binding filters, then test one predeclared change at a time.
5. **Time stability and operations:** compare independent daily/weekly cohorts and
   exercise restart/rollover/settlement recovery with open paper positions over
   consecutive days before drawing readiness conclusions.
