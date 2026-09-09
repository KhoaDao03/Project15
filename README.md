# Kalshi15 — BTC15 Settlement Edge

Settlement-aware research and simulated trading for **Kalshi BTC15 only**.
One executable strategy: **BTC15 Settlement Edge**. This is a research build,
not a validated profitability claim. Real-money order submission remains blocked.

## Start

Requires Python 3.12+ and uv. No Node build or new frontend framework is required.

```bash
uv sync --extra dev --locked
cp .env.example .env
uv run btc15 init-db
uv run btc15 dashboard
```

Open `http://127.0.0.1:8000`. With configured Kalshi read/feed credentials, the
dashboard starts or resumes the named `dashboard-paper` PAPER run. Orders/fills
are simulated. `--run-id NAME` selects another run; `--observe-only` explicitly
collects without orders; `--no-collect` displays a separately running writer.
The last two flags are mutually exclusive. Missing credentials show a setup error,
not fabricated prices or trades. Paper startup checks a 10 GiB free-space reserve.

**Upgrading from the multi-strategy version:** read
[scope and non-destructive recovery](docs/SINGLE_STRATEGY.md) first. Old runs with
momentum children cannot silently resume as controls. Resolve their pending orders
and positions using the compatible revision, then select a new single-strategy
run ID. Do not reset data or clear a crash lease to bypass recovery.

## Four focused views

**Live overview** shows current BRTI/contract prices, one Settlement Edge card,
its configuration hash/run, execution/entry state, reasons, positions and risk.
The headline results exclude retired strategies and follow the selected control
configuration or run. Disabled entries remain visible; hiding a card is not a stop.

**Trade history** distinguishes entry evidence, orders/cancellations, fills and
completed trades. **Results & accuracy** shows retained-evidence calibration and
realized P&L. **Settings** saves validated parameters for new sessions. History
stays still while reading; use Refresh to load newer records.

The History selector has an explicit **Archived strategies (read-only)** view.
Existing records, IDs and replay explanations remain available without loading or
running retired strategy code. No startup/reset/cleanup deletes historical records.

## Configurations: one strategy, one active configuration

The original control defaults remain `120 < seconds_remaining <= 480`, minimum
ask $0.85, conservative probability >= 0.90, quality >= 85, and net edge/EV >= $0.03
per contract after estimated costs. Spread, liquidity, metadata, freshness, regime
and risk checks must also pass. These are research assumptions, not optimized values.

The existing moderate preset changes only entry-window start to 600 seconds,
minimum ask to $0.80, and both minimum net edge/EV to $0.02. No preset or setting
is silently selected by this scope change. Use a frozen config deliberately:

```bash
uv run btc15 --config config/settlement-edge-paper-moderate.json dashboard --run-id settlement-paper --port 8001
```

`--config` overrides `DATA_DIR/strategy.json`; absent both, built-in defaults apply.
Saving settings does not change an active run. Resume with its original config.
Configuration hashes, version identities and raw sources remain attached to evidence.
See [strategy semantics and presets](docs/STRATEGY.md).

## Data and execution

Paper mode keeps one durable compressed `data/raw/<session>.jsonl.gz` tape plus
orders, cancellations, first-fill entry evidence, fills, fees, settlement/results
and checkpoints. Skipped evaluations and periodic status are replaceable live
snapshots. An empty opportunity-history view does not mean no checks or orders.
`btc15 collect` and `dashboard --observe-only` retain full research evaluations
and JSONL/Parquet recordings. Read [recording limitations](docs/TRADE_RECORDING.md).

The shared engine uses the standard BRTI settlement samples; the independent 5 Hz
reference display is not a replacement for them. It validates the contract's
close time, comparison/rounding and price grid. UI prices do not authorize orders.
Passive fills require eligible opposing volume, latency and conservative queue
accounting; quote touches do not guarantee fills. The fractional-quantity fix is
retained. One attempt per market/run and daily attempt/exposure limits still apply.

Only one writer may own a database. SQLite WAL is the local default; PostgreSQL
support and the existing Compose/service configuration remain available.

```bash
uv run btc15 paper
# In a separate terminal, using the same database:
uv run btc15 dashboard --no-collect
```

Never commit `.env` or private keys. Public `btc15 discover` needs no credentials;
authenticated reference/order-book collection requires an appropriate RSA API key
configured locally. No real order-submission endpoint is enabled.

## Shutdown and recovery

The dashboard's **Shut down safely** stops entries, cancels unfilled remainders,
drains/flushes data, and saves positions before exiting. It does not liquidate
positions or delete evidence. Restart the same compatible run/config to resume.
A crash lease requires operator review; no automatic lease stealing is introduced.
`btc15 halt` writes the persistent HALT switch; it is not a guaranteed liquidation.
See [paper lifecycle](docs/PAPER_TRADING.md), [safety](docs/SAFETY.md), and the
[managed single-strategy service](docs/AUTONOMOUS_PAPER.md).

## Offline verification and research

```bash
uv run btc15 demo --output data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db dashboard --no-collect
uv run pytest -q
uv run ruff check src tests
uv build
```

The demo is **synthetic**, not historical returns. Choose BACKTEST for its results.
Do not mix repeated backtests or different configurations into a portfolio claim.

```bash
uv run btc15 audit data/raw/SESSION.jsonl.gz
uv run btc15 --config config/original.json backtest data/raw/SESSION.jsonl.gz
uv run btc15 analytics --mode BACKTEST --run RUN_ID
uv run btc15 export OPPORTUNITY_ID
uv run btc15 walk-forward config/walk-forward.example.json
```

Replace placeholders with original complete inputs/configs. Archived strategy
results can be read with `analytics --archive --run RUN_ID`, but archived algorithms
cannot execute in this version. Counterfactuals belong in new runs, never rewrites
of recorded predictions or fills. Authentic overnight validation remains separate
from unit tests, public REST discovery and synthetic end-to-end replay.

The retired commands `models`, `model-paper`, `model-backtest`, and `model-comparison`
are deliberately removed. See [SINGLE_STRATEGY.md](docs/SINGLE_STRATEGY.md) for the
API history scope, known compatible legacy revision, and migration safeguards.
