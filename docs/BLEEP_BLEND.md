# Bleep probability and blend experiments

All four active paper runs use `bleep_probability_blend_enabled=true` and
`bleep_probability_only_enabled=false`:

```text
P(YES) = (Project15 P(YES) + safety-clamped Bleep Mode B P(YES)) / 2
P(NO) = 1 − P(YES)
```

Both standard and late entries require Bleep probability ≥78%; the blended minimum is disabled. Project15 contributes half the blend but has no
individual veto. Same-side sample confirmation and quality checks remain;
modeled-sigma floors stay disabled. The purchase range is 80–95¢ and the hard
stop is 55¢. Full-position execution remains enabled.

Both probability modes remain available for historical runs and are mutually
exclusive. Missing Bleep inputs block evaluation. Current records identify
`settlement-bleep-equal-v1` with `weight_bleep=0.5`.

## Bleep component

The formulas are ported from `/home/devk/bleepblorp/src/lib/indicators.ts`,
`src/lib/decide.ts`, `src/lib/math.ts` and `src/server/indicators/compute.ts`.
They use Wilder ATR(14), Wilder RSI(14), Stochastic RSI(14) smoothed over three
values (latest and previous), and Bollinger Bands(20, two population standard
deviations). These are separate from Project15's existing diagnostic indicators.

```text
ATR used = max(Wilder ATR, reference × 0.00015, $0.01)
sigma_T = ATR used × sqrt(max(seconds remaining, 1) / 60)
base P(Up) = NormalCDF((reference − strike) / sigma_T)
indicator P(Up) = clamp(0.5 + 0.12 × Bleep indicator lean, 0.05, 0.95)
indicator weight = min(0.15, max(seconds remaining, 0) / 900)
Bleep P(Up) = clamp((1 − weight) × base P(Up)
                   + weight × indicator P(Up), 0.02, 0.98)
```

For above-strike contracts, Up means YES. For below-strike comparators the
probability is complemented. The calculation never averages confidence in NO
as though it were YES. With `bleep_safety_clamp_enabled=true`, distance/sigma_T
below 0.5 caps the favored-side confidence at 75%, before complementing to YES
and averaging with Project15. The audit records the ratio, pre-clamp probability,
and whether the cap changed it. With the current formulas, probabilities this
close to strike generally already fall below 75%, so the cap may be redundant.
Bleep's other entry/exit rules and Mode A are not imported.

With `bleep_exchange_seed_enabled=true`, startup asynchronously downloads up to
100 closed one-minute BTC candles from Coinbase (BTC-USD), then Kraken (XBTUSD),
then Binance (BTCUSDT) on failure. Valid seeds require at least 33 contiguous,
finite, valid OHLC candles with a recent last close. The current unfinished
exchange candle is excluded. Failure retries after 20 seconds while official
feeds continue; no placeholder indicators are invented.

These exchange candles feed only Bleep's ATR, Stoch RSI and Bollinger indicators.
Spot, strike distance, Project15's Monte Carlo and official settlement continue
using BRTI. After the seed's last closed minute, observed BRTI samples form live
minute OHLC candles (including the in-progress minute), as in Bleep; these Bleep
candles do not require 58 samples. A rolling 120-minute window gradually removes
all seed candles. Missing minutes are not filled with synthetic prices.
Project15's own indicators and reference-quality checks retain their stricter
completeness rules. Historical exchange prices can differ from BRTI.

The exact seed is recorded as a `bleep_seed` event for offline replay. It cannot
create reference samples, feed health, confirmations, orders or settlements.
Seed provenance and remaining seed count appear in evaluation features. Without
a seed, the existing official-history Bleep warmup remains the fallback; an
unavailable Bleep component never silently becomes a 100% Project15 calculation.
Both new flags default off to preserve historical config hashes and replay.

## Rules kept from Project15

The selected-side entry probability and reported fee-adjusted EV use the blend.
The current paper experiment disables net-edge/EV entry checks through
`entry_value_filters_enabled=false`. `both_models_80_enabled=true` requires
Bleep to assign at least 80% to the selected side for all four assets in standard entries
(`standard_component_min_probability=0.80`), with `120 < seconds_remaining <= 480`.
Late entries also require Bleep ≥80% (`late_component_min_probability=0.80`).
The blended minimum is disabled in both windows.
`project15_probability_veto_enabled=false` removes only Project15’s individual
floor; the blend and settlement-lead checks remain. This gate does not change exits. Entry
deductions remain disabled. The probability exit uses the blended held-side
probability minus Project15's existing full simulation/calibration/rounding
allowance. This is an experimental deduction, not a calibrated confidence bound
for the blend. The current BTC hard-stop-only test disables this exit with a zero threshold.

Side selection, sustained-lead magnitude/confirmation and settlement mean/std
remain based on Project15's reference and settlement simulation. The recorded
reversal stress remains an original-model diagnostic and does not veto this run.
An opposing component can lower the average enough to block entry; it does not
override Project15's side/lead rules.

Both entry paths retain the inclusive $0.75–$0.95 purchase range. Standard
cashout, profit-value and hold-value exits remain disabled. The 0.99 take-profit,
0.75 hard stop, 60-second global post-close cooldown, one filled trade per market,
entry windows, IOC behavior, fees, sizing and other safety gates are unchanged.
Remaining inventory resolves only from validated official settlement evidence.

## Evaluation and fresh start

This is a new hypothesis, not an established improvement: the two components
are correlated and estimate different targets (settlement average versus final
spot). Compare predictions against official settlement outcomes and measure
net paper P&L under Project15's execution model.

Prior paper ledgers, settings, services, pre-change source and a raw-recording
index are archived before starting the new run. Existing inventory must be flat
before reset. The new ledger starts at $1,000 paper bankroll, zero realized P&L
and fresh risk counters; past data are retained rather than deleted. The inactive
stop-comparison collector remains disabled.

Deployment, regression reports and the archive manifest are recorded under
`data/runtime/bleep-blend-v1/`. The committed reference fixture contains 300
formula cases generated from Bleep's TypeScript, covering flat, trending and
oscillating candles, time horizons and price/strike distances.

The subsequent seed-and-clamp update preserves the same run, trade history and
balances. Its backups and validation are under `data/runtime/bleep-seed-clamp-v1/`.

The separate directional Bollinger entry veto is enabled on all four assets
as of September 15, including strategy decisions used by live automation.
Bleep continues to use Bollinger Bands in its probability indicator adjustment.

## September 16: settlement-average and reference-volatility model (v2)

All four deployed assets enable `bleep_settlement_model_enabled` and start entries
at 420 seconds remaining (7 minutes); the late cutoff remains 15 seconds. The
flag defaults off and is omitted from historical configuration hashes when off,
so recorded v1 runs still use their original formulas. Current blended records
use `settlement-bleep-equal-v2`; the Bleep component identifies
`bleep-settlement-reference-v2`. There is no blended entry minimum; Bleep ≥78%
remains the component requirement.

The new normal approximation models the arithmetic average on the contract's
60-point settlement grid. Causal observed sample slots are fixed; missing or
duplicate required samples reject evaluation. Remaining samples have expected
price equal to the latest official reference price. Their uncertainty includes
shared price increments, rather than assuming independent future observations.

```
price volatility / sqrt(second) = max(
    floored ATR / sqrt(60),
    reference price * max(rv_30, rv_60, EWMA)
)
mean = (observed sum + remaining sample count * reference price) / 60
variance = volatility² * sum(dt * (remaining affected samples / 60)²)
```

The first future increment starts at the latest causal reference source time.
Thirty- and sixty-second realized log-return volatility and EWMA come from the
existing causal reference features; valid estimates are required. ATR's existing
floor is retained. Taking the maximum is a conservative heuristic, not a fitted
or calibrated volatility conversion. The arithmetic diffusion is a short-horizon
normal approximation, not a guarantee of settlement odds or profitable exits.

The normal-CDF boundary accounts for contract price precision and strict versus
inclusive comparison. Before completion, existing indicator weights, probability
caps and the safety clamp remain; the clamp measures expected average versus the
settlement boundary. Once all samples are observed, the contract's actual rounding
and comparison determine probability without indicator adjustment or caps; an
ambiguous rounding tie rejects evaluation.

Component logs include known/remaining sample counts, observed sum, predicted
average and standard deviation, effective boundary, variance time, ATR/reference
volatility estimates and the selected volatility source. These identify changes
in confidence for later evaluation. The implementation was tested for accounting
of observed samples, causality, gaps, comparisons, volatility response and entry
boundaries; profitability has not been established by these tests.

Current standard and late Bleep entry thresholds were lowered to 78% at the user’s request. The 75% safety-clamp ceiling remains unchanged. Evidence: `data/runtime/bleep78-all-20260916/`.
