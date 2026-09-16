# Backtesting and held-out research — Settlement Edge

Replay uses the same single Engine and PaperExecutor in BACKTEST mode. It simulates against recorded receipt order, not against current market prices, and writes a **new** experiment/run. It never retroactively fixes the paper ledger or guarantees that a live session could achieve its fills.

## First verify the synthetic flow

Use the [Getting started demo](GETTING_STARTED.md#offline-smoke-test-first). Synthetic inputs test software behavior only. Do not mix their results with authentic recordings or select only successful contracts.

## Audit and replay authentic inputs

Gather the complete chronological source tapes and the frozen strategy configuration for the experiment. They are local artifacts, not bundled in Git. Use a separate replay database; keep the live PAPER environment flags unchanged.

```bash
uv run --locked btc15 audit data/raw/SESSION.jsonl.gz
uv run --locked btc15 --config data/runtime/settlement-original.json --database sqlite:///data/replay.db backtest data/raw/SESSION.jsonl.gz
```

Replace `SESSION` and the config path with actual matching files. Additional files may be passed in causal capture order:

```bash
uv run --locked btc15 audit data/raw/FIRST.jsonl.gz data/raw/SECOND.jsonl.gz
uv run --locked btc15 --config data/runtime/settlement-original.json --database sqlite:///data/replay.db backtest data/raw/FIRST.jsonl.gz data/raw/SECOND.jsonl.gz
uv run --locked btc15 --database sqlite:///data/replay.db analytics --mode BACKTEST --run REPLAY_RUN_ID
uv run --locked btc15 --database sqlite:///data/replay.db dashboard --no-collect --port 8001
```

Choose BACKTEST and the replay ID in the UI. `backtest` prints metrics that identify its run. `--parent-run PAPER_RUN_ID` records a relationship only: **it does not load a parent portfolio/checkpoint**. The current CLI has no automatic starting-checkpoint import for replay. A later resumed session alone cannot reconstruct earlier inventory, warmup, prior risk usage or downtime. State those limitations in any comparison.

JSONL, `.jsonl.gz` and Parquet inputs are supported. One Parquet chunk can lack required warmup, metadata or settlement. Prefer complete tapes. Identical IDs across mirrors are deduplicated; conflicting IDs fail. The audit reports data quality; a replay's completion is not by itself a declaration that the dataset passed all research acceptance checks. Walk-forward has stricter audit rejection gates.

## Causality and provenance

The reader preserves physical receive order. Backward wall time with forward monotonic order is retained and audited; backwards order in both clocks is rejected. Do not sort timestamps to hide clock issues. Source timestamps govern availability; official results are visible only after their event arrives. Do not synthesize reference history/order books from candles, backfill receipt times or replace missing official prices with a proxy feed.

Experiments retain input hashes, audit results, config identity, event count and remaining-position indicators. Inputs changing during replay cause a failed experiment. An open position at the end is unfinished, not a predicted payout. A missing final settlement can legitimately leave results incomplete.

## Interpreting comparisons

A counterfactual compares the same source inputs under a declared config/source revision; it does not replay wall-clock processing delays automatically. Keep immutable paper orders/fills/results as the record of actual operation. Report zero qualifying signals, unfilled cancellations, losses, data-quality failures and insufficient samples, not just completed winners.

Calibration uses retained eligible predictions with known outcomes. Full BACKTEST/observation can evaluate more markets than compact paper first-fill evidence. Repeated quote checks within a market are correlated. Do not pool independent replays or differing config versions as one continuous portfolio.

## Walk-forward runner

[walk-forward.example.json](../config/walk-forward.example.json) is a template, not a downloadable historical dataset. Copy/edit a manifest to reference real train/test files relative to the manifest and predeclare candidate configurations:

```bash
uv run --locked btc15 --database sqlite:///data/walk-forward.db walk-forward config/walk-forward.example.json
```

This command requires the template's referenced captures to exist first. The runner permits 1–12 predeclared configurations, checks overlap/shared markets/holdouts and dataset audits before selection, applies an optional embargo, selects by market-level Brier score with stable tie-breaking, and evaluates the frozen selection on the next holdout. It does not optimize by maximum P&L or promote a model to live operation. Insufficient training markets are a result, not a reason to fabricate data.

## Validation needed

Evaluate contiguous markets/day blocks, calibration, realistic queue/latency/fee/slippage sensitivity and negative outcomes on independent holdouts. UTC-day bootstrap intervals require at least 20 days in the implemented analysis; that threshold is a research guard, not proof that days are independent. Authentic replay, current feed operation, multi-day endurance and profitability remain distinct from [software tests](VALIDATION.md).
