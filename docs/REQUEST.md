# PROJECT: Kalshi BTC15 Settlement Edge Trading System

You are acting as the lead quantitative developer, trading-systems engineer, backend engineer, data engineer, and frontend engineer for this project.

Your job is to design and implement a robust, production-oriented automated trading research system focused ONLY on Kalshi's 15-minute Bitcoin prediction markets.

Do NOT treat this as a simple indicator bot.

The system's core purpose is:

> Wait until late in a Kalshi BTC 15-minute market, estimate the probability of the actual Kalshi settlement outcome using the same Bitcoin reference methodology used by the contract, compare that probability with the executable Kalshi market price after fees and execution costs, and trade only when a sufficiently large positive expected-value opportunity exists.

The initial priority is NOT live trading.

Priorities, in order:

1. Correctness and accurate data handling
2. Data collection and researchability
3. Probability calibration
4. Realistic backtesting
5. Realistic paper trading
6. Risk and execution safety
7. Strategy optimization
8. Live trading only after explicit user approval

Do not optimize for impressive-looking win rates.

Do not claim profitability.

Do not invent undocumented Kalshi rules.

Do not silently alter strategy assumptions.

If you discover during implementation that something in this specification is incorrect, outdated, technically infeasible, mathematically questionable, or could be substantially improved, implement the safest reasonable interpretation when possible and document the issue in the final implementation report.

---

# 1. IMPORTANT DEVELOPMENT BEHAVIOR

First inspect the existing repository before making changes.

Understand:

* current project structure
* programming language
* package management
* frontend stack
* backend stack
* existing database
* current environment configuration
* existing API integrations
* testing infrastructure

Reuse existing architecture where reasonable instead of unnecessarily rewriting the project.

If this is a new repository, create a clean architecture suitable for this specification.

Before substantial implementation, create or update:

`docs/ARCHITECTURE.md`

and include:

* component architecture
* data flows
* state machine
* persistence design
* probability-model design
* execution architecture
* paper-trading architecture
* live-trading safety model

Then implement the system.

Do not stop after producing an architecture document.

Continue through implementation as far as the environment permits.

---

# 2. STRATEGY PHILOSOPHY

This strategy is NOT:

> Predict whether Bitcoin will go up or down during the next 15 minutes.

It is:

> With only a few minutes remaining, determine whether Bitcoin is sufficiently far from the settlement strike, relative to remaining volatility and the Kalshi settlement mechanism, that one outcome is materially more probable than the executable Kalshi contract price implies.

The central relationship is:

P(settlement outcome) = f(
settlement reference price,
strike,
time remaining,
realized volatility,
expected remaining volatility,
short-term price behavior,
settlement averaging mechanics,
market regime
)

Then compare:

conservative_model_probability

against:

all_in_executable_market_probability

Trade only when the difference is sufficiently positive.

---

# 3. MARKET SCOPE

ONLY target:

Kalshi BTC 15-minute markets.

Do not trade other Kalshi markets.

Implement reliable automatic discovery of the currently active and next BTC15 markets.

For every discovered market determine:

* ticker
* event ID if relevant
* market ID
* contract title
* exact YES condition
* exact settlement rule
* expiration time
* settlement period
* strike/reference value
* market status
* valid price tick structure
* whether trading is currently permitted

Never infer YES/NO meaning from assumptions.

Parse the actual contract wording.

Normalize internally into a canonical representation such as:

YES = settlement result satisfying Kalshi YES condition
NO = complementary outcome

---

# 4. SETTLEMENT DATA — CRITICAL

Use the exact settlement/reference methodology specified by the active Kalshi BTC15 contract.

Current design assumption:

Kalshi BTC15 crypto contracts use CF Benchmarks BRTI-related data and settle based on the average of observations during the final settlement minute.

However:

DO NOT hard-code this without validating the current contract/API documentation.

At runtime or implementation time, verify:

* exact CF Benchmarks instrument
* exact averaging methodology
* exact observation interval
* exact final settlement window
* rounding rules
* strike comparison semantics
* tie behavior if applicable

If Kalshi changes the methodology, isolate it behind a settlement-model abstraction.

Create something conceptually similar to:

`SettlementSpecification`

with fields such as:

* reference_source
* index_name
* averaging_window_seconds
* sample_frequency
* comparison_operator
* strike
* settlement_start
* settlement_end

Do NOT substitute Coinbase, Binance, Kraken, etc. for settlement truth.

Secondary exchanges may eventually be used as optional informational features, but must never replace the official settlement reference.

---

# 5. DATA SOURCES

## Kalshi

Use Kalshi for:

* market discovery
* market metadata
* strike information
* expiration
* contract wording
* order book
* ticker
* trades
* lifecycle events
* order submission
* order status
* fills
* position reconciliation
* fees where available
* settlement results

Prefer WebSocket/event-driven data for live operation where supported.

REST may be used for:

* initial snapshots
* historical retrieval
* reconciliation
* recovery
* metadata

## CF Benchmarks / official settlement feed

Use the official Kalshi-supported CF Benchmarks data path wherever available.

Capture high-frequency BRTI/reference values.

If Kalshi exposes 5 Hz reference data, support it.

Do not design the system around continuously polling REST when a suitable WebSocket channel exists.

---

# 6. RAW DATA RECORDER — IMPLEMENT EARLY

Build the raw market-data recorder as one of the first components.

This is critical.

Persist sufficient information to reconstruct historical trading decisions.

Capture, where available:

* CF Benchmarks/reference ticks
* Kalshi order-book snapshots
* Kalshi order-book deltas
* Kalshi ticker updates
* Kalshi trades
* market lifecycle events
* timestamps
* sequence numbers
* connection/reconnection events
* stale-data events

High-frequency raw datasets should be stored efficiently.

Recommended hybrid:

PostgreSQL:

* structured operational records
* markets
* trades
* decisions
* orders
* positions
* analytics metadata

Parquet:

* high-frequency reference ticks
* order-book event streams
* probability paths
* large historical replay datasets

If another architecture is materially better, document why before using it.

---

# 7. ENTRY WINDOW

Default candidate entry window:

2 minutes < time remaining <= 8 minutes

Configurable:

`ENTRY_WINDOW_START_SECONDS = 480`
`NO_NEW_ENTRY_SECONDS = 120`

Do not normally enter during the first approximately seven minutes.

Never submit a new order if the market enters the no-entry window during signal evaluation/order preparation.

Re-check time immediately before submission.

---

# 8. FAVORED SIDE

Using the official reference price:

if reference_price > strike:
favored_side = YES

if reference_price < strike:
favored_side = NO

But note:

Favored side alone does NOT create a trade.

Calculate:

signed_distance
absolute_distance
distance_pct

Example:

signed_distance = reference_price - strike

Use positive/negative direction consistently.

---

# 9. PROBABILITY MODEL — CORE OF THE SYSTEM

Do NOT create an arbitrary weighted "confidence formula" as the main probability calculation.

Build an explicit settlement-probability model.

Initial recommended V1:

Monte Carlo / statistical simulation of the remaining Bitcoin settlement path.

Estimate:

P(YES settles)

and:

P(NO settles) = 1 - P(YES settles)

The model should simulate the ACTUAL settlement rule rather than only the terminal BTC value.

For each simulated path:

1. Begin from the latest official reference price.
2. Simulate future short-horizon BTC/reference movement.
3. Continue through the settlement averaging window.
4. Compute the same settlement average Kalshi would conceptually use.
5. Evaluate the simulated average against the strike.
6. Record YES or NO result.

Then:

P(YES) =
YES simulations / total simulations

Do not introduce look-ahead bias.

Do not use future volatility.

---

# 10. INITIAL VOLATILITY MODEL

Create a modular volatility estimator using configurable combinations of:

* high-frequency realized volatility
* 30-second realized volatility
* 60-second realized volatility
* 3-minute realized volatility
* 5-minute realized volatility
* EWMA volatility
* 1-minute ATR
* maximum recent one-minute shock
* recent jump magnitude

Do not assume Gaussian returns are perfectly accurate.

For V1, a simple transparent model is acceptable.

Architecture should allow later replacement with:

* empirical residual bootstrap
* Student-t innovations
* regime-conditioned distributions
* jump diffusion
* GARCH-type models
* machine-learning probability estimators
* ensemble models

Document all assumptions.

---

# 11. SETTLEMENT-AWARE FINAL MINUTE

This is important.

If the bot is already inside the settlement averaging window, some observations are known.

Do not resimulate observations that have already occurred.

Conceptually:

settlement_average =
(
sum(observed_settlement_samples)
+
sum(simulated_remaining_samples)
)
/
total_required_samples

As more settlement samples become known, model uncertainty should decline.

Use this for:

* position monitoring
* probability invalidation
* settlement forecasting
* emergency exits

---

# 12. PROBABILITY VS MODEL QUALITY

Maintain TWO separate concepts.

## Probability

Example:

`P_YES = 0.958`

This should represent the model's calibrated estimate of actual settlement probability.

## Model Quality Score

Example:

`MODEL_QUALITY = 91 / 100`

This represents how much we trust the current probability estimate.

Quality may be reduced by:

* stale data
* missing packets
* incomplete reference feed
* regime outside historical distribution
* extreme short-term shock
* volatility-model disagreement
* insufficient recent data
* degraded probability calibration
* abnormal market conditions
* unstable simulation output

DO NOT call probability "confidence."

Use precise terminology.

---

# 13. MODEL UNCERTAINTY

Produce:

* raw probability
* uncertainty estimate
* conservative probability

Example:

Raw P(YES): 95.7%
Uncertainty penalty: 1.8%
Conservative P(YES): 93.9%

Trade using the conservative estimate.

Initial conceptual form:

conservative_probability =
model_probability - uncertainty_penalty

The exact uncertainty methodology should be transparent and configurable.

Potential uncertainty sources:

* statistical estimation error
* probability calibration error
* simulation uncertainty
* volatility-model disagreement
* regime uncertainty
* data-quality penalties

Clamp probabilities to valid [0,1] range.

---

# 14. TECHNICAL INDICATORS

Use 1-minute chart-derived indicators as SECONDARY features.

Support:

* Bollinger Bands
* Stochastic RSI
* ATR

All settings configurable.

Do not pretend proprietary/original creator settings are known.

## Bollinger Bands

Track:

* upper
* middle
* lower
* position within band structure
* distance outside band if applicable

Use as:

* reversal-risk context
* regime context

Do NOT allow Bollinger Bands alone to trigger trades.

## Stochastic RSI

Use for:

* overbought/oversold context
* reversal-risk adjustment

Do NOT allow Stochastic RSI alone to trigger trades.

## ATR

Use primarily for volatility context and comparison.

Do not rely solely on:

distance / ATR

when settlement-probability modeling provides a more meaningful measure.

---

# 15. ADDITIONAL PRICE FEATURES

Calculate configurable short-term features such as:

* 5-second momentum
* 15-second momentum
* 30-second momentum
* 60-second momentum
* 3-minute momentum
* 5-minute momentum
* return acceleration
* consecutive directional movement
* maximum adverse move
* maximum favorable move
* recent candle direction
* extreme shock detection

Do not overfit an excessive number of features into V1.

Record them for later research even if they are not initially used.

---

# 16. MARKET MICROSTRUCTURE

Track:

* YES bid
* YES ask
* NO bid
* NO ask
* midpoint
* spread
* depth
* top-N book depth
* recent trades
* trade direction if derivable
* order-book imbalance
* contract-price velocity

Normalize Kalshi's market representation internally.

The application should expose:

YES_BID
YES_ASK
NO_BID
NO_ASK

regardless of how Kalshi's raw API encodes binary order books.

Be extremely careful with complement pricing.

Write tests proving that:

buy YES
sell YES
buy NO
sell NO

are interpreted correctly.

---

# 17. DO NOT MAKE KALSHI PRICE THE PRIMARY PROBABILITY INPUT

The settlement model should be independently grounded primarily in BTC/reference dynamics.

Do not create a circular model where:

Kalshi says 91%
→ probability model becomes 94%
→ bot claims edge over Kalshi.

Keep conceptually separate:

`P_MODEL`

and:

`P_MARKET`

Order-book information may influence:

* execution quality
* liquidity filter
* model-quality score
* residual research models later

But not dominate the base settlement probability.

---

# 18. IMPLIED MARKET PROBABILITY

Use the executable price rather than merely midpoint where appropriate.

For buying YES:

market_cost should reflect executable YES ask or realistically modeled entry.

For buying NO:

market_cost should reflect executable NO ask or equivalent valid Kalshi representation.

Include:

* expected fees
* spread
* expected slippage
* partial-fill cost
* execution assumptions

Compute:

raw_edge =
conservative_probability - expected_fill_price

Then compute an execution-adjusted EV/edge.

Do this using a mathematically correct binary-contract expected-value formula rather than relying only on probability subtraction.

For a contract purchased for price `c`, settlement payoff 1 if correct and 0 if wrong:

EV before costs =
p * (1 - c) - (1 - p) * c

which simplifies to:

EV = p - c

but actual implementation must then subtract:

* applicable fees
* slippage
* execution costs

Use the exact Kalshi fee schedule/API data where possible.

---

# 19. CONTRACT-PRICE FILTER

Retain configurable price filters as strategy constraints.

Suggested defaults to test:

`MIN_ENTRY_PRICE = 0.85`

Also support:

`MAX_ENTRY_PRICE`

Do not assume a more expensive contract is always safer/better.

Examples:

Ask = .86
Model P = .87
→ likely NO TRADE

Ask = .89
Model P = .96
→ potential trade

Ask = .97
Model P = .975
→ likely too little edge after costs

Primary criterion is EV, not high contract price.

---

# 20. ENTRY CONDITIONS

A candidate trade should require all applicable conditions.

Conceptual logic:

market_is_valid
AND market_is_open
AND correct_contract_identified
AND 120 < seconds_remaining <= 480
AND reference_data_is_fresh
AND kalshi_data_is_fresh
AND favored_side_identified
AND model_quality >= MIN_MODEL_QUALITY
AND favored_ask >= MIN_ENTRY_PRICE
AND favored_ask <= MAX_ENTRY_PRICE
AND conservative_probability >= MIN_WIN_PROBABILITY
AND net_expected_value >= MIN_EXPECTED_VALUE
AND estimated_edge >= MIN_EDGE
AND volatility_regime_allowed
AND spread <= MAX_SPREAD
AND liquidity >= MIN_LIQUIDITY
AND no_reversal_emergency
AND no_risk_limit_violation

Then:

generate TRADE_CANDIDATE

The signal engine MUST NOT directly place orders.

---

# 21. STATE MACHINE

Implement an explicit state machine.

Recommended states:

DISCOVER_MARKET
VALIDATE_MARKET
WARMUP
MONITORING
ENTRY_WINDOW
EVALUATING
NO_TRADE
TRADE_CANDIDATE
ORDER_PENDING
ORDER_PARTIALLY_FILLED
ORDER_CANCELLED
POSITION_OPEN
TAKE_PROFIT_PENDING
STOP_MONITORING
INVALIDATION_EXIT
HARD_STOP_EXIT
EXITING
SETTLEMENT_PENDING
CLOSED
ERROR
HALTED

Persist every meaningful state transition.

Prevent:

* duplicate entry
* duplicate exit
* accidental order retry
* stale-market orders
* orders after market expiration
* concurrent conflicting state transitions

---

# 22. PASSIVE ORDER LOGIC

Support passive limit entry.

Example concept:

best ask = .92

possible passive bid = .89

But do NOT blindly use a constant discount.

Calculate from:

* spread
* current depth
* probability edge
* remaining time
* expected price evolution
* tick size
* execution urgency

Configurable:

* passive-order mode
* discount
* maximum waiting time
* cancel/replace policy
* aggressive-entry threshold

Never chase the market if the new price destroys expected value.

---

# 23. ADVERSE SELECTION PROTECTION

This is critical.

A passive order that becomes fillable may be fillable because BTC moved adversely.

Continuously revalidate resting orders.

If the signal becomes invalid before fill:

CANCEL THE ORDER.

Examples:

* probability deteriorates
* reference approaches strike
* volatility jumps
* spread changes
* market enters final no-entry window
* EV falls below threshold
* data becomes stale

A fill at a "better" contract price is NOT automatically a better trade.

---

# 24. POSITION SIZING

Support configurable sizing modes:

* fixed contracts
* fixed dollars
* percentage of bankroll

Recommended production behavior:

risk-based sizing.

Limits:

* max contracts/trade
* max dollars/trade
* max bankroll percentage/trade
* max concurrent exposure
* max daily exposure

Do NOT use martingale.

Do NOT automatically size up after losses.

Worst-case risk sizing must assume the position can settle worthless.

Do not assume a stop-loss fill is guaranteed.

---

# 25. TAKE PROFIT

Support configurable targets:

* .98
* .99
* .995
* .999
* settlement

Do not assume all markets support every sub-penny increment.

Read the market's current price/tick structure.

Round orders to valid price increments.

Do not hard-code weekday/weekend assumptions.

Backtest target behavior.

---

# 26. EXIT LOGIC

Use multiple exit layers.

## A. Take Profit

Exit when configured target can be realistically filled.

## B. Probability Invalidation

If holding the position no longer has favorable expected value or probability deteriorates materially, allow an early exit.

Possible conditions:

conservative_probability < EXIT_PROBABILITY

or:

hold_EV < MIN_HOLD_EV

or:

reference crosses/approaches strike under dangerous volatility

Make configurable.

## C. Hard Market-Price Stop

Retain configurable fallback:

stop_price =
entry_price * STOP_MULTIPLIER

Suggested test default:

0.75

This is a catastrophic backstop, NOT guaranteed risk.

## D. Settlement

Allow strong positions to settle when configured and appropriate.

---

# 27. MARKET REGIME FILTER

Create observable volatility/regime classification based on data.

Potential inputs:

* ATR percentile
* realized volatility percentile
* 1-minute candle magnitude
* 3-minute movement
* 5-minute movement
* shock count
* spread abnormality
* reference-feed instability

Possible regimes:

LOW
NORMAL
ELEVATED
EXTREME

Depending on regime:

* require higher probability
* require larger edge
* reduce size
* require larger strike separation
* skip trading

Do not rely primarily on hard-coded economic-news calendars for V1.

---

# 28. PAPER / BACKTEST / LIVE MODES

Implement:

BACKTEST
PAPER
LIVE

Default:

PAPER

LIVE must require explicit activation.

Recommended:

`TRADING_MODE=PAPER`

and additionally:

`ENABLE_LIVE_TRADING=false`

Live execution should require BOTH:

TRADING_MODE=LIVE
AND
ENABLE_LIVE_TRADING=true

Add a clear startup warning for live mode.

---

# 29. PAPER TRADING MUST BE REALISTIC

Do not assume:

"Price touched my limit → filled."

Paper trading should model:

* current order-book depth
* queue ahead where possible
* traded volume
* latency
* partial fills
* slippage
* fees
* spread
* cancelled orders
* adverse selection

When fill certainty is unknown, prefer conservative assumptions.

Track:

* theoretical signal price
* desired order price
* simulated fill price
* time-to-fill
* unfilled quantity
* estimated queue
* slippage

---

# 30. TRADE MEMORY — FIRST-CLASS REQUIREMENT

Every potential opportunity must be stored.

Do not store only executed trades.

Persist:

1. executed trades
2. unfilled trade attempts
3. rejected opportunities
4. market snapshots
5. probability estimates
6. model-quality data
7. reasons for decisions
8. order lifecycle
9. resulting settlement

Every opportunity gets a unique ID.

Example:

BTC15-20260908-174500-000184

All related records reference this ID.

---

# 31. OPPORTUNITY RECORD

For every meaningful evaluation in the entry window store at least:

* opportunity_id
* timestamp
* mode
* market ID
* ticker
* market expiration
* settlement window
* seconds remaining
* strike
* official reference price
* signed distance
* absolute distance
* distance %
* volatility estimates
* ATR
* Bollinger values
* Stochastic RSI
* short-term momentum values
* regime
* YES bid/ask
* NO bid/ask
* spread
* depth
* model raw probability
* model uncertainty
* conservative probability
* model-quality score
* market implied probability
* theoretical edge
* estimated fees
* expected slippage
* expected fill price
* expected EV
* decision
* approval status
* rejection reason(s)

This must exist even when no trade occurs.

---

# 32. STRUCTURED REJECTION REASONS

Do not merely store:

`approved = false`

Store structured rule failures.

Example:

decision = NO_TRADE

reasons:

MIN_EDGE:
required = .03
actual = .014

MODEL_QUALITY:
required = 85
actual = 81

This should allow analytics such as:

* rejected for insufficient edge
* rejected for volatility
* rejected for low quality
* rejected for spread
* rejected for liquidity
* rejected because entry window expired

---

# 33. MODEL / STRATEGY VERSIONING

Every opportunity and trade must record:

* strategy version
* probability-model version
* volatility-model version
* configuration version
* software version
* Git commit hash if repository metadata is available

Example:

strategy_version = btc15-v0.8.1
probability_model = montecarlo-v1.3.2
config_version = config-2026-09-08-A
git_commit = abc1234

This is non-optional.

Historical decisions must remain reproducible.

---

# 34. CONFIGURATION SNAPSHOTS

Store the complete effective configuration associated with each trade or opportunity.

Never depend only on the current configuration.

Examples:

* entry window
* min price
* max price
* min probability
* min quality
* min edge
* Monte Carlo paths
* volatility estimator
* stop settings
* TP settings
* size settings
* execution settings
* spread limit
* liquidity requirements

Historical replay must know exactly what settings produced the decision.

---

# 35. NEVER OVERWRITE HISTORICAL PREDICTIONS

Before settlement, persist the actual probability prediction.

Example:

P(YES) = .957
P(NO) = .043

After settlement attach:

ACTUAL = YES

Never replace the historical probability with post-settlement information.

This prevents hindsight contamination.

---

# 36. COMPLETE TRADE LIFECYCLE MEMORY

Executed trades should store:

* signal time
* decision time
* order submission
* requested order price
* order quantity
* order updates
* partial fills
* average fill
* fees
* slippage
* entry reference price
* entry strike distance
* probability path while held
* market-price path
* stop level
* take-profit level
* exit orders
* exit fill
* exit reason
* settlement
* gross P&L
* fees
* net P&L
* maximum favorable excursion
* maximum adverse excursion
* holding time

---

# 37. HIGH-FREQUENCY TRADE PACKET

For each completed trade/opportunity, generate an AI-readable standardized packet.

Suggested structure:

trade_packets/
YYYY/
MM/
<opportunity_id>/
summary.json
features.json
probability.json
decision.json
orders.json
fills.json
config.json
model_versions.json
market_path.parquet
probability_path.parquet
analysis.md

The Markdown summary should be understandable by both a human and an AI.

Example categories:

Trade ID
Mode
Decision
Entry
Exit
Result
P&L
Reason for entry
Supporting factors
Risk factors
Exit reason
Settlement
Model version
Configuration version

---

# 38. TRADE TIMELINE

Persist events so the UI can reconstruct:

17:40:11 — entered entry window

17:40:26 — P(YES) 91.8%
NO TRADE: insufficient edge

17:41:13 — P(YES) 96.4%
TRADE CANDIDATE

17:41:14 — limit bid submitted

17:41:38 — order repriced

17:41:39 — partial/complete fill

17:44:23 — TP filled

17:45:00 — settlement

Make this timeline available in the dashboard.

---

# 39. TRADE GRADE

After settlement optionally calculate a process-quality grade independent of financial outcome.

Example:

LOSS
Trade Grade: A

may be valid if:

* model was well calibrated
* EV was strongly positive
* execution was correct
* the low-probability unfavorable outcome happened

Likewise:

WIN
Trade Grade: D

may occur if the bot violated good process.

Do not teach the system:

win = good decision
loss = bad decision

Design the grade transparently and mark it as diagnostic, not objective truth.

---

# 40. ANALYTICS ENGINE

Build analytics from stored trade memory.

Metrics:

* trades
* wins
* losses
* win rate
* gross P&L
* fees
* slippage
* net P&L
* EV/trade
* profit factor
* average win
* average loss
* maximum drawdown
* Sharpe ratio where meaningful
* longest win streak
* longest loss streak
* stop-out %
* TP %
* settlement %
* fill rate
* partial-fill rate

Break down by:

* hour
* entry time remaining
* entry price
* side
* volatility regime
* normalized distance
* probability bucket
* model quality
* edge
* spread
* liquidity
* model version
* strategy version

---

# 41. PROBABILITY CALIBRATION — CRITICAL

Evaluate probability quality separately from profitability.

Compute:

* Brier score
* log loss
* calibration/reliability curve
* expected calibration error
* probability-bucket accuracy

Example buckets:

80–85%
85–90%
90–92%
92–95%
95–97%
97–99%
99%+

Break calibration down by:

* time remaining
* volatility regime
* YES/NO
* distance
* model version
* time period

If the model predicts ~95%, outcomes in that group should occur approximately 95% of the time over a sufficiently large sample.

This is more important than raw win rate.

---

# 42. COUNTERFACTUAL ANALYSIS

Because rejected opportunities are stored, support replay questions such as:

* What if MIN_EDGE were 2% instead of 3%?
* What if we entered one minute earlier?
* What if TP were .99?
* What if we held to settlement?
* What if high-volatility markets were excluded?
* What if model quality threshold were 90?

Do NOT overwrite original decisions.

Counterfactual results must be stored separately as experiments.

---

# 43. BACKTESTING

Build a proper event-driven/replay backtester.

Avoid:

* look-ahead bias
* future volatility use
* unrealistic fills
* assuming touched limits filled
* survivor bias where applicable
* using settlement information before settlement

Test parameter ranges such as:

ENTRY TIME:
8m
7m
6m
5m
4m
3m

MIN CONTRACT PRICE:
.85
.87
.89
.90
.92
.94

MIN PROBABILITY:
.80
.85
.90
.92
.95

STOP MULTIPLIER:
.65
.70
.75
.80
.85

TAKE PROFIT:
.98
.99
.995
.999
settlement

But do NOT brute-force thousands of combinations and select the best in-sample result.

Use:

* staged optimization
* walk-forward validation
* out-of-sample evaluation
* parameter stability analysis
* probability calibration first
* P&L second

---

# 44. UI / DASHBOARD

Build a clean, friendly dashboard.

Main trading view should display:

* official BTC reference price
* strike
* signed distance
* distance %
* seconds remaining
* favored side
* YES bid
* YES ask
* NO bid
* NO ask
* spread
* book depth
* ATR
* Bollinger status
* Stochastic RSI
* volatility regime
* model probability
* uncertainty
* conservative probability
* model quality
* market implied probability
* estimated fees
* expected slippage
* raw edge
* net edge
* recommendation
* open position
* entry
* take profit
* stop
* unrealized P&L
* realized daily P&L
* mode

Recommendation states:

WAIT
LEAN YES
LEAN NO
BUY YES
BUY NO
NO TRADE
ORDER PENDING
POSITION OPEN
TAKE PROFIT
INVALIDATED
STOP LOSS
SETTLED
HALTED

---

# 45. HUMAN-READABLE DECISION EXPLANATIONS

The UI should tell the user why the bot acted.

Example:

BUY YES

✓ Conservative settlement probability: 94.8%
✓ Executable market price: 90.0%
✓ Positive net expected value
✓ Sufficient distance from strike
✓ Normal volatility
✓ Acceptable spread
✓ Adequate liquidity
✓ No severe reversal warning

For NO TRADE:

NO TRADE

Reason:
Insufficient expected value

Model P(YES): 93.1%
Ask: 92¢
Net edge after costs: -0.4¢

Do not display only opaque model scores.

---

# 46. TRADES PAGE

Create a searchable/filterable trade table.

Columns should include at least:

* timestamp
* mode
* trade ID
* side
* entry
* exit
* contracts
* model probability
* conservative probability
* market price
* estimated edge
* result
* net P&L
* model version

Clicking a trade opens full replay.

---

# 47. TRADE REPLAY UI

For each trade show:

## Summary

* market
* side
* entry
* exit
* P&L
* probability
* edge
* volatility
* model quality
* reason

## Timeline

Every decision/order/fill event.

## Charts

Chart 1:
official BTC reference price vs strike

Overlay:

* entry
* exit
* entry-window start
* settlement window

Chart 2:
model probability through time

Chart 3:
Kalshi contract price through time

Optional later:
order-book depth visualization.

---

# 48. ANALYTICS PAGE

Show metrics such as:

Total Trades
Win Rate
Net P&L
EV/Trade
Profit Factor
Max Drawdown
Brier Score
Calibration Error

Charts:

* cumulative P&L
* drawdown
* probability calibration
* P&L by model probability
* P&L by entry minute
* P&L by entry price
* P&L by volatility regime
* YES vs NO
* edge vs realized returns
* model-version comparison

---

# 49. PAPER / LIVE VISUAL SEPARATION

Never visually mix paper and live trading without making the difference obvious.

Every record must include:

BACKTEST
PAPER
LIVE

Dashboard should have clear mode indication.

Default analytics should allow filtering.

Live should be visually obvious.

---

# 50. AI-READABLE ANALYTICS

Design data and API interfaces so an external AI assistant can easily analyze historical performance.

Provide endpoints/export mechanisms for questions like:

* Why did we lose money this week?
* What conditions characterize losing trades?
* Compare YES versus NO.
* Which volatility regime performs best?
* Which rejection criteria exclude profitable opportunities?
* How calibrated is the model?
* Did version 1.4 outperform 1.3?
* What parameter changes deserve testing?

Prefer:

* structured JSON
* stable schemas
* Markdown summaries
* Parquet/CSV exports where helpful

Do not require an AI to scrape dashboard HTML.

---

# 51. AI MUST NOT AUTOMATICALLY CHANGE LIVE STRATEGY

Architecture should enforce:

AI analysis
→ recommendation
→ backtest
→ paper test
→ user review
→ explicit approval
→ live deployment

Never allow:

AI detects losses
→ automatically modifies live parameters

Strategy changes require explicit human approval.

---

# 52. SAFETY CONTROLS

Implement:

* maximum trade loss
* maximum daily loss
* maximum daily trades
* max open exposure
* maximum order size
* maximum position size
* stale-reference detection
* stale-Kalshi-data detection
* clock synchronization checks
* duplicate-order protection
* order reconciliation
* partial-fill handling
* market-expiration protection
* connectivity handling
* retry policy
* rate-limit handling
* API error handling
* unexpected-state protection
* emergency kill switch

Never submit orders using stale data.

Never assume a submitted order succeeded.

Reconcile with Kalshi.

---

# 53. CLOCK AND TIME SAFETY

Because this is a short-duration strategy:

Use monotonic clocks for elapsed timing where appropriate.

Track exchange/API timestamps separately from local receive times.

Detect excessive clock drift.

Do not depend on UI timers for trading logic.

Use backend authoritative timing.

---

# 54. EXPECTED MODULES

Use modular boundaries conceptually similar to:

config
kalshi_client
market_discovery
reference_feed
market_data
raw_data_recorder
settlement_spec
indicators
features
volatility
probability_model
model_quality
edge_engine
signal_engine
state_machine
risk_manager
execution_engine
position_manager
paper_trader
backtester
live_trader
trade_memory
analytics
database
dashboard
tests

Adjust naming for the project's language/framework.

Signal generation MUST be separate from execution.

---

# 55. TESTING

Add comprehensive automated tests.

At minimum:

## Market parsing

* correct BTC15 identification
* strike parsing
* expiration
* YES/NO semantics

## Binary pricing

* YES/NO complement calculations
* bid/ask normalization
* tick rounding

## Timing

* entry-window boundaries
* final-two-minute rejection
* expiration protection

## Probability

* probability bounds
* deterministic seeded simulations
* final-minute observed-sample handling
* strike comparison

## Risk

* size limits
* daily loss limits
* concurrent exposure

## Execution

* duplicate-order prevention
* cancel/reprice
* partial fills
* stale-data rejection

## Paper trader

* conservative fills
* no false fills from mere price touching

## Persistence

* opportunity records
* model/config versioning
* historical predictions immutable

## State machine

Test valid and invalid transitions.

---

# 56. OBSERVABILITY

Use structured logging.

Include IDs:

market_id
opportunity_id
order_id
position_id
strategy_version
model_version

Log levels should be meaningful.

Add health/status information for:

* Kalshi connection
* reference connection
* database
* feed freshness
* current market
* current mode
* trading enabled/disabled

---

# 57. ENVIRONMENT VARIABLES

Create `.env.example`.

Never commit secrets.

Potential environment variables:

KALSHI_API_KEY_ID
KALSHI_PRIVATE_KEY_PATH or appropriate credential fields
DATABASE_URL
TRADING_MODE=PAPER
ENABLE_LIVE_TRADING=false
LOG_LEVEL
DATA_DIR

Use the authentication mechanism actually required by the current Kalshi API.

Do not invent deprecated credential patterns.

---

# 58. DOCUMENTATION

Provide:

README.md

docs/
ARCHITECTURE.md
STRATEGY.md
SETTLEMENT_MODEL.md
PROBABILITY_MODEL.md
DATA_MODEL.md
PAPER_TRADING.md
BACKTESTING.md
LIVE_TRADING.md
SAFETY.md
TRADE_MEMORY.md

README must include:

* setup
* dependencies
* environment variables
* database setup
* starting dashboard
* starting data collector
* running tests
* running backtest
* paper mode
* live-mode precautions

---

# 59. IMPLEMENTATION ORDER

Implement roughly in this order unless the repository requires otherwise:

PHASE 1
Repository assessment and architecture

PHASE 2
Configuration and persistence infrastructure

PHASE 3
Kalshi market discovery

PHASE 4
Official BTC/reference feed

PHASE 5
Raw data recorder

PHASE 6
Settlement-rule abstraction

PHASE 7
Feature calculations

PHASE 8
Volatility model

PHASE 9
Settlement probability model

PHASE 10
Model uncertainty / quality

PHASE 11
Edge/EV engine

PHASE 12
Signal/state machine

PHASE 13
Trade memory

PHASE 14
Historical/backtest engine

PHASE 15
Paper execution simulator

PHASE 16
Risk manager

PHASE 17
Dashboard/trade replay/analytics

PHASE 18
Live execution adapter

LIVE must remain disabled by default.

---

# 60. IMPORTANT ASSUMPTION POLICY

Whenever a rule is not confirmed, clearly mark it:

ASSUMPTION

Examples:

* technical-indicator parameters
* volatility estimator parameters
* model uncertainty method
* initial minimum edge
* initial model-quality threshold
* stop multiplier
* take-profit rules
* passive-order discount

Do not present assumptions as known optimal values.

Expose them through configuration.

---

# 61. DO NOT OVER-OPTIMIZE V1

Prefer a transparent system over an opaque model.

The first probability model should be understandable.

The objective is to first determine:

> Are our probability estimates calibrated?

Then:

> Do calibrated probability differences versus Kalshi produce positive realized EV after execution costs?

Only then add more sophisticated models.

---

# 62. PERFORMANCE REVIEW AFTER IMPLEMENTATION

THIS SECTION IS REQUIRED.

After implementation, do not simply say:

"Done."

Perform a critical review of what you built.

Create:

`docs/IMPLEMENTATION_REVIEW.md`

Include:

## A. What Was Implemented

List completed components.

## B. What Was Not Implemented

Clearly explain anything incomplete.

## C. Assumptions Made

List every significant assumption introduced during development.

## D. API / Data Limitations

Identify:

* unavailable historical data
* unavailable order-book history
* missing settlement fields
* WebSocket limitations
* authentication limitations
* rate limits
* tick-size issues
* fee uncertainties
* anything else discovered

## E. Strategy Concerns

If implementation reveals weaknesses in the strategy, describe them.

Examples:

* probability model too optimistic
* settlement simulation unrealistic
* insufficient historical high-frequency data
* passive-fill simulation unreliable
* stop-loss behavior problematic
* Kalshi market liquidity insufficient
* 2–8 minute window questionable
* .85 minimum threshold unsupported
* fees eliminate expected edge

DO NOT silently change these strategy rules.

Explain what you recommend testing.

## F. Engineering Concerns

Identify:

* race conditions
* latency concerns
* data-loss risks
* database scalability
* reconciliation risks
* WebSocket recovery
* UI/backend synchronization
* order-state ambiguity

## G. Recommended Improvements

Categorize as:

CRITICAL
HIGH PRIORITY
MEDIUM PRIORITY
EXPERIMENTAL

## H. Recommended Experiments

For every proposed strategy improvement include:

Hypothesis
Why it might help
Required data
Backtest method
Paper-test method
Success metric

Example:

Hypothesis:
Increase minimum net edge from 3% to 4%.

Reason:
Current 3–4% trades show poor realized EV.

Validation:
Walk-forward backtest + minimum 100 paper opportunities.

Do not implement strategy changes automatically.

---

# 63. FINAL RESPONSE TO USER AFTER CODING

When your implementation work is complete, provide a concise but substantive summary containing:

1. What you implemented
2. Repository structure
3. How to run it
4. How to start PAPER mode
5. How to collect market data
6. How to open the dashboard
7. How to run tests
8. How to run backtests
9. What is currently disabled
10. Any bugs/limitations
11. Any Kalshi/API behavior that differed from assumptions
12. Any strategy rules you think should be reconsidered
13. Recommended next improvements
14. Files/documents to review
15. Whether you consider the system ready for:

* data collection
* backtesting
* paper trading
* live trading

Use explicit readiness statuses such as:

READY
READY WITH LIMITATIONS
NOT READY

Do NOT label LIVE trading ready unless execution, reconciliation, risk controls, stale-data checks, testing, and paper validation are genuinely complete.

---

# 64. IMPORTANT FINAL INSTRUCTION

Treat this specification as a research and engineering framework, not proof of profitability.

If implementation reveals that any original assumption is poor, tell me.

If a better mathematical approach becomes obvious, recommend it.

If Kalshi's current API or settlement rules differ from this prompt, use the current documented behavior and explain the difference.

If sufficient historical information does not exist to validate an assumption, say so.

If the strategy appears unprofitable after realistic fees/slippage, do not manipulate parameters merely to produce a profitable backtest.

Preserve negative findings.

I want the system to help us discover whether the strategy genuinely has edge.

The desired long-term improvement cycle is:

COLLECT
→ ANALYZE
→ FORM HYPOTHESIS
→ BACKTEST
→ OUT-OF-SAMPLE VALIDATE
→ PAPER TRADE
→ REVIEW
→ USER APPROVAL
→ LIVE CHANGE

Never skip this process for a strategy modification.

Begin by inspecting the repository and documenting the current architecture, then proceed with implementation.
