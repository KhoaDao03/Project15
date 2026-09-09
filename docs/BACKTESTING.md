# Backtesting and held-out research

`btc15 backtest <journal.jsonl>` runs the same causal engine and paper executor in
BACKTEST mode. Raw receipt order is preserved. Reversals in both wall and monotonic
order reject the tape. Forward-monotonic wall-clock steps are retained and block
paper decisions until clock health is reverified. No sorting hides the anomaly.
Repeated IDs across inputs/mirrors are deduplicated using an on-disk index;
conflicting IDs fail before replay. JSONL and Parquet are streamed in bounded batches. Source timestamps govern feature availability;
settlement is visible only once its event arrives. Future reference observations
never enter the probability or volatility estimate.

A session journal includes metadata, snapshots, deltas, reference ticks, trades,
health and settlement results. One Parquet file can also be replayed but is often
insufficient for warmup or settlement. `research.replay_files(paths, config, store)`
accepts several chronological nonoverlapping captures, preserving risk across
markets. No order book or reference history is synthesized from contract candles.

Every counterfactual has a new run and experiment record containing parent run,
input hashes, dataset audit, real/synthetic provenance, config version, event count
and unsettled-position indicator. Input mutation during replay records a failed
experiment. `btc15 audit first.jsonl second.jsonl` exposes gaps, clock steps,
settlement coverage and available official-average comparisons.
Original decisions, predictions and results remain immutable. Reports never mark
an open position at a forecasted $1 payout merely because it looks likely to win.

## Walk-forward runner

`btc15 walk-forward config/walk-forward.example.json` reads predeclared candidate
configurations and ordered train/test file lists. It rejects overlapping train/test
timestamps, shared train/test markets and overlapping holdout folds. All folds are
checked before any selection; an optional `embargo_seconds` extends the separation.
Failed dataset audits and mixed synthetic/authentic folds are rejected. It limits
candidates to 12, requires a
minimum count of settled training markets, chooses by primary market-level Brier
score (stable candidate-order tiebreak), then evaluates the frozen configuration
on the next holdout. Insufficient data is a recorded negative finding. There is
no P&L-based “best strategy” promotion, no automatic live update and no thousands-
combination optimizer. Training can expand across folds; holdouts cannot overlap.

Do not supply a tape whose “received” timestamps were backfilled from publication
times; historical source times are not proof that the strategy had the data then.
Do not manually select successful contracts. Capture consecutive markets including
feed failures, illiquid intervals and rejected opportunities.

## Validation needed

Use contiguous market/day blocks, calibration first, realistic cost sensitivity
second. Examine Brier/log loss/ECE, observed bucket frequency and independent
market counts. Evaluate passivity against pessimistic queues and latency sweeps.
Inspect outcomes by regime, side, time remaining and price/edge buckets. Report
negative P&L, no-fill results and insufficient sample sizes. Deterministic UTC-day
cluster bootstrap intervals for Brier and per-trade P&L require at least 20 days;
the minimum is a disclosed research guard, not proof of independence. The method
retains intraday dependence but does not establish independence between days.
Survivorship audits and parameter stability assessment still require research judgment. Synthetic demo results validate only
software behavior and must not be included in claims about market performance.
