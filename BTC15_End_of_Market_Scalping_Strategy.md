# BTC 15-Minute End-of-Market Scalping Strategy

> **Strategy objective:** Wait until late in a Kalshi 15-minute Bitcoin contract, identify the side that already appears likely to win, buy that side at approximately $0.85 or higher, and seek the final convergence toward $1.00.

This is not a strategy for predicting Bitcoin's direction at the beginning of a 15-minute market. Its proposed edge is buying a high-probability outcome when the estimated probability of winning is sufficiently higher than the price paid, while controlling reversal and execution risks.

**Source:** The user-provided `Pasted markdown(1).md`. This document describes the supplied strategy, not a verified implementation or a demonstrated profitable trading system. The source does not provide historical performance results, the original confidence formula, or complete model settings.

---

## 1. Market

The target is **Kalshi BTC 15-minute markets**. For each contract, identify the exact contract wording, strike/reference price, outcome mapping, and time remaining until settlement.

The supplied strategy uses the **CF Benchmarks real-time index** as its primary BTC reference. Settlement logic should use the exact reference source specified by the individual Kalshi contract whenever possible. Do not substitute Coinbase, Binance, or another exchange price unless the contract explicitly settles from that source. Other exchange feeds may be secondary informational inputs.

Labels such as `YES/UP` and `NO/DOWN` must follow the actual contract wording; they are not universally interchangeable.

**Unspecified in the source:** The exact index identifier, full settlement calculation, and treatment of equality with the strike. These are contract-specific details to resolve rather than infer from a generic BTC price feed.

## 2. Core Entry Window

The normal entry window is:

```text
2 minutes < time_remaining <= 8 minutes
```

Equivalently:

```text
120 < seconds_remaining <= 480
```

| Time remaining | Normal entry behavior |
| --- | --- |
| More than 8 minutes | Observe; do not normally open a position. |
| More than 2 minutes, up to and including 8 minutes | Evaluate possible entries. |
| 2 minutes or less | Do not normally open new positions; continue managing existing positions. |

This excludes approximately the first seven minutes of a 15-minute contract and the final two minutes. Both boundaries must be configurable. With the stated settings, exactly eight minutes remaining is eligible, while exactly two minutes remaining is not.

## 3. Determine the Favored Side

Compare the current contract-aligned BTC reference price with the strike:

```text
BTC_reference_price > strike  -> favor YES/UP
BTC_reference_price < strike  -> favor NO/DOWN
```

This mapping assumes the contract wording matches those outcomes. The source does not define a favored side when the reference price equals the strike.

Calculate both absolute and proportional separation:

```text
distance_from_strike = abs(BTC_reference_price - strike)
distance_pct = abs(BTC_reference_price - strike) / strike
```

`distance_pct` is expressed as a decimal ratio in this formula; multiply it by 100 to display a percentage.

The important question is not simply which side of the strike BTC occupies. It is whether that separation is substantial relative to the remaining time and current volatility.

## 4. Minimum Kalshi Contract Price

The favored-side contract generally must satisfy:

```text
favored_contract_price >= 0.85
```

The threshold is configurable. The intention is to buy an outcome already priced as relatively likely to win, rather than seek a cheap, approximately 50/50 contract.

**Source example:** BTC is comfortably above the strike and YES trades at $0.89. The proposed opportunity exists when the system estimates that YES has a materially greater than 89% probability of settling at $1.00.

A high contract price alone is not an entry signal. The time window, distance, confidence, indicator, volatility, spread, and liquidity conditions still apply.

**Unspecified in the source:** Whether the $0.85 eligibility gate uses the bid, ask, midpoint, last trade, or proposed entry price. That distinction matters when a passive bid is below the current quoted market price.

## 5. Technical Indicators

Use a **1-minute BTC chart** with Bollinger Bands, Stochastic RSI, and ATR. These indicators confirm the favored side or identify risk; they are not the primary directional signal.

All settings must be configurable. The original creator's exact settings were not supplied, so no particular lookback period, multiplier, smoothing setting, or indicator threshold is presented here as an original strategy parameter.

### Bollinger Bands

Track the upper, middle, and lower bands, along with BTC's position relative to them.

Be cautious about buying YES/UP when BTC is extremely extended near or against the upper band and appears likely to revert toward the strike. Apply the corresponding caution to NO/DOWN when BTC is extended near or against the lower band.

The source does not quantify what constitutes extreme extension or an unacceptable reversal signal.

### Stochastic RSI

Use Stochastic RSI to identify short-term overbought or oversold conditions and possible reversals. Its reading should increase or decrease confidence in the currently favored side, not independently trigger a trade.

### ATR and Volatility-Adjusted Distance

Use ATR to estimate short-term BTC volatility. The source describes the intended normalization concept as:

```text
normalized_distance = distance_from_strike / expected_remaining_price_movement
```

The same $200 distance from the strike can represent very different risk under low versus extreme volatility. Distance therefore needs to be considered alongside the expected amount BTC could move before expiration.

**Unspecified in the source:** The exact ATR-based formula for expected remaining movement, its time scaling, and the minimum acceptable normalized distance. This document does not substitute an undisclosed formula or a numerical threshold.

## 6. Confidence Score

Create a transparent, configurable **0–100 confidence score** representing the estimated safety of the favored side remaining on the winning side of the strike until expiration.

The supplied minimum is approximately:

```text
confidence_score >= 80
```

Setups with approximately **87–95% or higher confidence** are preferred when available, but that preference is not a separate mandatory threshold in the supplied rules.

The source also uses a fractional confidence value in its pseudocode. To keep the notation explicit:

```text
confidence_fraction = confidence_score / 100
80 points on the 0–100 scale = 0.80 on the fractional scale
```

### Potential Inputs

| Feature group | Inputs identified in the source |
| --- | --- |
| Strike separation | Absolute distance and distance normalized by ATR. |
| Time and volatility | Time remaining, realized volatility, and ATR. |
| Technical confirmation | Bollinger Band position, Stochastic RSI, recent momentum, and recent candle direction. |
| Contract pricing | Bid, ask, midpoint, spread, implied probability, and recent contract-price changes. |
| Optional market depth | Order-book imbalance, when reliable data is available. |

The confidence model must expose individual components, make weights configurable, permit backtesting-based optimization, and log each feature's contribution.

**Source limitation:** The original confidence formula was not disclosed. Neither numerical weights nor a mapping from a constructed confidence score to a calibrated win probability was supplied. A score of 95 should therefore not be presented as an established 95% win probability without a defined probability-estimation method.

## 7. Probability vs. Market Price

The strategy description warns against buying merely because confidence exceeds 80%. Compare the estimated probability of winning with the contract price and account for execution costs.

**Source example:**

```text
YES ask                  = $0.89
Estimated P(YES wins)     = 0.95
Gross probability edge   = 0.95 - 0.89 = 0.06
```

This is a six-percentage-point probability difference before costs, not a demonstrated net return.

Evaluate expected value after accounting for Kalshi trading fees, spread, expected slippage, and expected fill price. When the EV filter is enabled, require a positive expected value that meets a configurable minimum margin.

**Classification:** The explicit EV gate is an enhancement to the original strategy and must be possible to enable or disable. The source does not supply the fee schedule, full EV calculation, or minimum numerical margin.

## 8. Entry Logic

An entry candidate requires the correct market, an eligible time window, a clearly favored side, sufficient favored-side contract price and confidence, adequate volatility-adjusted separation, acceptable reversal risk and volatility, acceptable spread, and sufficient liquidity.

The following pseudocode organizes the supplied rules. It is illustrative, not a complete executable implementation. It uses the fractional confidence scale and makes the optional EV gate explicit.

```python
entry_allowed = (
    market_is_valid
    and entry_cutoff_seconds < seconds_remaining <= entry_start_seconds
    and favored_side_is_clear
    and favored_contract_price >= minimum_contract_price
    and confidence_fraction >= minimum_confidence_fraction
    and normalized_distance >= minimum_normalized_distance
    and reversal_risk_is_acceptable
    and volatility <= maximum_volatility
    and spread <= maximum_spread
    and liquidity >= minimum_liquidity
    and (
        not ev_filter_enabled
        or expected_value_after_costs >= minimum_expected_value
    )
)

if entry_allowed:
    generate_entry_signal()
```

The numerical settings explicitly provided in the source are:

```text
entry_start_seconds         = 480
entry_cutoff_seconds        = 120
minimum_contract_price      = 0.85
minimum_confidence_fraction  = 0.80
```

The remaining thresholds must be defined through configuration and testing; the source does not specify their values.

## 9. Limit Order Entry

Prefer passive limit orders when possible. Determine the bid from the current order book rather than using the same entry price for every trade.

The source gives this illustrative construction:

```text
entry_bid = current_best_ask - configurable_discount
```

For example, with a contract around $0.92, the strategy might initially bid approximately $0.89 and wait for a temporary retracement. Neither $0.89 nor a $0.03 discount is a universal rule.

If the order remains unfilled until a configurable deadline, and the setup remains valid, the bot may optionally cancel and replace it with a more aggressive order near the preferred entry time. Do not chase the price when the new execution price destroys expected value.

**Unspecified in the source:** The discount, timeout, repricing schedule, preferred entry time within the window, and treatment of unfilled entry orders at the two-minute cutoff.

## 10. Position Sizing

Support three configurable sizing approaches: a fixed number of contracts, fixed-dollar risk, and a percentage of bankroll at risk. The supplied description recommends risk-based sizing for production use.

Apply configurable caps to dollars per trade, contracts per trade, percentage of bankroll per trade, and total exposure across concurrent markets.

**Do not use martingale or automatically increase position size after losses.**

The source does not supply numerical position limits or a complete definition of the risk amount used by the sizing calculation.

## 11. Take Profit

The target is near the contract's settlement value. Example configurable take-profit prices are:

```text
$0.99
$0.995
$0.999
```

The source reports that the creator commonly used approximately $0.99 in normal/weekday conditions and approximately $0.999 under particularly favorable conditions. This is a description of the proposed approach, not evidence that a weekday/weekend distinction is profitable.

Keep the target configurable and compare alternatives through historical testing. Place a take-profit limit order after entry when supported.

**Execution limitation:** The supplied target examples do not establish which price increments or order features are supported by a particular contract. Those details are not verified in this document.

## 12. Stop Loss

The described stop is approximately **75% of the original entry price**:

```text
stop_price = entry_price * stop_loss_multiplier
stop_loss_multiplier = 0.75
```

| Entry price | Calculated stop price |
| --- | --- |
| $0.90 | $0.6750 |
| $0.92 | $0.6900 |
| $0.89 | $0.6675 |

A 0.75 multiplier corresponds to a 25% decline from the entry price before execution effects; it does not mean a 75% loss. The multiplier is configurable.

Do not assume a Kalshi-native stop order exists. When necessary, emulate the stop by monitoring market price continuously and submitting an executable sell order when the stop condition is reached.

Account for spread, latency, slippage, partial fills, and rapidly changing order books. The calculated stop is a trigger level, not a guarantee of the final execution price.

The source calls this stop “optimized,” but supplies no optimization results. It also does not specify the monitored price used to trigger the stop or how calculated levels should be rounded to supported price increments.

## 13. Early Exit Logic

Do not exit solely because the position fluctuates a few cents against the entry. The normal outcomes are **take profit, stop loss, or contract settlement**.

An optional emergency/probability-invalidation exit may close the position earlier if the original favored side's estimated probability deteriorates sharply. Examples include BTC approaching or crossing the strike, or volatility increasing dramatically.

This feature must be separately configurable and backtested. The source does not prescribe a probability-drop threshold, a volatility trigger, or a precise invalidation formula.

## 14. Final Two Minutes

Do not normally start new positions with **two minutes or less remaining**.

For positions already open, continue monitoring. A position may remain open while waiting for take profit or settlement if confidence remains extremely high and BTC stays comfortably separated from the strike. If the probability deteriorates significantly, follow the defined stop-loss or optional invalidation rules.

The final-two-minute rule is an **entry restriction**, not an instruction to automatically liquidate every open position.

---

## Configuration Reference

The distinction below separates values actually supplied in the source from implementation choices still to be defined.

| Parameter | Supplied value or requirement |
| --- | --- |
| Market | Kalshi BTC 15-minute contracts. |
| Primary reference | CF Benchmarks as described; match the exact contract settlement source. |
| Indicator chart interval | 1 minute. |
| Entry-window start | 480 seconds remaining, inclusive; configurable. |
| New-entry cutoff | 120 seconds remaining, exclusive; configurable. |
| Minimum favored-side contract price | Approximately $0.85; configurable; quote basis unspecified. |
| Minimum confidence | Approximately 80/100, or 0.80 fraction; configurable. |
| Higher-confidence preference | Approximately 87–95%+ when available; not an additional fixed gate. |
| Indicator settings | Configurable; exact settings not supplied. |
| Confidence model and weights | Transparent and configurable; exact formula not supplied. |
| Remaining-movement model | ATR-based concept; exact formula not supplied. |
| Minimum normalized distance | Configurable; value not supplied. |
| Maximum volatility | Configurable; value and measurement definition not supplied. |
| Maximum spread | Configurable; value not supplied. |
| Minimum liquidity | Configurable; value and measurement definition not supplied. |
| EV filter | Optional enhancement; default enabled/disabled state not supplied. |
| Minimum expected value | Positive configurable margin when EV filtering is enabled; value not supplied. |
| Passive-order discount and deadline | Configurable; fixed values not supplied. |
| More aggressive cancel/replace | Optional while the setup remains valid and EV is preserved. |
| Sizing mode and exposure caps | Configurable; no numerical limits supplied. |
| Take profit | Example targets: $0.99, $0.995, $0.999; configurable. |
| Stop-loss multiplier | Approximately 0.75 of entry price; configurable. |
| Emergency/invalidation exit | Optional; exact triggers and default state not supplied. |

## System Structure Requested by the Source

The implementation is intended to separate **market data, signal generation, execution, risk management, logging, and backtesting** into distinct modules.

Market data supplies the contract details, reference prices, 1-minute inputs, and order-book information. Signal generation evaluates direction, distance, indicators, confidence, and the applicable entry gates. Execution handles entry, optional repricing, take profit, and exit orders. Risk management applies sizing, exposure limits, stop rules, and optional invalidation. Logging makes confidence components and feature contributions inspectable. Backtesting evaluates model weights and configurable alternatives, including take-profit levels and invalidation exits.

The supplied material is a strategy specification rather than evidence of live readiness. Exact contract mechanics, unresolved model definitions, execution details, and performance remain to be established; this document does not silently fill those gaps.
