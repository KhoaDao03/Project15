# Project15 — BTC15 Settlement Edge

**This branch has one executable strategy: BTC15 Settlement Edge.** The current milestone is to make its data collection, paper execution, recovery and evaluation dependable before adding any other strategy. Conservative Confirmed Momentum and Volatility-Regime Momentum are not executable here. Their retained history is read-only.

This is a research/paper-trading application, not a claim of profitability. It uses real Kalshi market data when credentials are configured, but orders and fills are simulated. **Real-money submission is blocked in code.**

For this installation, [active paper settings](docs/ACTIVE_PAPER_SETTINGS.md) describe the current paper configuration, including [sustained-lead checks and late entries](docs/SUSTAINED_LEAD.md). See [paper exit execution](docs/EXIT_EXECUTION.md) for post-latency depth matching and separate stress diagnostics. The [stop-confirmation comparison](docs/STOP_CONFIRMATION_SHADOW.md) runs in a separate paper ledger.

## Start here

Read [Getting started from scratch](docs/GETTING_STARTED.md) for the complete Linux/macOS and PowerShell setup, credentials, frozen configuration, first run and restart instructions. Existing multi-strategy installations must first read [legacy retirement and recovery](docs/SINGLE_STRATEGY.md); a fresh clone is not permission to abandon an existing portfolio.

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then explicitly select this milestone's branch:

```bash
git clone --branch fix/fractional-passive-fills --single-branch https://github.com/KhoaDao03/Project15.git
cd Project15
uv python install 3.12
uv sync --python 3.12 --extra dev --locked
git branch --show-current
```

Python 3.12+ is required; the documented CI baseline is Python 3.12 on Ubuntu. There is no Node/frontend build step and SQLite needs no separate database server. The default branch may not yet contain this work.

### 1. Verify the software without market credentials

On a fresh checkout, leave both Kalshi credential variables unset/empty. The demo below writes synthetic data into a separate database. Do not use it as evidence of actual-market performance.

```bash
uv run --locked btc15 demo --output data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db dashboard --no-collect
```

Open `http://127.0.0.1:8000`, choose **BACKTEST**, and select the generated run. The demo dashboard does not start collection. Stop it before using the same port for paper trading. Repeating a backtest creates another independent run; it does not extend the previous portfolio.

### 2. Configure the real feed and freeze the first experiment

Follow the [environment and credential steps](docs/GETTING_STARTED.md#environment-and-credentials). Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`. Save the RSA private key locally, never in Git or chat. The example key path is a placeholder, not a bundled file. Both credential fields must be empty for a key-free discovery check; a nonexistent nonempty key path can fail client initialization.

Create an original-control configuration once. This command is valid in Bash and PowerShell and refuses to overwrite an existing frozen file:

```bash
uv run --locked python -c "from pathlib import Path; from dataclasses import asdict; from btc15.config import Strategy; from btc15.domain import dumps; p=Path('data/runtime/settlement-original.json'); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='utf-8').write(dumps(asdict(Strategy()))+'\n')"
uv run --locked btc15 --config data/runtime/settlement-original.json config
uv run --locked btc15 discover
uv run --locked btc15 init-db
```

`discover` checks public REST discovery/contract parsing, not authenticated streaming or fill readiness. Upcoming markets can be blocked until their strike is published. The freeze command uses built-in defaults deliberately; `config/defaults.json` is an older explicit example with a different fee precision, not an automatically loaded default.

### 3. Start one named paper run

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --run-id settlement-original --port 8000
```

The dashboard starts/resumes **one Settlement Edge paper engine**. It requires configured feed credentials, compatible checkpoints, available writer ownership, and a 10 GiB free-space reserve on the data filesystem. All strategy and freshness checks still apply; startup does not force a trade.

Use **Shut down safely** and confirm. Open positions are saved, not liquidated. After a clean stop, repeat the exact command above to resume the same run/configuration. Do not start a second collector against that database. See [Paper operation](docs/PAPER_TRADING.md) and [Troubleshooting](docs/TROUBLESHOOTING.md).

## One strategy, not one mutable experiment

| Entry setting | Original built-in control | Optional moderate preset |
| --- | --- | --- |
| Seconds remaining | `120 < remaining <= 480` | `120 < remaining <= 600` |
| Minimum ask | $0.85 | $0.80 |
| Minimum conservative probability | 0.90 | 0.90 |
| Minimum quality | 85 | 85 |
| Minimum net edge and EV | $0.03 each | $0.02 each |

Both presets are the same strategy. Only one selected configuration runs at a time. The optional [moderate preset](docs/STRATEGY.md#moderate-settlement-edge-paper-preset) does not become active merely by pulling this branch. New settings need a deliberately named experiment after prior exposure is resolved; checkpoints require their original configuration.

The optional [paper fill experiment](docs/FILL_EXPERIMENT.md) adds limit-price revalidation
and one bounded retry; a separate IOC preset tests more competitive execution. Neither
changes an existing frozen run automatically.

For this installation, [collector throughput recovery and service controls](docs/COLLECTOR_THROUGHPUT.md)
describe the measured performance fix and supervised operation.

## Operating modes

| Command | Behavior |
| --- | --- |
| `dashboard` | Named PAPER execution plus UI; default run ID `dashboard-paper` |
| `dashboard --observe-only` | Full research collection/evaluations without simulated orders |
| `dashboard --no-collect` | Viewing UI for the selected database; no collector started |
| `paper` | Standalone paper collector; prints its generated run ID on completion |
| `paper --resume RUN_ID` | Explicit compatible checkpoint resume |
| `paper-service --run-id NAME` | Named managed paper runner; see the Linux/WSL guide |
| `backtest INPUT...` | New offline replay using the same engine/executor |

`--observe-only` and `--no-collect` are mutually exclusive. Global `--config` and `--database` arguments go **before** the subcommand. A UI mode/history filter does not change the executing process.

## What you see and what is saved

**Live overview** shows current prices, one strategy card, selected run/configuration, execution state and rejection reasons. **Trade history** separates orders/cancellations, fills, completed results and retained entry evidence. **Results & accuracy** reports retained-data metrics. **Settings** saves parameters for future sessions, not an emergency stop.

Paper mode writes compressed `.jsonl.gz` source tapes, orders, cancellations, first-fill entry evidence, fills, fees, results and checkpoints. Latest rejected evaluations/status are replaceable snapshots, not a complete growing evaluation history. **Zero retained opportunities does not mean zero evaluations or zero attempted orders.** Observation-only collection keeps full evaluation history plus JSONL/Parquet inputs. See [Recording](docs/TRADE_RECORDING.md) and [Trade memory](docs/TRADE_MEMORY.md).

Default views exclude retired strategies; the explicit archive selector reads their evidence without running them. Configuration/run filtering matters when comparing results. Synthetic tests, public discovery, authentic replay and a live-connected paper session answer different questions; see [Validation](docs/VALIDATION.md).

## Documentation

[Documentation index](docs/README.md) · [From scratch](docs/GETTING_STARTED.md) · [Paper lifecycle](docs/PAPER_TRADING.md) · [Strategy/settings](docs/STRATEGY.md) · [No-trade troubleshooting](docs/TROUBLESHOOTING.md) · [Managed service](docs/AUTONOMOUS_PAPER.md) · [Replay](docs/BACKTESTING.md) · [Safety/recovery](docs/SAFETY.md)

The earlier measured single-strategy verification is retained in [SINGLE_STRATEGY_VALIDATION.md](docs/SINGLE_STRATEGY_VALIDATION.md). It is dated evidence, not a claim that a later checkout or the user's overnight session has been tested. No startup command deletes history; no live-trading activation procedure is part of this milestone.

## Quarantined settlement recovery

Invalid or changed metadata blocks trading but no longer discards a held contract. Final settlement recovery and the preview/confirmation command are documented in [Settlement recovery](docs/SETTLEMENT_RECOVERY.md). Recovery does not reopen trading or reset risk budgets.

Decision and execution audit records: [logging reference](docs/DECISION_LOGGING.md).

Active market routing and replay validation: [processing notes](docs/MARKET_PROCESSING.md).

Current IOC paper strategy: [trading strategy documentation](docs/TRADING_STRATEGY.md).

## Convergence paper experiment

The opt-in [convergence experiment](docs/CONVERGENCE_EXPERIMENT.md) lowers net entry
edge/EV to two cents and disables only the hold-value exit. It includes separate
entry-only and exit-only presets for comparison. Existing frozen runs keep their
settings; select the combined preset explicitly for a new run.

Entry safety: [reference receipt and model revalidation](docs/ENTRY_REFERENCE_REVALIDATION.md).

Entry reporting: [settlement, target-sale and stop-exit economics](docs/ENTRY_ECONOMICS.md).

Sequential paper trades: [same-market re-entry](docs/REENTRY.md).
