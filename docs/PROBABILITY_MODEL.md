# Probability model

V1 estimates P(YES) independently of Kalshi prices. Only official standard BRTI
observations feed its base model. NO raw probability is exactly one minus YES.

## Simulation

Log price follows a zero-log-drift random walk with independent normal innovations:
`log S(t+dt) = log S(t) + sigma * sqrt(dt) * Z`.
Sigma is in log-return units per square-root second. The drift/distribution is an
ASSUMPTION, not established short-horizon BTC dynamics. For each path, simulate
future settlement sample prices, add already observed final-minute prices, divide
by 60, round to two decimals, and apply the parsed comparator. Known samples are
never resimulated. Missing past samples raise an error rather than introduce
look-ahead or imputation. The same seed/config/data reproduces the probability.

V1 draws directly between settlement sample times. The first draw crosses the
pre-settlement interval from the latest observed reference. For this independent
Gaussian log-increment model, this has the same finite-dimensional distribution
as stepping through every intervening second. It does not model jumps between
those observations or microstructure noise. Positive prices are preserved.

## Volatility and secondary features

Estimate realized volatility over 5, 15, 30, 60, 180 and 300 seconds and EWMA of
squared log returns divided by elapsed time. Use the maximum of the 30–300-second
estimates, EWMA, and a configurable floor as a conservative ASSUMPTION. Record
disagreement, recent jump, gap coverage, short-term momentum, acceleration,
consecutive direction, extrema, closed-candle direction and minute shock.

Contiguous closed minute candles support configurable Bollinger Bands, simple
window RSI→Stochastic RSI and simple-mean true-range ATR. These definitions are
explicit choices; they do not claim proprietary or original-creator settings.
Indicators unavailable during warmup are null. They never trigger a trade alone.
The 5 Hz stream is archived for future estimator comparison, not mixed into the
1 Hz estimator. LOW/NORMAL/ELEVATED/EXTREME uses configurable absolute sigma and
shock thresholds; it is not a learned regime classifier.

## Uncertainty and quality

Raw P is a simulation frequency, **not yet calibrated**. The larger distance from the point estimate to either 95% Wilson interval
endpoint gives a nonzero Monte Carlo allowance even with all-YES/all-NO simulations.
Add a default .02 uncalibrated-model allowance, rounding ambiguity, and in the
entry engine a volatility-disagreement allowance capped at .05. Each conservative
side estimate is clamped to [0,1]. Their sum can be less than one: they are separate
lower estimates, not complementary point probabilities.

Quality starts at 100, loses 25 for each observed stale/gap/warmup/shock condition,
and loses up to 10 times relative estimator disagreement. These diagnostic weights
and every threshold are ASSUMPTIONS, not statistical confidence or calibration.
Freshness and gaps also independently block signals regardless of numeric score.
No calibration fitting is automatically applied to the predictor.

## Calibration evaluation

Brier score, clipped log loss, reliability buckets and ECE are reported. The primary
summary takes the last eligible entry-window prediction per settled market to
avoid treating hundreds of correlated observations as independent samples.
All-prediction scores and selected groupings are also exposed with a dependence
warning. Confidence intervals require market/day block bootstrap, not binomial
counts over every tick. Rounding all raw probabilities to 0 or 1 does not establish
certainty. Monitor calibration before attempting to optimize net P&L.
