# Documentation — Settlement Edge-only milestone

The branch `fix/fractional-passive-fills` operates **BTC15 Settlement Edge only**. Finish software and authentic-data paper validation before considering additional strategies. Documentation below distinguishes current procedures from historical development evidence.

## Current operating guides

| Task | Guide |
| --- | --- |
| Install from an empty directory; first demo/feed/paper run | [Getting started](GETTING_STARTED.md) |
| Start, monitor, stop and resume | [Paper trading](PAPER_TRADING.md) |
| Understand thresholds, original/moderate presets and saved settings | [Strategy](STRATEGY.md) |
| Diagnose no orders/fills, startup errors, wrong database or stale data | [Troubleshooting](TROUBLESHOOTING.md) |
| Run without an attached terminal on Linux/WSL | [Managed paper service](AUTONOMOUS_PAPER.md) |
| Audit/replay real captures without rewriting their evidence | [Backtesting](BACKTESTING.md) |
| Run tests and interpret readiness honestly | [Validation](VALIDATION.md) |
| Protect credentials, back up data, recover a crash or halt | [Safety](SAFETY.md) |
| Retire an existing multi-strategy installation | [Single-strategy scope/recovery](SINGLE_STRATEGY.md) |

## Current implementation references

[Architecture](ARCHITECTURE.md), [data model](DATA_MODEL.md), [recording policy](TRADE_RECORDING.md), [trade memory/exports](TRADE_MEMORY.md), [probability model](PROBABILITY_MODEL.md), [settlement model](SETTLEMENT_MODEL.md), and [why live trading is blocked](LIVE_TRADING.md).

Implementation links: [CLI](../src/btc15/cli.py), [environment settings](../src/btc15/config.py), [strategy settings](../src/btc15/strategies/settlement_edge/config.py), [engine](../src/btc15/engine.py), [executor](../src/btc15/execution.py), [storage](../src/btc15/storage.py), [runner](../src/btc15/runner.py), [dashboard](../src/btc15/dashboard.py). When interpreting an old run, its frozen configuration and source revision take precedence over current defaults.

## Historical evidence — not setup instructions

The following files record earlier designs, host-specific actions, test counts or measurements. They do not prove the current installation is running, its database was reset, or it traded overnight. Commands for removed strategies are not valid on this branch.

- [Original request](REQUEST.md) and [initial implementation review](IMPLEMENTATION_REVIEW.md).
- [Historical collection/host-clock recovery](COLLECTION_RECOVERY.md), [research hardening](RESEARCH_HARDENING.md), and [processing measurements](PROCESSING_RELIABILITY.md).
- [Retired momentum model notes](MOMENTUM_MODELS.md), [retired readiness review](MOMENTUM_READINESS.md), and [sample report](MOMENTUM_REPORT_SAMPLE.json).
- [Fractional-fill correction](PASSIVE_FILL_FIX.md) and [dated single-strategy verification](SINGLE_STRATEGY_VALIDATION.md).

For current startup, recording or shutdown behavior, use the current guides above rather than copying a historical command or host-specific path. Earlier contents of updated documents remain in Git history. Operational trade history, raw tapes, checkpoints and credentials are not modified by this documentation update.

## Settlement recovery

[Settlement recovery](SETTLEMENT_RECOVERY.md) covers pinned entry identity, finalized evidence, explicit operator review, legacy checkpoints and retry/rollback behavior.
