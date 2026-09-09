> Historical initial implementation review. The follow-up hardening work supersedes
> the implementation-gap and readiness claims below. See
> [RESEARCH_HARDENING.md](RESEARCH_HARDENING.md) and [VALIDATION.md](VALIDATION.md)
> for current changes and measured evidence. Initial findings are retained for audit.

# Implementation review — 2026-09-08

This is a working research V1 built in an initially empty repository. It does not
complete every production requirement in the request. No live orders were sent,
no real paper strategy performance was measured, and no profitability claim is
supported. AGENTS.md was treated as engineering guidance; the pasted request,
preserved in REQUEST.md, defined the implementation scope.

## A. What was implemented

- Python package/CLI, locked dependencies, environment example, PostgreSQL Compose
  setup, SQLite local alternative, documented architecture before implementation.
- Read-only Kalshi client with RSA-PSS/SHA256 authentication, bounded GET retries,
  pagination, current/next BTC15 discovery using metadata shard routing; a strict
  market validator and persisted pending/invalid contracts.
- Authenticated WebSocket collector for standard/5 Hz BRTI, binary order books,
  public trades, ticker and lifecycle events. Explicit unified YES-leg book pricing,
  per-subscription sequences, connection events, stale/error handling, reconnect
  and snapshot recovery. Historical CF download and read-only account reconciliation.
- Durable fsync raw journal, atomic compressed Parquet chunks; operational immutable
  records with indexes, state transitions, unique intent claims and writer leases.
- Settlement specification with source, instrument, window/grid, comparator, strike,
  precision and rules hash. Seeded Monte Carlo with fixed observed final-minute
  samples, missing-observation rejection, raw probability, uncertainty and separate
  diagnostic quality. Causal volatility, minute indicators, momentum and regime data.
- Executable-side prices, fractional quantities, dynamic tick snapping, explicit
  binary EV after estimated fees/slippage, structured signal/rejection records and
  separate execution guard. All configs and model/Git/source versions are retained;
  complete source/lock snapshots are attached to runs.
- Paper execution with latency, queue/traded-volume constraints, partial fills,
  duplicate prevention, signal revalidation and cancellation, conservative aggressive
  IOC fills, TP/invalidation/hard-stop exits, depth accounting and official settlement.
- Fixed-contract/dollar/bankroll sizing with worst-case trade, bankroll, open/daily
  exposure, daily net loss and daily attempt limits; restart restoration of closed
  paper-run budgets; kill switch and crash lease protection.
- Shared-engine replay, isolated counterfactual runs with input hashes, small
  predeclared walk-forward comparisons with chronological holdouts and minimum
  training counts. Synthetic end-to-end demonstration with fractional partial fills.
- Calibration/Brier/log loss/ECE, market-level and all-prediction summaries, P&L,
  drawdown, fill rates, streaks, reasons and selected grouped analytics. Structured
  JSON APIs and dated AI-readable trade packets with Parquet paths.
- Responsive local dashboard with mode/run filters, monitor, searchable opportunity
  and completed-trade tables, replay/timeline/charts, calibration/returns and JSON.
- V2 live payload builder and an explicitly disabled live submission boundary.

## B. What was not implemented or is incomplete

1. **Production LIVE execution:** no POST/DELETE trading path, persistent live
   reconciliation state machine, uncertain-submission recovery, live collateral,
   exchange order-group integration, live kill/cancel race handling or activation.
2. **Authenticated validation:** no account credentials were supplied. WebSocket
   entitlements, exact account payloads, reconnect/rollover behavior and historical
   CF entitlement were not exercised against a real account. Read-only public
   discovery was exercised successfully.
3. **Empirical strategy validation:** no historical synchronized reference/book/trade
   dataset was supplied or collected with credentials. No fitted calibration,
   out-of-sample market findings, real fill comparisons or long-running paper record.
4. **Paper crash resume:** fills are persisted, but in-memory order/position projections
   do not recover automatically. Startup refuses unresolved earlier paper positions.
   A crash between multiple related SQL writes can require manual forensic recovery.
5. **Execution completeness:** no automatic cancel/replace/repricing, calibrated queue
   depletion model, hidden liquidity, exact market-impact model, passive exit queue,
   exchange reconciliation of paper queue assumptions, or optimized urgency policy.
   One entry attempt per market is intentionally conservative.
6. **Model completeness:** no Student-t/bootstrap/jump/GARCH ensemble or ML; no
   automatically fitted calibration; 5 Hz observations are recorded but not used
   for model estimation. Minute ATR/RSI use explicit simple-window definitions.
   Model-quality weights and uncertainty allowances are assumptions.
7. **Fees:** current series and effective event overrides are checked, but scheduled
   series fee changes and account-specific alignment/discounts are not completely
   resolved. Fee coefficients are research assumptions; confirm against account fills.
8. **Research/UI breadth:** no block-bootstrap intervals, purged statistical
   cross-validation, all requested breakdown charts, automatic process letter grade,
   export CSV command, or every entry/exit/settlement-window chart overlay. Core
   structured data is available for additional analysis. Sharpe is explicitly null
   because irregular small trade samples do not justify an annualized estimate.
9. **Operations:** no schema migration tool, authenticated multi-user dashboard,
   retention policy, incremental large-dataset analytics or recorder throughput/
   disk-failure soak testing. The UI is intended for localhost use.

These are explicit gaps, not claims of completed production functionality.

## C. Significant assumptions

`strategies/settlement_edge/config.py` defines and validates the built-in
assumptions; `config/defaults.json` is an explicitly selectable example. Effective
CLI settings come from `--config`, then `DATA_DIR/strategy.json`, then built-in
defaults. The Strategies UI saves settings for new sessions only. None is
represented as optimal. See [the strategy guide](STRATEGY.md).

- Zero log drift, independent Gaussian increments, maximum past RV/EWMA sigma,
  1e-5 volatility floor, .97 EWMA decay, 300-second warmup, 4,000 seeded paths.
- A one-second settlement sample grid indexed by source time, based on documented
  standard-feed semantics; exact subsecond offsets need observed reconciliation.
  Half-cent rounding ties remain bracketed rather than assumed official.
- Uncalibrated probability allowance .02, Wilson simulation allowance, volatility
  disagreement allowance, quality deduction weights and fixed regime thresholds.
- Entry 2–8 minutes, .85–.99 prices, .90 conservative probability, quality 85,
  .03 edge/EV, .04 spread and five-contract visible liquidity minimum.
- .01 maximum passive discount bounded by spread/edge, 20-second patience,
  .25-second latency, 1.5 queue multiplier, .002 slippage, no beneficial inference
  from quote cancellation, one entry attempt and no automatic repricing.
- .07 taker and .0175 maker quadratic coefficients and .01 account alignment;
  entry uses a conservative taker/rounding cost bound. Maker coefficient can
  overcharge a maker-free market; it is not a verified BTC15-specific fee claim.
- $1,000 simulated bankroll, five target contracts, ten-contract/$10 trade cap,
  1% bankroll cap, $20 concurrent/$100 daily exposure, $30 daily loss, 20 attempts.
  Cancelled attempts retain daily budget use. All are tunable assumptions.
- .99 TP, .75 catastrophic stop multiplier, .70 probability exit threshold,
  zero minimum hold EV. Stops are not guaranteed.
- Bollinger 20 periods/2 standard deviations; RSI and stochastic windows 14;
  ATR simple 14-period average. These secondary features cannot initiate a trade.
- Daily accounting uses UTC; host wall clock is compared coarsely with HTTP Date
  and source timestamps. Backend monotonic timing detects local clock jumps.

## D. API and data limitations / differences

Public metadata confirmed BRTI, final 60 samples, two-decimal rounding and YES
including equality. `close_time` is the underlying/trading boundary; the captured
`expiration_time` was seven days later, and expected expiration five minutes later.
Upcoming contracts can have null strike/strike type. A future market cannot be
made tradable by inferring an opening reference. Metadata reports shard 2.

The standard feed's quarter-hour accumulation is (close−60s, close], unlike its
trailing average. The separate 5 Hz feed must not change settlement sample count.
Contract terms describe unavailable/incomplete underlying contingencies; a local
feed gap does not prove that contingency occurred. Settlement always waits for
Kalshi's result. Half-cent tie rounding remains unconfirmed.

Prices and quantities are fixed-point strings, with dynamic piecewise ticks.
V2 order directions use unified YES-leg prices. Authentication is RSA-PSS, not
username/password. Historical CF data is documented but entitlement-gated; contract
candles are insufficient for realistic passive-fill replay. Sources and captured
fixtures are indexed in SETTLEMENT_MODEL.md. API rate limits differ by endpoint;
V1 uses bounded backoff, not a full token-budget scheduler.

## E. Strategy concerns

Gaussian paths can understate jumps and regime transitions. High raw probabilities
are especially sensitive to misestimated tails. A fixed two-point penalty does not
establish calibration. The .85 entry floor, 2–8-minute window, .03 edge and .75 stop
have no supporting performance evidence here. Fees and missed/partial fills may
eliminate apparent edge. Passive fills select adverse price moves and cannot be
validated from touched quotes. Stops may realize repeated spread/fee losses without
preventing the largest event losses. Observe calibration before optimizing returns.

No rules were silently optimized to make the synthetic backtest profitable. The
demo is designed to exercise the accounting path and has no statistical significance.

## F. Engineering concerns

- Receive timestamps describe when the application dequeues/processes data, not
  kernel arrival. Synchronous simulation/SQL/fsync and periodic REST refresh can
  delay ingestion. Source freshness checks reject delayed inputs, but sustained
  performance requires measurement and bounded collector/model separation.
- Raw data is durable before SQL evaluation, but raw/SQL/position writes are not
  an atomic ledger. Unique claims and crash leases prevent blind retry; they do
  not replace reconciliation. Open-paper restart is deliberately blocked.
- Metadata pauses and reconnects can conservatively cancel paper orders and reduce
  fills. Feed error handling requires account-based soak tests. Subscription sequence
  semantics were implemented from documentation and tested with fixtures only.
- SQLite is suitable for local research; PostgreSQL is the operational target.
  Dashboard queries, full-tape reads and JSON snapshots need scale measurement.
- Unchanged-depth reuse is prevented conservatively; actual hidden replenishment
  can make simulated exits too pessimistic. Neither optimistic nor pessimistic
  fill assumptions should be mistaken for measured queue behavior.
- Dashboard timestamps are historical evaluation timestamps. Freshness is exposed;
  frontend clocks have no role in order decisions. The API is local/read-only.

## G. Recommended improvements

**CRITICAL:** authenticated capture/rollover/recovery soak; official sample/average
and fee reconciliation; append-only atomic execution ledger and crash reconstruction;
independent multi-day calibration dataset. Keep LIVE blocked.

**HIGH PRIORITY:** measured queue/latency/slippage, disk/DB failure testing, token
rate-budget accounting, scheduled series fee refresh, block-bootstrap intervals,
process monitoring and alerting.

**MEDIUM PRIORITY:** incremental Parquet replay/analytics, additional breakdown
charts and replay overlays, migrations/retention, carefully tested cancel/replace.

**EXPERIMENTAL:** residual bootstrap/Student-t tails, regime-conditioned sigma,
calibration maps trained only on earlier periods, and exit/window/edge variants.
None should be automatically applied to a running strategy.

## H. Recommended experiments

| Hypothesis | Why it may help | Required data | Backtest method | Paper method | Success metric |
| --- | --- | --- | --- | --- | --- |
| Empirical/Student-t innovations improve tail forecasts | Gaussian tails may miss shocks | Consecutive official ticks and settled markets across regimes | Fit on earlier days; score frozen next-day probabilities | Shadow probabilities beside V1 without changing entries | Lower held-out log loss/Brier, reliable high-P buckets with block intervals |
| A higher volatility floor reduces overcertainty in quiet periods | Flat ticks may understate remaining risk | Quiet-period reference paths and outcomes | Predeclared 1e-5 vs 2e-5 floor, rolling chronological holds | Shadow both models on at least 100 independent markets | Calibration improves without merely suppressing all signals |
| Increasing minimum net edge from .03 to .04 improves realized EV | Small edges may not cover execution error | All accepted/rejected signals and queue-aware fills | Replay same contiguous tapes, fixed costs, out-of-sample comparison | Separate labelled shadow runs, at least 100 candidates each | Higher net EV with uncertainty bounds and adequate fill counts |
| Changing the .85 price floor or entry minute improves calibration/EV | Current filters are unsupported | Full opportunity memory including rejections | Staged .85/.89 and 8/6/4-minute comparisons; held-out days | Fixed configurations in separate runs; no in-run tuning | Stable calibration and net return across adjacent settings |
| Alternative exits outperform the .75 price stop | Spread and noisy marks may cause unnecessary losses | Reference/book paths through settlement, all exit depths | Compare hold, invalidation and fixed stops with identical conservative fills | Shadow exit plans on the same paper entries | Better downside distribution and net EV, including failed exits |
| Queue/latency assumptions materially change apparent edge | Passive adverse selection is underobserved | Authenticated sequence-complete books/trades and measured receipt delays | Sweep latency .1/.25/1s and queue multipliers 1.5/2/3 on holdouts | Record estimated queue and fill opportunity for each resting paper order | Conclusions remain robust across plausible pessimistic execution assumptions |

No historical evidence was available to choose among these hypotheses. Preserve
insufficient-data and negative results. Real paper validation and explicit user
review remain mandatory before a live change.

## Validation and readiness

See `VALIDATION.md` for the executed checks and environment limitations.
Public discovery: **READY**. Authenticated collection: **READY WITH LIMITATIONS**.
Replay/backtesting software: **READY WITH LIMITATIONS**. Paper simulator:
**READY WITH LIMITATIONS**. Autonomous production paper operation: **NOT READY**.
Live trading: **NOT READY**. Economic edge/profitability: **UNDETERMINED**.

Additional experimental hypothesis: a regime-conditioned sigma or an isotonic
calibration map trained on earlier independent markets may reduce systematic
probability bias. Required data: multi-regime official ticks, immutable forecasts
and outcomes. Backtest: train only on earlier blocks, compare raw and adjusted
scores on the next block with sufficient per-regime counts. Paper method: shadow
both estimates without changing production settings. Success metric: improved
held-out Brier/log loss and reliability with stable market-block intervals, not
higher in-sample win rate.
