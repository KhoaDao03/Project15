# Persistence design — current single-strategy runtime

SQLite WAL is the default (`sqlite:///data/btc15.db`); SQLAlchemy also supports the existing PostgreSQL setup. `DATA_DIR` and `DATABASE_URL` are independent settings. Initializing a Store creates missing tables/indexes and history protections; it is not a reset or a general migration framework.

## Tables

| Table | Role / mutability |
| --- | --- |
| `records` | Indexed record ID, run, mode, kind, market, opportunity ID, timestamp and JSON body; append-only history |
| `states` | Current run/market state and optimistic version; replaceable operational state |
| `claims` | Unique run/key claims for entry/settlement and retained historical registry claims |
| `leases` | Collector ownership; no automatic expiry/stealing |
| `paper_checkpoints` | Latest accounting snapshot for each run; updated with execution actions |
| `market_display` | Replaceable current display, reference, evaluation and status projections |

The two compound record indexes target kind/mode/run/time and kind/mode/time lookups. Startup also installs them on existing tables. Not every dashboard filter/pagination operation is executed in SQL; see [Architecture](ARCHITECTURE.md).

## Evidence versus projections

Runs retain effective config, identity, source/version information and a source snapshot. `raw_source` links each collection session to its tape. Market metadata, orders/cancellations, fills, fees, results, settlements and exceptional health information remain historical records.

In compact PAPER execution, rejected evaluations and routine status replace projections. A candidate's evidence is held in the pending order checkpoint and appended as an `opportunity` only on first fill. Unfilled cancellations retain order records but not opportunity evidence. Observation and BACKTEST retain full evaluation records. Read [Recording](TRADE_RECORDING.md) before interpreting counts.

Execution ledger writes, duplicate claims, state transitions and the checkpoint commit atomically. Checkpoints include positions, orders/queues, pending entry evidence, fee carry, duplicate trades, consumed exit depth and daily risk totals. Failed actions roll back SQL and in-memory accounting. No missing historical checkpoint is fabricated on resume.

## Raw inputs and recovery

Paper records one `.jsonl.gz` tape of ordered source envelopes. Observation records an fsynced JSONL journal plus compressed Parquet chunks. IDs, local receipt/monotonic timestamps, connection IDs and original payloads preserve inputs; exchange timestamps/sequences remain in the payload. Durable raw batches precede analysis, but the filesystem and SQL are not a distributed transaction.

Readers fail explicitly on damaged records/torn compressed batches. Preserve originals; any repair must be an audited copy. Replaying incomplete files does not invent absent reference history, a starting portfolio or downtime fills. [Backtesting](BACKTESTING.md) and [Safety](SAFETY.md) cover those limits.

## Retained legacy data

Archived identities, groups, model definitions, claims and old results may remain in the same database. They are data, not executable models. Default read APIs filter them out of Settlement Edge views; explicit archive scope exposes retained evidence. Startup guards reject incompatible group resumes and unresolved archived exposure. No automatic purge, checkpoint conversion or trading-history rewrite is performed. [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) identifies the recovery revision.
