# Persistence design

SQLAlchemy supports PostgreSQL and SQLite WAL. `records` stores indexed ID, run,
mode, kind, market, opportunity ID, event timestamp and immutable JSON payload.
Indexes support research lookup without imposing speculative relational fields.
This is a versioned event log with operational projections, not a fully normalized
financial ledger. Current schema version is the initial 0.1 schema.

Record kinds: run, raw_source, market, pending_market, invalid_market, opportunity,
transition, order, fill, exit_intent, execution_rejection, settlement, trade_result,
health, status, experiment and walk_forward. Full effective configs and model,
source and Git versions accompany predictions; runs retain source/lock snapshots.
Historical prediction updates/deletes are rejected by database triggers.

`states` stores per-run/per-market state and optimistic version. Each transition
updates the version and appends history in one transaction. `claims` uniquely
reserves entry/settlement intents. `leases` prevents concurrent collector writers.
Paper objects have a single owner; no multi-worker execution is supported.

Raw recording first appends and fsyncs JSONL, preserving the original wire payload,
local receive/processing timestamp, monotonic timestamp, connection ID and event
UUID. Exchange/source timestamp and sequence remain in the wire payload. Buffered
rows are written to Zstd Parquet using a fixed schema and atomic rename. The
journal remains the authoritative full-session recovery file; it is not deleted.
Malformed trailing lines produce an explicit replay error, not silent truncation.

A process crash may leave an incomplete temporary Parquet chunk and a complete
journal. Replay the journal into a new BACKTEST run. It may also leave the writer
lease and pending execution claims; these require review before clearing. Raw
recording and SQL evaluation are not one distributed transaction. The raw journal
precedes each SQL decision; replay repairs missing research outputs in a new run.

Limitations: per-event fsync is deliberately durable but needs throughput testing;
current dashboard aggregation loads selected records in memory; raw journals and
Parquet duplicate storage; lifecycle retention/compaction needs an operator policy;
initial schema has no migration tool. There are no unreviewed data-deletion jobs.
