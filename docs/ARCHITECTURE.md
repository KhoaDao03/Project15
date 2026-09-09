# Architecture — one Settlement Edge engine

This is the current architecture of the single-strategy branch. Earlier multi-model and initial-repository assessments are historical; use [the documentation index](README.md) to find them. No new framework or distributed service is required.

## Components and flow

```text
Kalshi REST metadata / effective fees / official results
                         |
Authenticated WebSocket receipt + heartbeat/metadata tasks
                         |
Bounded queue -> one ordered worker -> durable input batch
                         |
Strict BTC15 validation -> standard reference/book state
                         |
One Settlement Edge Engine: features -> probabilities -> entry checks
                         |
PaperExecutor: time/freshness/risk rechecks -> order -> eligible fills
                         |
Atomic ledger + checkpoint -> exits / official settlement -> results
                         |
Local FastAPI dashboard, analytics, replay and exports
```

Socket receipt and REST refresh are independent of the ordered processing worker. Raw inputs are durable before analysis. Paper uses a compressed JSONL tape; observation uses the full JSONL/Parquet research recorder. Offline replay feeds the same Engine/PaperExecutor in recorded receipt order without connecting a live feed.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `cli.py`, `config.py` | Entry points, environment/next-session configuration and live guard |
| `api.py` | Read-only REST, request signing and feed subscriptions |
| `domain.py` | Contract wording/times/price-grid validation, book normalization and serialization |
| `runner.py` | Receipt, metadata, queue, durable recording, one engine, status/display publication |
| `strategies/settlement_edge/` | Configuration, reference features/probability/quality, entry/fee/risk/pricing rules |
| `engine.py` | Causal market lifecycle and decisions, execution freshness checks |
| `execution.py` | Simulated matching, fee/risk ledger, exits, official settlement and checkpoint rollback |
| `storage.py` | Immutable records, operational projections, indexes, raw readers/recorders, exports |
| `models.py` | Settlement identity, read-only historical classification, incompatible-resume/exposure guards |
| `operation.py`, `shutdown.py` | Managed service health and safe shutdown coordination |
| `research.py`, `audit.py`, `analytics.py` | Ordered replay, dataset audits, predeclared walk-forward tests, retained-data metrics |
| `dashboard.py`, `static/` | Local APIs, plain HTML/CSS/JavaScript UI and display stream |

There is no momentum import, activation registry or ModelGroup dispatch. One configuration is frozen for each executing run. Identity/version/hash fields remain necessary to interpret different experiments and old evidence.

## Execution and state

Per-market states include discovery, validation, warmup, evaluation/candidate, order-pending/partial-fill/cancelled, position-open/exiting, settlement-pending and closed. Error/halted states prevent normal processing. The exact transition graph is [defined in storage.py](../src/btc15/storage.py); this overview does not imply every state permits every transition.

State versions provide optimistic checks. In compact mode no-trade cycles do not append every historical transition, but operational state still exists. Execution actions commit records/claims/checkpoints together and restore in-memory state after a failed write. Database ownership prevents two collectors competing for a portfolio. Backtests should use a separate database.

## Configuration and persistence boundaries

Global `--config` wins over `DATA_DIR/strategy.json`, then built-in defaults. Saving in Settings does not mutate a running `Strategy` object. Resume validates configuration/mode and refuses incompatible legacy groups. [Recovery](SINGLE_STRATEGY.md) deliberately requires completing old exposure rather than silently omitting children.

[Data model](DATA_MODEL.md) distinguishes immutable records from mutable states/display/checkpoints. Input recording and SQL accounting are not one distributed transaction; replay is a new experiment, not permission to overwrite the historical ledger. First-fill entry evidence is retained atomically; skipped paper evaluations are replaceable snapshots.

## Display and analytics boundaries

The independent 5 Hz reference/display path supports a responsive UI. It does not supply substitute settlement samples or override processing-time freshness checks. Server-sent events send latest display snapshots, not a durable tick replay service.

Default history/results exclude archived strategies. The overview scopes to the selected Settlement Edge configuration/run. Archived evidence can be read by explicit scope/ID without loading old algorithms. Some list/report operations still load histories into memory; existing indexes/caches do not imply all reads are bounded SQL pagination. Compact-paper calibration/rejection statistics cover retained entries, not every considered market.

## Deliberate limits

NumPy, SQLAlchemy, PyArrow, HTTPX/websockets/cryptography, FastAPI and Uvicorn serve existing requirements. SQLite WAL is the default; PostgreSQL support exists but needs separate backend validation. The frontend has no bundler/runtime build requirement. Live configuration and `LiveTrader.submit` fail closed. No always-on host provisioning, remote dashboard authentication, auto-tuning, automatic raw retention deletion or guaranteed profitability is implemented.
