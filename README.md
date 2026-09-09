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

The dashboard has four views: **Live overview**, **Opportunity memory**,
**Results & accuracy**, and **Strategies**. Opportunity memory groups saved
evaluations by market, with counts for skipped entries and passed entry checks.
Expand a market to browse its evaluation cards with plain-language decisions and
reasons. Select **Completed trades** for realized results; passing entry checks
alone does not confirm a trade. Open **View explanation** for saved inputs,
charts, and the market-wide timeline. Technical records remain available in
expandable details and JSON. Use **Refresh history** to load newer records.

**Live overview** shows separate cards for all three strategies, including inactive
models with no saved evaluations. Each card identifies its version/configuration,
latest recorded decision, market, time and estimated edge. Cards show the latest
saved evaluation across runs in the selected mode; the run filter controls the
detailed view. **Evaluation**, **History** and **Results** open that card's run.
The run picker includes strategy names and full run IDs. New registered model
versions appear automatically, with configurations kept separate.

**Strategies** lists the model library and edits the current BTC15 Settlement Edge algorithm. The enable
switch controls new entries; disabled strategies still record evaluations and
retain position-management logic. Saving writes validated settings atomically to
`DATA_DIR/strategy.json` (default `data/strategy.json`). New CLI collection, paper,
and backtest sessions load this file automatically. An explicit `--config` file
has priority. Changes do not modify running sessions, and saving or enabling does
not start paper execution. Restart the dashboard to apply changes to its collector;
resume an existing paper run with its original configuration. Live stays disabled.

The algorithm lives in `src/btc15/strategies/settlement_edge/`: `config.py` holds
its validated settings, `model.py` computes features and settlement probabilities,
and `rules.py` contains entry, fee, sizing and risk rules. The event engine and
paper execution remain shared infrastructure. The existing algorithm remains the control. Independent Conservative Confirmed
Momentum and Volatility-Regime Momentum models are available through the
[paper model research workflow](docs/MOMENTUM_MODELS.md); both start inactive.
See the [strategy guide](docs/STRATEGY.md) for UI controls, configuration precedence,
and checkpoint-compatible session changes.

Open **http://127.0.0.1:8000**. The dashboard automatically loads current official
Kalshi BTC15 markets, including strike, quotes, volume and closing time. With the
authenticated collector running, current quotes and the separate 5 Hz BRTI display
stream into the browser, with display snapshots published at most twenty times per
second. Values update in place independently of model evaluations. Disconnections,
stale snapshots and invalid books are labelled; the browser reconnects automatically.
The YES/NO bid/ask panel also follows this stream in PAPER and LIVE views, with
the current ticker and a millisecond update timestamp shown above it. Evaluation polling cannot overwrite live quotes.
BACKTEST retains saved quotes, and evaluation features remain historical. When the
live feed becomes unavailable, the quote panel clears instead of presenting old
prices as current.
The evaluation area's first tile shows live 5 Hz BRTI in PAPER/LIVE views. Its
decision metrics separately identify the reference used in the recorded evaluation;
live price changes do not rewrite saved probabilities or their inputs. BACKTEST
continues to show its recorded reference. This display change does not accelerate
the model's evaluation interval or establish better trading outcomes.
Without collection, public REST snapshots refresh every 15 seconds. These fallback
snapshots need no credentials and are explicitly labelled. Current prices appear separately from
recorded research and are not saved as a replay dataset. The dashboard has
mode/run filters, searchable opportunities, replay charts/timeline, calibration,
P&L and JSON APIs. It binds to localhost and has no order-submission endpoint.

With API credentials configured, `btc15 dashboard` now starts a read-only collector
inside its own process and stops it cleanly on exit. This generates live prices and
research evaluations, not simulated or real orders. If a separate collector or paper
service is already running, use `btc15 dashboard --no-collect` to view its data.
An occupied writer lease is never stolen; startup failures appear in the dashboard.
After a crash, follow the recovery instructions before restarting. Stop the default
dashboard before starting a standalone writer on the same database.

The overview polls evaluations and collector health every second. Active
markets record evaluations at the configured interval (one second by default),
including warmup and outside-entry-window rejection reasons. Quote updates recheck
entry conditions between calculations using the latest cached probability; unchanged
checks do not repeat audit state transitions. Each decision records the model
calculation timestamp and its age. Entry limits remain
unchanged. Run choices use run IDs and refresh automatically. The first page of
opportunity memory and analytics refresh every five seconds; loading older pages
pauses list refresh. LIVE explains that execution is disabled instead of implying
that live evaluations should appear. A stopped collector is explicitly marked stale.

The collector publishes a single replaceable `market_display` projection after
ordered processing; it does not add high-frequency UI snapshots to immutable
research history. `/api/market-stream` reads that projection every 50 ms and sends
[server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events).
Reconnects start from the latest snapshot instead of replaying price history. The visible
snapshot age measures publication-to-server-delivery time, not exchange-to-browser
latency. Upstream delivery, processing and browser scheduling add latency. The
standard settlement reference and trading timing rules remain unchanged.

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

For operation without an attached terminal, see the
[managed PAPER service and health probe](docs/AUTONOMOUS_PAPER.md).

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

Fee correction in this follow-up: metadata-backed `quadratic` markets use zero
maker trading coefficient; scheduled changes are checked before execution. The
default balance precision is now `0.0001` for direct Kalshi accounting. Set `0.01`
for an applicable FCM account and review additional intermediary fees. Existing
experiment configurations and histories remain immutable.

## Tests and research

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run btc15 audit data/raw/<session>.jsonl
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
identical IDs across journal/Parquet mirrors are deduplicated and conflicting IDs
are rejected. The CLI accepts several chronological captures in one run:
`btc15 backtest first.jsonl second.jsonl`. Public candles cannot reconstruct queue
position or passive fills. No historical order-book dataset is bundled.

Historical CF data can be downloaded when the account has the required entitlement:

```bash
uv run btc15 reference-history --timestamp 2026-09-08T18:00:00.000Z --output data/reference-hour.json
```

This stores the upstream response, not a fabricated tradable backtest tape.

## Status and limitations

See [collection recovery](docs/COLLECTION_RECOVERY.md) for the resolved clock/rollover
gates and [the hardening review](docs/RESEARCH_HARDENING.md) for the follow-up changes,
measured authenticated evidence, and remaining acceptance gates. Software readiness
does not establish calibration, realistic counterfactual fills, or economic edge.
Those require consecutive real market data and independent observations.

Paper recovery: `btc15 paper --resume <run-id>` restores the committed checkpoint
and cancels resting remainders; it never invents fills during downtime. Use the same
strategy configuration. Legacy runs without checkpoints need forensic recovery.
A crash lease must be reviewed before release as described in `docs/SAFETY.md`.


| Capability | Status |
| --- | --- |
| Public BTC15 discovery | READY; current endpoint checked |
| Authenticated data collection | READY for authenticated research collection; repaired host clock and 1,000-second rollover capture passed audit |
| Recorded/synthetic replay | READY for audited research; streaming replay, provenance and held-out gates tested |
| Paper simulation | READY for conservative simulation; atomic accounting and explicit checkpoint resume tested |
| Autonomous production paper operation | IMPLEMENTED FOR VALIDATION; managed service, resume and health probe available; multi-day acceptance pending |
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

## Independent momentum experiments

The existing Settlement Edge model remains unchanged as the control. Two new,
inactive paper-only models have separate immutable configurations and portfolios:
**Conservative Confirmed Momentum v1** and **Volatility-Regime Momentum v1**.
Use `btc15 models list`, `btc15 model-paper --run-id <name>`,
`btc15 model-backtest <capture.jsonl>` and `btc15 model-comparison`.
Activation is explicit; existing `paper`/`paper-service` remain the control-only workflow.
Read [model rules, activation, versioning and volatility methodology](docs/MOMENTUM_MODELS.md)
and the [implementation readiness review](docs/MOMENTUM_READINESS.md) first.

The strategy overview now shows only enabled configurations. Historical configurations
remain accessible through run selection and history, without additional overview cards.
Both momentum v1 models were explicitly activated for PAPER on 2026-09-09. The current
local run is `momentum-paper`, with the dashboard viewing it on port 8001 using
`--no-collect`. Runtime logs and process IDs are under `data/runtime/`. These are
background processes in the current host session, not a newly installed startup service.
Live execution remains disabled.

The dashboard header includes **Shut down safely**. After confirmation, it stops
paper entries, cancels unfilled orders, saves positions and flushes data before
closing the bot and dashboard. Open positions are retained for resuming the same
run; see [shutdown and resume instructions](docs/PAPER_TRADING.md#dashboard-shutdown).

Starting `btc15 dashboard --port 8001` evaluates Settlement Edge and every active
momentum strategy on the shared market feed, without paper execution. To resume
paper trading, run `btc15 model-paper --run-id momentum-paper` with a separate
`btc15 dashboard --no-collect --port 8001`. Strategy cards show evaluation freshness
and whether paper execution is running.

The strategy overview includes lifetime realized net P&L after recorded fees and a
completed-trade count. The mode total includes all retained history, including
inactive strategies and older configurations; each card covers its exact configuration
across runs. Open positions are excluded. PAPER and BACKTEST stay separate; backtest
totals sum independent replays (potentially repeated datasets), not one portfolio.

Lifetime summaries and strategy cards also show maximum realized drawdown, average
net P&L per completed trade, win rate, profit factor, open exposure, open-trade count,
and current/longest win and loss streaks. Break-even trades reset streaks and count
in the win-rate denominator. Profit factor is undefined without losses. Drawdown
starts at zero and uses completed-trade P&L; streaks follow completion timestamps
with record IDs breaking ties. Combined metrics interleave independent runs. Open
exposure is remaining filled inventory at average entry cost including allocated
entry fees, adjusted for partial exits; it excludes unfilled orders and is not a
live market valuation. Expand “How these metrics are calculated” for UI definitions.

Live market prices places the 5 Hz Bitcoin reference above contract cards. Each
card groups the strike, live-reference distance and approximate countdown, followed
by YES/NO bids, asks and spreads in cents. Volume and close time sit below the
quotes; feed diagnostics are expandable. Missing strikes and stale reference data
show no distance, and disconnected/stale quote feeds clear their price values.
