# Kalshi15

Settlement-aware research for **Kalshi BTC15 only**. This is an engineering V1,
not a validated strategy or a profitability claim. LIVE order submission is disabled.
The original request is preserved in [docs/REQUEST.md](docs/REQUEST.md).

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). No Node build is required.

```bash
cd /home/devk/Project15
uv sync --extra dev --locked
cp .env.example .env
uv run btc15 init-db
uv run btc15 dashboard
```

Open **http://127.0.0.1:8000**. The dashboard initially shows no observations. It has
mode/run filters, searchable opportunities, replay charts/timeline, calibration,
P&L and JSON APIs. It binds to localhost and has no order-submission endpoint.

## Try the complete offline flow

```bash
uv run btc15 demo --output data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db dashboard
```

Choose **BACKTEST** in the dashboard. This is explicitly synthetic data: it tests
partial fills and settlement accounting, not model calibration or returns.
The backtest prints its run ID. Keep research datasets separate from synthetic demos.

## Collect official market data

Public discovery needs no credentials:

```bash
uv run btc15 discover
```

Authenticated WebSockets require `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH` in `.env`. Use an existing appropriate Kalshi RSA API key.
Never commit the key or `.env`. The private key is read locally; it is not stored
in research records. Set `DATA_DIR` and `DATABASE_URL` before collection.

```bash
uv run btc15 collect
# Optional bounded capture:
uv run btc15 collect --seconds 900
```

Records current/next market metadata, standard BRTI and 5 Hz BRTI, book snapshots
and deltas, trades, ticker/lifecycle events, timestamps, sequence and connection
health. Data is journaled under `data/raw/`, then compressed to Parquet. Collector
mode evaluates and records opportunities but does not simulate orders.

## Start PAPER mode

```bash
# .env: TRADING_MODE=PAPER and ENABLE_LIVE_TRADING=false
uv run btc15 paper
```

The collector and paper runner are alternatives: run **one writer per database**.
Run the dashboard alongside it. Missing/invalid contracts, missing strikes, stale
feeds, unsupported fees, clock skew and risk violations block entries. New orders
are allowed only when `120 < seconds_remaining <= 480` by default.

`uv run btc15 halt` creates `DATA_DIR/HALT`; the runner cancels pending paper entries
and prevents new entries. It does not promise liquidation. Remove the file only
after reviewing the cause and restarting the runner. A killed process leaves a
writer lease intentionally; consult [SAFETY.md](docs/SAFETY.md) before recovery.

## Database and configuration

SQLite WAL is the local default. To use PostgreSQL:

```bash
docker compose up -d db
# Set DATABASE_URL in .env:
# postgresql+psycopg://btc15:local-development-only@localhost:5432/btc15
uv run btc15 init-db
```

The Compose password is for localhost development. Dependencies are locked in
`uv.lock`. Schema initialization is automatic; this initial release has no schema
migration runner. `LOG_LEVEL` controls logs; default is INFO.

```bash
uv run btc15 config > config/research.json
uv run btc15 --config config/research.json paper
```

All strategy defaults are **ASSUMPTIONS**. JSON files may supply just overrides;
unknown keys and invalid values are rejected. Every decision saves the complete
configuration, version hashes, source snapshot reference and model versions.
Changing a JSON file does not modify an already-running process.

## Tests and research

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run btc15 backtest data/raw/<session>.jsonl
uv run btc15 analytics --mode BACKTEST --run <run-id>
uv run btc15 export <opportunity-id>
uv run btc15 walk-forward config/walk-forward.example.json
```

Replace placeholder paths in the walk-forward manifest with your recordings.
Counterfactuals use a new run, preserving original decisions:

```bash
uv run btc15 --config config/experiment.json backtest data/raw/<session>.jsonl --parent-run <original-run>
```

Use the journal for an entire capture, or one Parquet chunk for a bounded replay;
never replay both copies together. The Python `replay_files` API supports an ordered
list of nonoverlapping captures in one run. Public candles cannot reconstruct queue
position or passive fills. No historical order-book dataset is bundled.

Historical CF data can be downloaded when the account has the required entitlement:

```bash
uv run btc15 reference-history --timestamp 2026-09-08T18:00:00.000Z --output data/reference-hour.json
```

This stores the upstream response, not a fabricated tradable backtest tape.

## Status and limitations

| Capability | Status |
| --- | --- |
| Public BTC15 discovery | READY; current endpoint checked |
| Authenticated data collection | READY WITH LIMITATIONS; credentials/entitlements and a stream soak test required |
| Recorded/synthetic replay | READY WITH LIMITATIONS; no historical edge validation |
| Paper simulation | READY WITH LIMITATIONS; queue, latency, fee and model assumptions need empirical validation |
| Autonomous production paper operation | NOT READY; authenticated recovery and long-running tests remain |
| Live trading | NOT READY; submission is blocked, even with activation flags |

Confirmed API differences: use `close_time`, not delayed `expiration_time`; YES
includes equality; settlement rounds the 60-sample BRTI average to two decimals;
5 Hz updates are separate from the 60 settlement samples; prices/quantities are
fixed-point; current BTC15 metadata routes to shard 2. See sources and exact
limitations in [SETTLEMENT_MODEL.md](docs/SETTLEMENT_MODEL.md).

Read [IMPLEMENTATION_REVIEW.md](docs/IMPLEMENTATION_REVIEW.md) before relying on
results. Other documentation: [architecture](docs/ARCHITECTURE.md),
[strategy](docs/STRATEGY.md), [probability](docs/PROBABILITY_MODEL.md),
[data model](docs/DATA_MODEL.md), [paper trading](docs/PAPER_TRADING.md),
[backtesting](docs/BACKTESTING.md), [trade memory](docs/TRADE_MEMORY.md),
[safety](docs/SAFETY.md), [live trading](docs/LIVE_TRADING.md).
