# Independent BTC probability model

This is a transparent reconstruction of an options-informed model family, not HYHY's proprietary model. The package has no imports of Project15's trading application. No execution, entry, exit, sizing, or account-funded experiment is implemented here.

## Measures, units, and terminal diagnostic

Time is UTC Unix seconds; one year is 31,536,000 seconds (365 days). Volatility is an annualized decimal: a Deribit `mark_iv` of 20 becomes 0.20. Thresholds and official observations are decimal strings. Simulation uses float64 with explicit Decimal conversion at settlement comparison.

The diagnostic assumes the short-horizon forward F=S and discount D=1 unless configured otherwise:

```
tau = seconds / 31536000
v = sigma² tau
d2 = [ln(F/K) - v/2] / sqrt(v)
P(YES terminal) = Phi(d2)                 for an above/at-least event
value_yes = payout × D × P(YES)
```

The NO probability is complementary. Reversing the comparison reverses the CDF. Zero time/variance uses the explicit inequality and equality outcome, not 50%. The terminal diagnostic deliberately excludes averaging and rounding; it is never substituted for an unavailable headline estimate. Analytic tails use `erfc`.

Approximate short-horizon intuition:

```
remaining_uncertainty_usd ≈ S sigma sqrt(tau)
standardized_distance ≈ (S-K) / remaining_uncertainty_usd
probability ≈ Phi(standardized_distance)
```

Above-target BTC can become more likely to finish above target as time shrinks even with no bullish drift. A pullback can leave probability high when target distance remains large relative to uncertainty. These are model consequences, not guarantees or evidence of market leadership.

## Primary arithmetic-average model

A verified ContractSpec supplies the sample schedule, comparison, and rounding. Known official samples remain fixed. Missing elapsed samples block forecasting. Future observations share a single path per Monte Carlo trial:

```
S_next = S_current exp[(mu - sigma²/2) dt + sigma sqrt(dt) Z]
mu = 0 per year                 # arithmetic-price drift, consistent with F=S
A = [sum(known) + sum(future)] / total_required_samples
YES = contract_comparison(contract_round(A), exact_target)
```

Exact lognormal steps jump directly from the latest usable reference timestamp to the first future settlement timestamp, then between samples. A stale-but-allowed reference includes the entire unobserved interval. There is no independent drawing of future price levels, square-root-of-60 uncertainty reduction, or duplication of the current tick. At completion, averaging and comparison use Decimal only; that deterministic outcome is distinct from exchange result reconciliation.

The initial budget is 20,000 IID paths with seed 15. Numerical sampling uncertainty uses a 95% Wilson binomial interval. Its nonzero tail width prevents interpreting zero sampled failures as certainty. `requested_half_width` is a reporting target; increase `paths` up to 1,000,000 to request more precision. The engine reports unmet precision rather than spending an unbounded simulation budget. No antithetic or quasi-Monte-Carlo sampling is used. Exact replay requires the recorded configuration and compatible pinned numerical dependencies.

The sigma sensitivity range reruns the same seeded model at ±20% sigma by default and includes the central estimate. This is parameter sensitivity, not a statistical confidence interval or an estimate of all model uncertainty.

## Volatility providers

- **Options:** reject bad, stale, crossed, wide, thin, nonpositive, and inconsistent IV quotes; require at least three strikes. Select a qualifying expiry nearest the configurable 30-hour preference. Median mark IV combines identical log-forward-moneyness observations. Interpolate IV linearly in log(K/F); outside strike support, use a flagged flat endpoint. Query target moneyness using F_target=S, not the source option's forward. Retain selected instruments, rejected alternatives, expiry, age, and basis to the Deribit index. The live ingestion samples seven nearby call strikes from each of two candidate expiries; it is not an exhaustive search of the full chain.
- **Realized:** causal squared log returns divided by actual elapsed seconds, annualized; report 30/60/180/300-second windows and a 0.97 EWMA. Require warmup and no gaps exceeding policy. The configured 180-second window is the provider, not the maximum across windows. Microstructure noise and warmup sensitivity remain research limitations.
- **Fixed:** explicitly labeled diagnostic, 20% default. It is never a fallback for failed options access and is not HYHY ground truth.

Options use a constant variance-rate extrapolation, `sigma² tau`. A horizon below the selected expiry is flagged as extrapolated. A smile-informed constant sigma is neither a smile-consistent digital derivative nor local volatility. Coin option prices remain in their native units; the implementation uses reported IV and does not invert them using a USD formula. Price drift is zero; no momentum bonus is applied.

Options/fixed outputs are `pricing_proxy`, realized outputs `uncalibrated_forecast`, and complete exact outcomes `deterministic`. Calibrated outputs/version/cutoff stay null. Hybrid weighting, directional fitting, calibration fitting, arbitrage-constrained smile derivatives, stochastic volatility, and jump models are deferred optional research, not validated capabilities.

## Limits

Verified settlement arithmetic does not validate diffusion dynamics. Jumps, volatility changes, carry, risk premia, option maturity event concentration, reference/index basis, and sparse liquidity can dominate approximation error. Numerical intervals do not cover those risks. No estimate claims profitability or physical win-probability calibration. Kalshi quotes enter only downstream comparisons, and the model is allowed to disagree in either direction.

## User-selected model-lean tie assumption

`user-model-lean-v1` is a separate settlement-average research output, not a Kalshi rule. For each forecast, compute YES frequency from the unrounded arithmetic-average paths using the matched `>=` target comparison. Freeze that pre-rounding direction: above 50% rounds exact half-cent means up, below 50% rounds them down. Other values retain ordinary nearest-cent rounding. Exactly 50% yields `NO_DIRECTIONAL_LEAN`, with no research probability. The same simulated paths determine the final research frequency; the result does not feed back into its own lean.

This convention can reinforce the initial direction; it is not evidence of better forecast accuracy or official settlement correctness. It is allowed only when the sole unresolved contract mechanic is `ROUNDING_TIE_UNSPECIFIED` and required runtime inputs are valid. It never sets `ContractSpec.verified`, replaces the official numeric reconciliation, or populates primary probability fields. Older recorded contracts without this policy remain unchanged on replay. On the dashboard select **Settlement research · user tie rule** to view it; the guarded primary series remains separate. Research outputs retain the policy version, pre-rounding probability, lean, rounding direction and numerical interval.
