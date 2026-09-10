# Exact order prices and whole-contract sizing

Settlement Edge has not acquired a new signal or a larger risk budget. This change
corrects binary floating-point artifacts in **execution-price selection and
whole-contract sizing**, not the probability model or its entry thresholds.

## Prices

Convert the numeric operands to Decimal using the existing `D` helper **before**
calculating passive discounts, spread-limited discounts, the YES/NO trade-price
complement, or adding/subtracting configured slippage. Apply the existing market
price-grid rounding afterward. Converting an already-rounded float expression to
Decimal is too late.

Examples on the supported $0.001 grid:

| Calculation | Correct supported price | Previous artifact |
| --- | --- | --- |
| Ask $0.938 minus $0.010 passive discount | $0.928 | $0.927 |
| Ask $0.939 minus $0.010 passive discount | $0.929 | $0.928 |
| Exit bid $0.939 minus $0.002 slippage | $0.937 | $0.936 |

Passive limits still stay below the ask and at or above the bid. Aggressive entry
limits still round down; aggressive simulated fills still round up. If an
intentionally off-grid slippage assumption makes those two prices incompatible,
the order remains unfilled and is cancelled as before. Take-profit targets and
sell-price flooring are not relaxed. Trade-price matching uses the exact limit,
not a floating-point epsilon; invalid/nonfinite source prices fail validation.

Quantity flooring, observed exit replenishment, latency, queue, one-attempt,
venue-pause and settlement-recovery rules remain in force. This changes intended
prices, not a promise that the real venue would fill an order.

## Sizing

Cost includes the unchanged conservative fee bound and configured slippage.
Fixed-contract target, bankroll allocation, reserved exposure, daily exposure,
and whole-contract division are calculated from Decimal operands. A target of
five returns five when **all** budgets permit it, rather than accidentally
flooring a representation just below five to four. A budget genuinely short of
five-contract cost, even by $0.000000001, still permits only four.

All fixed-contract, fixed-dollar and bankroll-percentage modes retain the same
per-trade, available-bankroll, allocation, open-exposure, daily-exposure, daily
attempt, realized-loss and kill-switch limits. Reservation and risk-history sums
use Decimal before writing the existing numeric fields so a subsequent risk
calculation or history reconstruction does not newly accumulate binary sum drift.
`size()` and rejection diagnostics use the same `size_details()` calculation.
There is no blanket epsilon and no rounding a genuinely insufficient budget up.

## Persistence and scope

API values, orders and checkpoints retain their existing numeric types and
configuration hashes. There is no schema migration, new setting, dependency,
manual bypass, history rewrite or automatic configuration switch. Previously
recorded artifacts are not silently repaired; stored legacy values are used as
recorded. This is not a conversion of every model/P&L/reporting float to Decimal.
The Monte Carlo, evaluation formulas, fee accumulator and strategy presets remain
unchanged. Source revision must accompany results: corrected prices/sizes can
change fill eligibility and P&L, even with the same configuration hash.

Tests retain the independent strategy/lifecycle regressions and add valid-grid,
exact/just-insufficient-budget, aggressive-price, malformed-price, rollback,
checkpoint and risk-history coverage. Reference sizing checks use an independent
rational-arithmetic oracle. Inputs are synthetic, not a live venue session or
profitability evidence. See the PR for the actual completed CI results.

References: [Python Decimal](https://docs.python.org/3/library/decimal.html) and
[Kalshi fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration).
