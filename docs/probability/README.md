# Independent BTC probability laboratory

A read-only options-informed probability engine for Kalshi BTC 15-minute settlement averages. It is isolated from the existing Project15 strategy and trading runtime. Existing trading services need not run.

## Install and test

From the repository root, use Python 3.12+ and the existing pinned `uv.lock`:

```bash
uv sync --locked --extra dev
uv run --locked pytest -q tests/probability
uv run --locked ruff check src/btc_probability tests/probability
```

No added numerical/network/frontend dependencies and no Node build step. `btc-probability` is a new command; `btc15` preserves its previous behavior. The recorder's exclusive lock currently targets Linux/macOS (`fcntl`); Windows requires WSL.

## Synthetic end-to-end demonstration

```bash
uv run --locked btc-probability --config examples/probability/config.json synthetic data/probability/synthetic.sqlite
uv run --locked btc-probability dashboard data/probability/synthetic.sqlite --port 8015
```

Open http://127.0.0.1:8015. The banner says SYNTHETIC. Blue is model YES value, green is the selected market quote (midpoint by default, not executable), orange is BTC reference, and red is target on the right USD axis. The left axis is cents from 0–100. UTC clock labels, input ages, missing samples, raw/calibrated distinction, model diagnostics and replay controls are visible. Lines break on missing estimates or time gaps and are not smoothed. Select the terminal diagnostic explicitly; it never silently replaces an unavailable settlement model.

The synthetic generator uses a reproducible clock and stochastic price stream, synthetic option marks and synthetic quotes. It is not a historical market replay or a calibrated simulation of Kalshi liquidity. Existing synthetic output is never overwritten.

## Read-only live recording

Copy `examples/probability/.env.example` values into your shell environment **privately**. The package does not automatically load the trading project's `.env`. Only optional `PROB_KALSHI_API_KEY_ID` and `PROB_KALSHI_PRIVATE_KEY_PATH` are used, exclusively for read-only BRTI access. Public metadata/options discovery also works without them.

```bash
uv run --locked btc-probability record data/probability/live.sqlite --duration 120
uv run --locked btc-probability dashboard data/probability/live.sqlite --port 8015
```

Omit `--duration` for continuous recording. Restart the same command/configuration to resume the append-only recording. To view while collecting, start the dashboard in a second terminal. Stop with Ctrl-C. This does not start/enable any Project15 service or trade. The HTTP application accepts only GET/HEAD and binds to localhost.

Live headline probabilities remain **UNAVAILABLE** while the authoritative half-cent tie convention is unresolved. The versioned family profile verifies other supported normal mechanics and blocks changed semantics; a JSON review cannot override it. See [settlement verification](../../SETTLEMENT_VERIFICATION.md) and [DATA_CONTRACTS.md](DATA_CONTRACTS.md). Recording, numerical tests, and separately labeled assumption-based research diagnostics continue.

Live metadata polls every 15s, books every 2s, and the option snapshot fetch loop refreshes after a 20s wait. It queries two nearby expiries and up to seven calls per expiry, with conservative rate limits and bounded retries. A snapshot may fail quality criteria despite HTTP success. Reference WebSocket reconnection stops after five consecutive failures and records the failure. Check the health endpoint, recording age, and reason codes, not just whether a process exists.

## Replay and compare providers

```bash
uv run --locked btc-probability replay data/probability/synthetic.sqlite --output data/probability/replay.jsonl
uv run --locked btc-probability --provider realized replay data/probability/synthetic.sqlite --output data/probability/realized.jsonl
uv run --locked btc-probability evaluate data/probability/synthetic.sqlite --output data/probability/evaluation.json
```

Replay makes no network requests. With no overrides it uses the recorded configuration and timer events. Provider overrides create counterfactual research outputs from the original causal inputs, not rewritten historical forecasts. Output files use exclusive creation. The dashboard's replay view progressively reveals recorded forecasts without interpolating future information.

Evaluation compares contemporaneous midpoint, realized settlement, options terminal and options settlement baselines at fixed horizons. Reports distinguish full availability from matched timestamps and report unique contracts/days, chronological splits, Brier/log loss, reliability, horizon/distance/volatility groups and day-clustered uncertainty when enough days exist. A synthetic score is only a software demonstration.

## Model intuition

With `tau=remaining_seconds/31536000`, approximate uncertainty is `S × sigma × sqrt(tau)`. Target distance divided by this uncertainty determines the rough terminal probability. An above-target price can become more likely to finish above as the clock shrinks without bullish drift. A pullback need not erase a large standardized lead. The headline model instead simulates the correlated **settlement average**, fixing already-observed samples. Both remain approximations, not guarantees.

See [MODEL.md](MODEL.md) for equations, units, precision and assumptions; [DATA_CONTRACTS.md](DATA_CONTRACTS.md) for lineage and clock semantics; [source log](../assumptions_and_sources.md) for verified versus unresolved claims; and [implementation status](IMPLEMENTATION.md) for actual validation and deferred research.

The recorder saves options, realized and fixed diagnostic baselines separately, so the provider selector changes the displayed model explicitly. The configured primary provider is selected initially. For a small ready-made sample, open `examples/probability/sample.sqlite`; it is a synthetic first-minute recording with full input lineage, not an outcome dataset.

The Latest view follows the market with the newest recorded forecast. Selecting a historical market switches to Replay. Research-model status is displayed separately from the guarded primary estimate; original historical verification flags are preserved.
