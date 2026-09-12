# Getting started from scratch

This guide targets the Settlement Edge-only branch, not a generic three-strategy bot. Its default first experiment uses the **original built-in configuration**, frozen into a local file. The optional moderate configuration is described separately; neither is a profitability claim.

**Existing installation?** Do not follow fresh-data steps to bypass open positions, a halted checkpoint or a writer lease. Back up first and read [single-strategy retirement/recovery](SINGLE_STRATEGY.md). A fresh checkout and a fresh trading portfolio are different things.

## Prerequisites

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/). Open a new terminal and check `git --version` and `uv --version`. The commands below ask uv for Python 3.12; the package allows Python 3.12+. See uv's [Python installation guide](https://docs.astral.sh/uv/guides/install-python/) for platform-specific installation help.

Use Linux, macOS or PowerShell for the basic CLI examples. CI has exercised Ubuntu/Python 3.12; providing PowerShell commands is not a native-Windows validation claim. The managed `paper-service`/systemd procedure uses POSIX signal handling and belongs on Linux/WSL, not native PowerShell.

Have more than 10 GiB free on the data filesystem, a synchronized host clock, a stable network, and a machine that stays awake for a live paper session. SQLite is the default. Docker, PostgreSQL, Node and an npm build are **not prerequisites** for the local setup.

## Get the correct branch

From the parent directory where the new checkout should live:

```bash
git clone --branch fix/fractional-passive-fills --single-branch https://github.com/KhoaDao03/Project15.git
cd Project15
git branch --show-current
git rev-parse HEAD
uv python install 3.12
uv sync --python 3.12 --extra dev --locked
uv run --locked btc15 --help
```

Run the remaining commands from this repository root. Relative database, key and data paths depend on that working directory. `uv run --locked` uses the project environment and refuses to rewrite an out-of-date lockfile; investigate a mismatch rather than updating dependencies casually. It does not require manual virtual-environment activation.

## Offline smoke test first

Before configuring keys, verify the application using synthetic inputs and an isolated database:

```bash
uv run --locked btc15 demo --output data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db dashboard --no-collect --port 8000
```

Open `http://127.0.0.1:8000`. Select **BACKTEST** and the generated run in the picker. Inspect orders, fills and completed results. These are fabricated test-market inputs designed for software testing, not historical Bitcoin returns. A successful demo does not validate the authenticated feed.

Stop the viewing dashboard with **Shut down safely** and confirmation. Its database is separate from `data/btc15.db`. Never combine demo/replay totals with paper performance. Repeating the backtest adds a distinct run.

## Environment and credentials

Create `.env` from [.env.example](../.env.example) only when `.env` does not already exist; do not overwrite an existing installation's secrets/settings.

Bash (Linux/macOS/WSL):

```bash
if [ ! -e .env ]; then cp .env.example .env; fi
mkdir -p secrets
```

PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force secrets | Out-Null
```

Edit `.env` locally. For the default local database:

```dotenv
TRADING_MODE=PAPER
ENABLE_LIVE_TRADING=false
DATABASE_URL=sqlite:///data/btc15.db
DATA_DIR=data
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=
LOG_LEVEL=INFO
```

For real-feed paper trading, create appropriate Kalshi API credentials in your account, save the downloaded RSA private key at a protected local path, then fill in the API-key ID and that file path. The application's [client](../src/btc15/api.py) signs data-feed requests; it does not submit real orders. Feed access/entitlements must actually succeed before the bot can trade on current inputs. Do not paste the key into GitHub, logs, screenshots or chat.

The copied example initially names `secrets/kalshi-private-key.pem`, **which does not exist in the repository**. Either save your key there or correct the path. When testing without keys, leave **both** variables empty: even public discovery constructs the client and attempts to load a nonempty private-key path.

On Unix, restrict permissions after creating the files, for example `chmod 700 secrets` and `chmod 600 .env secrets/kalshi-private-key.pem`. On Windows, restrict access using the file's local permissions. Never change the live flags to get past a startup failure.

`DATA_DIR` controls tapes, saved settings and the HALT file; `DATABASE_URL` independently selects the database. Changing one does not relocate the other. Ensure all terminals and viewing dashboards target the same intended pair. Existing shell/service environment variables can take precedence over `.env`.

## Freeze the original configuration

Run once; exclusive creation protects an existing run's settings:

```bash
uv run --locked python -c "from pathlib import Path; from dataclasses import asdict; from btc15.config import Strategy; from btc15.domain import dumps; p=Path('data/runtime/settlement-original.json'); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='utf-8').write(dumps(asdict(Strategy()))+'\n')"
uv run --locked btc15 --config data/runtime/settlement-original.json config
```

`FileExistsError` means the frozen file already exists. Inspect/reuse it; do not overwrite it to make the command succeed. The output should show `entry_window_start=480`, `no_new_entry=120`, `min_entry_price=0.85`, `min_edge=min_ev=0.03`, and `fee_balance_precision="0.0001"` for the current original control. This uses UTF-8 on both shells and avoids old PowerShell redirection encodings.

Do not substitute `config/defaults.json` assuming it is identical: that explicit historical example still contains `fee_balance_precision="0.01"`. It is not automatically loaded. [Configuration precedence](STRATEGY.md#configuration-precedence-and-reproducibility) explains saved settings and hashes.

## Check discovery and initialize storage

```bash
uv run --locked btc15 discover
uv run --locked btc15 init-db
```

`discover` should return JSON with validated or explicitly blocked contracts. A not-yet-published strike can legitimately block an upcoming contract. Public REST success does not test authenticated WebSocket subscriptions. `init-db` initializes missing schema/indexes, not a data reset.

## Start the first paper session

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --run-id settlement-original --port 8000
```

Open `http://127.0.0.1:8000`. Select **PAPER**, the normal Settlement Edge history scope, and `settlement-original`. Confirm paper execution is on, the collector and evaluations are current, and the selected configuration is the one you froze. The 5 Hz display moving alone is not proof the execution feed is healthy.

Warmup defaults to 300 seconds of required reference history; missing samples or bad feed health can extend the wait. The original entry window is more than 2 and at most 8 minutes before close. The bot must also pass probability, net-EV, price, spread, liquidity, metadata, regime and risk checks. **No trade may be the correct result.** Use [Troubleshooting](TROUBLESHOOTING.md), not relaxed safeguards, to identify the stage that blocked it.

## Stop and restart

Use **Shut down safely**, confirm, and verify completion. It stops entries, cancels pending remainders, saves filled inventory and flushes the tape. It does not liquidate open positions. After a clean stop, repeat the exact start command with the same database, frozen file and run ID.

After a crash, use [Safety and recovery](SAFETY.md); do not clear a lease or delete checkpoints as a routine restart. A run started with different configuration cannot be resumed by changing its settings file. The Settings page applies to future sessions, not the running portfolio.

## Optional moderate experiment

Only after resolving previous exposure and stopping the previous writer, freeze the moderate preset under a different name:

```bash
uv run --locked python -c "from pathlib import Path; from dataclasses import asdict; from btc15.config import Strategy; from btc15.domain import dumps; p=Path('data/runtime/settlement-moderate.json'); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='utf-8').write(dumps(asdict(Strategy.load('config/settlement-edge-paper-moderate.json')))+'\n')"
uv run --locked btc15 --config data/runtime/settlement-moderate.json dashboard --run-id settlement-moderate --port 8000
```

This is still Settlement Edge, with a wider entry window and lower price/EV thresholds. It is an experiment, not an automatic upgrade. A new run does not reset carried-forward daily risk usage. Existing multi-strategy installations have additional [retirement requirements](SINGLE_STRATEGY.md).

## Next operating tasks

Use [Paper trading](PAPER_TRADING.md) for standalone/bounded operation, [Managed paper](AUTONOMOUS_PAPER.md) for Linux/WSL without an attached terminal, and [Backtesting](BACKTESTING.md) to evaluate a real complete tape. The [documentation index](README.md) separates current procedures from earlier host-specific reports.
