# Local paper setup

For a fresh server use [cloud deployment](CLOUD.md). This guide creates an isolated
local paper experiment. Existing installations should use [recovery](SAFETY.md)
instead of creating a new portfolio to bypass exposure or ownership checks.

## Install

Use the reviewed checkout, Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run commands from the project root:

```bash
uv sync --python 3.12 --extra dev --locked
uv run --locked btc15 --help
```

Use a synchronized clock, stable network and more than 10 GiB free data space.
SQLite is the default. Node.js is only needed for JavaScript tests. Linux/WSL is
required for the managed systemd service; native Windows is not an endurance claim.

## Offline smoke test first

```bash
uv run --locked btc15 demo --output data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db dashboard --no-collect --port 8000
```

Open `http://127.0.0.1:8000`, select BACKTEST and inspect the generated run. These
inputs are synthetic. Replaying creates a new run; keep it separate from actual
paper/live performance. Stop the viewer before starting another on port 8000.

## Environment and credentials

Copy [.env.example](../.env.example) only if `.env` does not already exist:

```bash
if [ ! -e .env ]; then cp .env.example .env; fi
mkdir -p secrets
```

In PowerShell, use `Copy-Item` only after checking `Test-Path .env`. Edit locally:

```dotenv
TRADING_MODE=PAPER
ENABLE_LIVE_TRADING=false
DATABASE_URL=sqlite:///data/btc15.db
DATA_DIR=data
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=
LOG_LEVEL=INFO
```

Actual-feed paper collection needs a valid API-key ID and protected RSA key file.
The example key path is not a supplied key. For offline work leave both values
empty; a nonempty key path is loaded even by public discovery. On Unix, use
`chmod 700 secrets` and `chmod 600` for `.env` and the created key. Never share keys.
These paper commands do not start the [separate real-order service](LIVE_TRADING.md).

`DATA_DIR` selects tapes, saved settings and HALT; `DATABASE_URL` selects the ledger.
CLI `--data-dir` additionally defaults the ledger to `<data-dir>/paper.db`, unless
`--database` is explicit. Existing shell/service variables can override `.env`.

## Freeze a configuration

Create the built-in control once, refusing overwrite:

```bash
uv run --locked python -c "from pathlib import Path; from dataclasses import asdict; from btc15.config import Strategy; from btc15.domain import dumps; p=Path('data/runtime/settlement-original.json'); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='utf-8').write(dumps(asdict(Strategy()))+'\n')"
uv run --locked btc15 --config data/runtime/settlement-original.json config
```

If the file exists, inspect/reuse it. This built-in control is different from the
[active presets](ACTIVE_PAPER_SETTINGS.md). `config/defaults.json` is also an explicit
example, not an automatically loaded configuration. See
[configuration precedence](STRATEGY.md#configuration-and-deployment).

## Start the first paper session

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json discover
uv run --locked btc15 --config data/runtime/settlement-original.json init-db
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --run-id settlement-original --port 8000
```

Discovery may reject future markets awaiting a strike; public REST success does
not verify authenticated streaming. Initialization creates missing schema, not a reset.
In the dashboard select PAPER and the matching run. Confirm collection, evaluation,
configuration and paper execution health; a moving 5 Hz display is insufficient.

Indicators need 33 contiguous minute candles or a validated seed. Official-reference
ATR has its own warm-up/fallback rules. Other quality, price, timing and risk checks
still apply. Use [troubleshooting](TROUBLESHOOTING.md) when no trade is allowed.

## Stop, resume or compare

Use **Shut down safely** and wait for confirmation. It preserves positions rather
than liquidating them. Resume with the same database, frozen file and run ID.
After a crash, follow [safety and recovery](SAFETY.md); do not delete checkpoints or
clear ownership blindly. Settings edits apply to future sessions.

A moderate experiment uses `config/settlement-edge-paper-moderate.json`, frozen
under a distinct name using the same exclusive-creation procedure. Resolve earlier
exposure first; new names do not reset daily risk. Multiple assets need
[separate directories and ledgers](CRYPTO_PAPER.md).

Next: [paper lifecycle](PAPER_TRADING.md), [managed services](AUTONOMOUS_PAPER.md),
[backtesting](BACKTESTING.md), [tests](../tests/README.md).
