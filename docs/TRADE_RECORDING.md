# Recording behavior

Current runtime: one Settlement Edge engine. Recording mode is determined by operation, not by the history filter or whether a trade happened.

| Operation | Inputs | Derived evaluation/status history |
| --- | --- | --- |
| `dashboard`, `paper`, `paper-service` executing PAPER | One `.jsonl.gz` tape per session | Latest skipped evaluations/status replace projections; first-fill evidence and execution history retained |
| `collect`, `dashboard --observe-only` | JSONL journal plus Zstd Parquet chunks | Full research evaluations/status |
| `backtest` | Reads supplied tapes; no live capture | Full evaluations in a new BACKTEST run |
| `dashboard --no-collect` | No new collector/tape | Displays the selected database |

## Retained data

Paper inputs live at `DATA_DIR/raw/<session>.jsonl.gz`. Envelopes retain event IDs, receipt/monotonic timestamps, connection IDs and original payloads including reference prices, book snapshots/deltas, trades, metadata/fees, lifecycle and health events. Each compressed batch is a complete gzip member, flushed and fsynced before analysis. No duplicate raw Parquet archive is created in paper mode. The run's `raw_source` record identifies each session's tape.

Orders and cancellations are retained even when never filled, including the evidence needed for duplicate prevention and daily attempt/exposure accounting. Pending orders checkpoint their entry snapshot. First fill atomically archives its original ID, timestamp, probabilities/features/book/config/model identity together with the fill. Later partial fills reuse it; an unfilled cancellation discards pending opportunity evidence. Failed writes roll back both evidence and accounting.

Fills, exits, fees, results, official settlement, relevant transitions, market metadata, exceptional health events, run provenance and checkpoints remain retained. No-trade state cycles do not append every permanent transition. Latest evaluation/status remain visible as replaceable projections. Thus zero saved opportunities, zero fills and zero evaluations are three different statements.

## Replay and recovery

Replace uppercase placeholders with actual complete files; they are not bundled datasets:

```bash
uv run --locked btc15 audit data/raw/SESSION.jsonl.gz
uv run --locked btc15 --config data/runtime/settlement-original.json --database sqlite:///data/replay.db backtest data/raw/SESSION.jsonl.gz
```

Use the configuration that actually produced the tape/run, not necessarily the original control shown here. Include needed warmup/earlier session files. A fresh replay does not automatically restore an earlier portfolio or reproduce host scheduling, downtime or manual actions. Original paper ledger/checkpoints remain the authority for what actually occurred. See [Backtesting](BACKTESTING.md).

A damaged/torn final gzip batch fails explicitly. Preserve the source; repair only an audited copy. Choosing a journal or its Parquet mirror avoids needless duplicate input work; identical IDs are deduplicated and conflicting copies rejected. Do not reorder by publication time or substitute prices to fill reference gaps.

## Database growth, console logs and backups

`market_display` holds replaceable projections and `paper_checkpoints` the latest recovery snapshot per run. Immutable `records` retain trading/research evidence. Existing compound indexes and compact run responses reduce selected query costs; some historical reporting still loads full results. The collector does not automatically purge old records or tapes.

CLI console logging uses `LOG_LEVEL` (default INFO), with message-only formatting. The systemd template sets WARNING and journald handles process output. A quiet console does not imply no captured events. This code does not automatically create a universal `data/runtime/bot.log`; redirect/manage console logs deliberately and keep credentials out of them.

Back up the database consistently, tapes, frozen settings and source revision. WAL files need a coordinated backup, not an arbitrary live file copy. No local data/credential files are tracked by this documentation change. Follow [Safety](SAFETY.md).

## Historical operations and measurements

The previous [recording report](https://github.com/KhoaDao03/Project15/blob/c23981dd0475d10af24caf1dde372d9477e78e08/docs/TRADE_RECORDING.md) describes a one-time authorized reset on September 9, 2026: 230,318 records removed and SQLite vacuumed from 663,937,024 to 86,016 bytes, with raw files retained. That is a reported action on that host, not a startup behavior or permission to reset another installation.

That same report's 30,000-event/three-model performance measurements predate the single-strategy refactor. They are not current one-engine benchmarks. Current repeatable software checks are in [Validation](VALIDATION.md); authentic-data and multi-day acceptance remain separate.
