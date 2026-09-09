# Architecture

## Repository assessment (2026-09-08)

The starting repository contains only a one-line README and AGENTS.md. There is no
application, package manager, database, integration, environment file, or test suite.
Existing user changes to those files are preserved. The pasted specification is
the requested scope; AGENTS.md supplies engineering guidance.

## Components and flow

One Python service owns collection and paper execution. FastAPI serves a local
dashboard with research views and validated next-session strategy settings; the CLI runs discovery, collection, replay, exports, and analytics.
NumPy provides seeded settlement Monte Carlo, SQLAlchemy provides PostgreSQL and
SQLite development persistence, PyArrow provides compressed Parquet, and HTTPX,
websockets and cryptography implement the external API boundary. These dependencies
serve explicit requirements; no distributed queue or frontend build system is needed.

Kalshi REST metadata → strict BTC15 contract validator → settlement specification.
Authenticated WebSocket receipt → bounded queue → ordered worker → durable raw journal → Parquet chunks → normalized
reference/book/trade events → causal features/volatility → settlement simulation →
quality and conservative probability → persisted opportunity → signal → risk →
paper execution → positions → official result → analytics and trade packets.
Historical replay uses the same service and execution model in receive order.
REST refresh runs independently from socket receipt. Rollover updates each market
subscription in place, preserving the BRTI connection. Books are retained while
strike metadata is pending; a transport reconnect still requires fresh snapshots.
Model features/probabilities are reused within one source-tick/second while
executable book checks remain current;
opportunity snapshots are coalesced between evaluation deadlines unless the decision
changes. Queue depth and processing lag are measured. Paper execution actions use
atomic ledger/checkpoint commits with rollback and explicit operator resume.

## State machine

Per market: DISCOVER_MARKET → VALIDATE_MARKET → WARMUP → MONITORING →
ENTRY_WINDOW → EVALUATING → NO_TRADE or TRADE_CANDIDATE → ORDER_PENDING →
ORDER_PARTIALLY_FILLED/POSITION_OPEN/ORDER_CANCELLED → EXITING or
SETTLEMENT_PENDING → CLOSED. ERROR/HALTED block new entries. State changes are
transactional compare-and-swap operations with append-only transition events.
One execution writer owns a run; a database lease prevents overlapping writers.

## Persistence

PostgreSQL is the operational target; SQLite WAL is the explicitly supported local
development alternative, avoiding a mandatory database daemon for offline research.
Operational records use indexed IDs plus versioned JSON snapshots: runs, markets,
opportunities, transitions, orders, fills, results and experiments. Predictions and
configurations are append-only; outcomes are separate records. Raw events first go
to an fsync journal and are then compacted to atomic Parquet chunks. The journal
is retained for crash recovery. IDs and receive timestamps preserve causal replay.

## Probability design

Zero-drift log-price random walk is an ASSUMPTION, with volatility estimated only
from past official reference ticks. Simulate the future path and average the actual
settlement sample grid, holding known final-minute observations fixed. Missing past
samples cause rejection, never retrospective simulation. Compare rounded averages
using the parsed operator. Monte Carlo interval width, volatility disagreement and
an explicit uncalibrated-model penalty produce separate conservative side bounds.
The quality score is diagnostic and is never called settlement probability.

## Execution and paper design

Signals cannot submit orders. Execution rechecks authoritative time, feed freshness,
market validity, EV and worst-case loss limits. Paper entries model latency, visible
queue ahead, opposing traded volume, partial fills, fees and cancellation. Touches
alone cannot fill passive orders. Depth limits aggressive fills and exits. Each run
is isolated by mode and ID. No automatic parameter tuning is applied.

## Live safety model

LIVE is disabled by default and fails closed even if environment flags are enabled:
this release has no authorized production submission path. A V2 payload builder and
read-only reconciliation are reviewable boundaries, not a live-readiness claim.
Explicit user approval, validated reconciliation, paper evidence and a separate
deployment review are required before implementing production activation.

## Confirmed API differences

Current public BTC15 metadata uses close_time for the 15-minute boundary;
expiration_time can be a week later and expected_expiration_time five minutes later.
YES currently includes equality. Settlement uses 60 BRTI observations rounded to
two decimals. The standard feed's final-minute window is (close−60s, close]; 5 Hz
ticks are recorded separately and are not substituted for those 60 samples.
Price ranges can change and fractional quantities must be preserved. Current
crypto metadata reports exchange_index=2; discovery reads it from the series.
Rounding ties at exactly half a cent are not resolved by the wording: predictions
bracket half-even and half-up, and mark the ambiguity rather than claim verification.
See SETTLEMENT_MODEL.md and IMPLEMENTATION_REVIEW.md for sources and limitations.


## Strategy configuration boundary

The current algorithm is grouped under `strategies/settlement_edge/` with separate
configuration, model and rules modules. The engine and paper executor remain
shared. See [strategy organization and controls](STRATEGY.md#code-organization).

Dashboard configuration writes replace `DATA_DIR/strategy.json` atomically after
validation. CLI startup prefers an explicit `--config` file, then this saved file,
then built-in defaults. A running engine retains its frozen `Strategy` object.
The mutable saved file is not historical evidence: run/evaluation snapshots and
checkpoint configuration versions remain authoritative for replay and resume.
An entry-disabled configuration blocks both entry selection and paper submission;
it does not bypass settlement, exits, risk checks or the live guard.
