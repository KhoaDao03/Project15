# BTC15 Settlement Edge — trading strategy

**Documented:** September 10, 2026  
**Run:** `settlement-ioc-v1`  
**Strategy:** BTC15 Settlement Edge, version `v1`  
**Frozen configuration hash:** `1b7ffae5ec144248`  
**Mode:** PAPER — live market inputs with simulated orders and fills

This document describes the implemented strategy and the frozen configuration
used by the installed collector. It is an explanation of current behavior, not
an assertion that the strategy is profitable or that paper fills will match
exchange fills. No strategy or service settings were changed to produce it.

## 1. Strategy in plain language

The bot looks for a Bitcoin 15-minute contract whose favored outcome has a high
estimated probability of winning, but whose available purchase price is still
low enough to leave an estimated advantage after costs.

It normally buys five YES or NO contracts, taking available asks within a price
cap. It can sell before settlement when a stop, profit target, or model-based exit
condition triggers. Otherwise, a remaining position receives the official
settlement payout.

This is a settlement-probability strategy. It is not a rule that blindly buys
Bitcoin momentum, buys every market, or holds every purchase until expiration.

## 2. Contract and price inputs

The market series is `KXBTC15M`. The bot validates the contract's published terms,
strike, comparison operator, settlement window, and supported price ticks.
Unsupported or inconsistent metadata blocks trading or triggers quarantine.

For the usual “at least” contract:

- YES wins if the official final averaging-window value is at least the strike.
- NO wins if it is below the strike.
- A winning contract pays $1; a losing contract pays $0.

The strike comes from the validated market metadata. The final reference is an
average of 60 one-second CF Benchmarks BRTI observations, subject to the contract's
rounding rules. A single last Bitcoin price is not the settlement value.

The bot uses:

| Input | Purpose |
|---|---|
| Official BRTI samples at 1 Hz | Model history and settlement-window observations |
| BRTI display feed at 5 Hz | Responsive dashboard price display; not extra settlement samples |
| Kalshi order book | Executable asks/bids, spread, and available quantity |
| Market, fee, and exchange metadata | Contract validation, fee checks, trading availability |
| Official final settlement evidence | Resolve remaining paper inventory and audit results |

For a usual “at least” contract, BRTI above the strike favors YES; below favors NO.
At exact equality the entry rule selects neither side. The code also respects the
comparison operator for other supported contract directions.

## 3. Probability model and warmup

The model simulates the final settlement average using **4,000 Monte Carlo paths**
with seed **15**. Future prices follow a log-price random walk with **zero log
drift**. This is an explicit modeling assumption, not a learned directional signal.

Volatility uses recent realized volatility over several windows and an EWMA
estimate. The model uses the maximum of the relevant estimates and a volatility
floor. Known settlement-window samples are included directly; only future samples
are simulated. Missing required past settlement samples make the model unavailable.

The model reports raw YES/NO probabilities and more conservative probabilities.
The conservative values subtract simulation uncertainty, a **0.02 calibration
penalty**, and applicable rounding uncertainty. Entry evaluation additionally
subtracts a volatility-disagreement penalty, capped at 0.05.

These are **uncalibrated model estimates**. A displayed 90% estimate is not an
empirically established 90% success rate.

New entries require at least **300 seconds of reference history**, freshness and
continuity checks, and acceptable quality. A restart requires rebuilding fresh
in-memory inputs; five elapsed minutes alone do not guarantee readiness.

Bollinger Bands, ATR, stochastic RSI, momentum, and other features are recorded for
context. They are not independent buy/sell crossover rules in this strategy.

## 4. Entry rules

All relevant checks must pass. Passing model checks creates a candidate; execution
then rechecks eligibility and risk before accepting an order.

| Check | Active setting |
|---|---|
| Time remaining until close | `120 < seconds_remaining <= 480` |
| Purchase price | $0.85–$0.99 per contract, inclusive |
| Conservative probability of selected side | At least 0.90, after entry penalties |
| Model quality | At least 85/100, with no blocking quality reasons |
| Net edge | At least $0.03 per contract |
| Net expected value | At least $0.03 per contract |
| Bid–ask spread | No more than $0.04 |
| Available quantity at best ask | At least 5 contracts at evaluation |
| Volatility regime | Must not be EXTREME |
| Reference freshness | Receive/source age checks using a 3-second limit |
| Book freshness | Receive/source age checks using a 5-second limit |
| Clock skew tolerance | 2 seconds |
| Venue and contract | Trading available, valid metadata, no pause/quarantine |
| Risk | No blocking kill switch or exhausted budget |

Thus, a contract closing at 10:15 can be considered from 10:07 through just before
10:13. Exactly two minutes remaining is outside the entry window.

Quality starts at 100 and loses 25 points for each quality blocking reason, plus
a volatility-disagreement deduction. Stale data, insufficient history, reference
gaps exceeding two seconds, and shocks can block entry. Quality is not win
probability.

### Entry value calculation

For a fresh candidate:

```text
net_EV = conservative_probability
         - available_ask
         - estimated_fee_per_contract
         - 0.002 slippage_allowance
```

Both `min_edge` and `min_ev` currently test this same net value. They are not two
independent estimates. The separately displayed raw edge omits fees and slippage.

The fee estimate uses a conservative bound around the configured quadratic taker
fee model. The configured taker coefficient is 0.07, and account balance precision
is $0.0001. Actual simulated fees use the fee accumulator and its rounding rules.
Fees are not simply a flat 7% of purchase cost.

## 5. Purchase execution: capped IOC

The active configuration has `passive=false`. The bot simulates an
immediate-or-cancel purchase rather than waiting in a passive bid queue.

1. Select a supported price cap at or below `min(best_ask + 0.002, 0.99)`.
2. Recheck that the cap still leaves the required fee-adjusted value.
3. Reserve the risk budget and record the submitted order.
4. Wait the configured **0.25-second simulated latency**.
5. On an eligible processing event, consume available asks from cheapest upward,
   staying within the cap and applying freshness/safety checks.
6. Cancel any unfilled remainder.

The $0.002 allowance is **0.2 cents**, not two cents. It bounds the purchase cap;
it is not added artificially to every fill. When checking an IOC limit, the code
does not deduct that allowance a second time.

A changed quote, insufficient depth, an unsupported tick, or a failed recheck can
leave an order unfilled. Partial fills are possible, including fractional contract
quantities supported by the matching logic. A candidate or submission is not a
confirmed purchase.

### Retry policy

There is at most **one retry**, after a **five-second cooldown** from cancellation,
only when the first attempt filled absolutely nothing. The retry must satisfy all
entry, freshness, market, and risk checks again.

A partial fill does not qualify for a retry. A filled trade does not authorize
another entry in the same market. Attempts and retries consume daily attempt and
exposure budgets, even when no fill occurs.

Passive discount, queue multiplier, and maximum resting wait remain in the shared
configuration, but do not turn this IOC strategy into a passive waiting strategy.

## 6. Position size and risk budgets

| Setting | Value |
|---|---:|
| Initial paper bankroll | $1,000 |
| Sizing mode | Fixed contracts |
| Target purchase quantity | 5 contracts |
| Absolute contract cap | 10 |
| Per-order dollar cap | $10 |
| Bankroll allocation cap | 1% of bankroll adjusted by realized P&L |
| Concurrent reserved exposure cap | $20 |
| Cumulative daily reserved exposure cap | $100 |
| Realized daily loss threshold | $30 |
| Daily submitted-order limit | 20 attempts, including retries/unfilled submissions |

Five contracts is a target, not an unconditional allocation. Sizing applies the
smallest available budget after estimated fees and slippage; it can reduce size
or reject the order. Daily exposure is cumulative reserved expenditure, not just
currently open inventory. A cancellation does not refund that daily allowance.

Daily buckets use **UTC dates**, which can differ from the dashboard's local date.
The daily loss threshold blocks further entries once reached; it does not guarantee
that losses cannot exceed $30. Execution gaps and remaining positions still matter.

The kill switch blocks entries and preserves state. It is not a promise of an
immediate liquidation at the displayed price.

## 7. Exit rules

The bot monitors executable bids for the held side. When multiple conditions are
true, the code checks them in this order:

| Priority | Condition | Recorded reason |
|---|---|---|
| 1 | Bid is at or below 75% of average entry price | `HARD_STOP` |
| 2 | Bid reaches the $0.99 target, adjusted upward to a supported tick | `TAKE_PROFIT` |
| 3 | Conservative held-side probability falls below 0.70 | `INVALIDATION`, probability trigger |
| 4 | Estimated value of holding becomes less than zero | `INVALIDATION`, hold-value trigger |

For a $0.85 average entry, the hard-stop trigger is $0.6375. This is a trigger,
not a guaranteed execution price. Fees, slippage, latency, or missing liquidity
can produce a larger loss.

The implemented hold-value calculation is:

```text
hold_EV = conservative_held_side_probability
          - (current_bid - estimated_selling_fee - slippage_allowance)
```

The probability used here comes from the probability model. It is not necessarily
identical to the displayed entry probability after its additional disagreement
penalty.

**An `INVALIDATION` exit can be profitable.** The contract's sale price may have
risen enough that holding no longer offers sufficient additional expected value.
The detailed audit trigger distinguishes this from a probability falling below
the exit threshold.

Exit requests incur simulated latency and consume available bid liquidity on
eligible matching book events. Exit simulation applies its configured slippage
and supported-tick rules. Partial exits can require subsequent book updates.
A requested exit is not a guaranteed immediate fill.

Fresh, valid inputs and market eligibility remain necessary for management.
Price-based risk reduction can remain available during model warmup or model
unavailability, but a missing probability is never treated as zero to fabricate
an invalidation exit.

## 8. Settlement and profit accounting

Any remaining inventory is resolved using validated final settlement evidence.
The bot does not declare the final result from its own prediction. Conflicting
metadata or evidence can require quarantine and recovery.

```text
net_P&L = sale_proceeds + settlement_payouts - purchase_cost - total_fees
```

Illustrative five-contract outcomes:

| Bought | Closure | Fees | Net P&L |
|---|---|---:|---:|
| 5 YES at $0.85 | Sold at $0.982 | $0.0509 | +$0.6091 |
| 5 NO at $0.85 | Official result YES; NO pays $0 | $0.0447 | −$4.2947 |

A trade sold before settlement already has its realized result. The later market
settlement does not credit another profit. A market can also settle when the bot
never traded it.

The live trade table shows filled purchases as OPEN with final P&L Pending. Once
closed, the trade shows its final fees, proceeds, and realized result. Submitted
but completely unfilled orders are order history, not completed trades.

## 9. Audit trail and continuous operation

The logs preserve orders, fills, cancellations, fees, exit decisions, settlement
and recovery evidence, monitoring interruptions, and periodic rejection summaries.
The new exact exit triggers are:

- `PROBABILITY_BELOW_EXIT_THRESHOLD`
- `HOLD_VALUE_BELOW_THRESHOLD`

Hypothetical hold-to-settlement comparisons are separate research records. They
do not modify actual paper P&L. See [decision logging](DECISION_LOGGING.md).

Local systemd services supervise the collector and dashboard. A restart resumes
the same durable run/configuration and rebuilds fresh inputs; it does not imply
orders were filled during downtime. Trading waits when the exchange reports
unavailable or required data is missing.

Event-routing optimizations preserve due evaluations, position/order checks, and
settlement handling. They do not add an entry signal or change strategy thresholds.
See [market processing](MARKET_PROCESSING.md).

## 10. Configuration and source references

The installed collector selects
[`data/runtime/settlement-ioc-v1.json`](../data/runtime/settlement-ioc-v1.json)
explicitly. Settings saved through the dashboard are for future sessions and do
not replace this run's frozen settings. The historical default strategy uses
passive orders and no retry; that is not the IOC configuration documented here.

Implementation references:

- [Entry checks, sizing, and fees](../src/btc15/strategies/settlement_edge/rules.py)
- [Features, probability model, and quality](../src/btc15/strategies/settlement_edge/model.py)
- [Order matching, exits, retry, and settlement](../src/btc15/execution.py)
- [Event orchestration and freshness gates](../src/btc15/engine.py)
- [Contract validation and settlement semantics](../src/btc15/domain.py)
- [Configuration definition](../src/btc15/strategies/settlement_edge/config.py)

## Appendix: exact frozen configuration

The following snapshot is included so this document remains interpretable even
when the local runtime file is unavailable or future settings change.

```json
{
  "enabled": true,
  "entry_window_start": 480,
  "no_new_entry": 120,
  "min_entry_price": 0.85,
  "max_entry_price": 0.99,
  "min_probability": 0.9,
  "min_quality": 85,
  "min_edge": 0.03,
  "min_ev": 0.03,
  "max_spread": 0.04,
  "min_liquidity": 5,
  "paths": 4000,
  "seed": 15,
  "warmup_seconds": 300,
  "ewma_decay": 0.97,
  "volatility_floor": 1e-05,
  "shock_threshold": 0.003,
  "extreme_sigma": 0.0005,
  "calibration_penalty": 0.02,
  "slippage": 0.002,
  "reference_max_age": 3,
  "book_max_age": 5,
  "max_clock_skew": 2,
  "evaluation_interval": 1,
  "passive": false,
  "passive_discount": 0.01,
  "resting_limit_recheck": true,
  "max_entry_retries": 1,
  "entry_retry_cooldown": 5,
  "max_wait": 20,
  "latency_seconds": 0.25,
  "queue_multiplier": 1.5,
  "bankroll": 1000,
  "sizing_mode": "fixed_contracts",
  "fixed_contracts": 5,
  "fixed_dollars": 5,
  "bankroll_fraction": 0.01,
  "max_contracts": 10,
  "max_trade_dollars": 10,
  "max_open_exposure": 20,
  "max_daily_exposure": 100,
  "max_daily_loss": 30,
  "max_daily_trades": 20,
  "stop_multiplier": 0.75,
  "take_profit": 0.99,
  "exit_probability": 0.7,
  "min_hold_ev": 0,
  "bollinger_period": 20,
  "bollinger_std": 2,
  "rsi_period": 14,
  "stochastic_period": 14,
  "atr_period": 14,
  "taker_fee_rate": 0.07,
  "maker_fee_rate": 0.0175,
  "fee_balance_precision": "0.0001"
}
```
