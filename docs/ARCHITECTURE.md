# Architecture

Each collector runs one asset with a frozen strategy configuration and isolated
reference history and ledger. Supported assets are BTC, ETH, SOL, XRP, GOLD,
SILVER and WTI. A separate executor manages real orders; the fleet dashboard
serves views and forwards controls to it.

## Components and flow

```text
REST metadata/results + authenticated reference/book/trade feeds
                         |
                collector receipt queue
                         |
             ordered Engine input processing
                         |
           model calculations + entry decisions
                  /                  \
       paper execution          published signal/book
       when enabled                    |
                              live execution service
                                       |
                               exchange orders/fills

ledgers + projections + optional research archive -> dashboard / offline analysis
```

The cloud profile is signal-only: no paper worker or ordinary raw tape. Optional
[research recording](RESEARCH_LOGGING.md) captures received inputs and decisions.
Local paper mode can execute in the collector or an opt-in separate paper process.
Offline replay runs against a separate database.

| Module | Responsibility |
| --- | --- |
| `cli.py`, `config.py` | Commands, environment and configuration selection |
| `api.py`, `domain.py` | Feed/REST access, signing, contract and book validation |
| `runner.py`, `engine.py` | Receipt, ordered ingestion, reference/model state, decisions and publication |
| `strategies/settlement_edge/` | Features, probabilities, entry and risk rules |
| `execution.py`, `paper_worker.py` | Simulated orders, matching, positions and checkpoints |
| `execution_service.py`, `live_automation.py`, `manual_trading.py` | Real order controls, submissions, fills and reconciliation |
| `storage.py`, `models.py` | Historical records, projections, ownership and run identity |
| `research_log.py`, `research_coverage.py`, `execution_journal.py` | Capture, causal references, execution evidence and coverage |
| `dashboard.py`, `fleet.py`, `public_dashboard.py`, `static/` | Private/public views and controls |
| `operation.py`, `shutdown.py` | Health and coordinated shutdown |
| `research.py`, `audit.py`, `analytics.py` | Offline replay, audits and research reports |

## State and recovery

When processing lag reaches the freshness limit (at most two seconds), the
collector blocks entries and suspends calculations while applying every queued
input in order. It resumes only after the backlog drains and the usual reference,
book, metadata and integrity checks pass. This catch-up keeps a healthy socket
connected, avoiding self-created reference gaps. A queue at 80% capacity, broken
sequence, transport failure or stalled stream still uses reconnect recovery.
Research input-processing records retain the analysis-suspended flag for replay.

Paper execution commits records, claims and checkpoints together and rolls back
in-memory accounting on a failed write. Ownership prevents competing writers.
Live execution uses its own durable order journal and process lock; uncertain
orders require reconciliation before replacement. See [data model](DATA_MODEL.md)
and [live automation](LIVE_AUTOMATION.md).

Configuration precedence is explicit `--config`, saved settings, then defaults.
Settings edits apply to future sessions. Resume requires the original compatible
configuration and state; a new run is not a way to discard unresolved exposure.

## Evidence and display

Raw files, SQL ledgers and portable archives are separate persistence boundaries,
not one transaction. Research queue losses are reported and do not stop trading;
required execution-state write failures have different consequences.

The 5 Hz display and server-sent events are current views, not settlement history
or a replay stream. Some historical queries load full results. Dashboard history
can include verified live fills alongside simulations; see
[live history semantics](LIVE_AUTOMATION.md#dashboard-history).

SQLite WAL is the default. PostgreSQL and Parquet are optional extras. There is no
frontend build requirement; Node.js is needed only for JavaScript test checks.
