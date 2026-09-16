# Validation — prove one strategy works before expanding

Current milestone: dependable Settlement Edge data collection, matching/accounting, recovery and evidence. These layers are separate:

| Layer | What success establishes | What it does not establish |
| --- | --- | --- |
| Unit/regression tests | Specified code paths and safety cases behave as asserted | Live feed readiness or statistical validation |
| Synthetic end-to-end run | Installed engine can progress through decisions/orders/fills/settlement on a test fixture | Real-market eligibility/profitability |
| Public `discover` | REST connectivity and current metadata parsing | Authenticated streaming or fills |
| Authentic tape audit/replay | Behavior on supplied recorded inputs and declared starting assumptions | Actual host timing/downtime or live fills |
| Live-connected PAPER run | Real feed operation with simulated execution on that host/session | Exchange fills or reliable long-run edge |
| Consecutive multi-day acceptance | Measured operational behavior under the tested conditions | Guaranteed safety/profitability outside them |

Never label synthetic inputs as authentic or infer a trade from a green process status.

## Install and run the current suite

```bash
uv sync --python 3.12 --extra dev --locked
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
uv build
```

Frontend assets are plain JavaScript. With Node installed as an optional developer check, use `node --check src/btc15/static/app.js`; Node is not required to start the bot. Formatting can be checked with Ruff on changed Python files. A repository-wide formatting finding should be reported separately rather than silently bundled into a docs change.

The existing [paper-validation workflow](../.github/workflows/paper-validation.yml) installs locked dependencies, exercises synthetic compact-input execution, runs the suite/lint/build, and probes public discovery with empty credentials. It does not access the user's local overnight database or private key. The [documentation workflow](../.github/workflows/documentation-validation.yml) checks current guide links and CLI syntax and exercises a fresh isolated setup plus the viewing dashboard. It does not start an authenticated paper collector.

## Reproduce the smoke flow

```bash
uv run --locked btc15 demo --output data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db dashboard --no-collect
```

Choose BACKTEST and the generated run. Inspect actual submitted-order/fill/result records. Do not change the fixture or thresholds merely to manufacture a successful result. The [behavior comparison script](../scripts/verify_settlement_behavior.py) records normalized outcomes for both presets:

```bash
uv run --locked python scripts/verify_settlement_behavior.py --output data/settlement-behavior.json
```

Use `--compare PATH` only with a deliberately saved baseline generated on the same inputs/environment and a known revision. A normal `--output` path can be overwritten by that script; choose a new evidence filename. A comparison excludes random identifiers/software metadata, not trading decisions or accounting.

## Dated evidence, not a universal current test count

[SINGLE_STRATEGY_VALIDATION.md](SINGLE_STRATEGY_VALIDATION.md) records the September 9, 2026 before/after comparison: original fixture 1,380 evaluations/3 fills/1 completed trade; moderate fixture 921 evaluations/zero fills, matching before and after. The then-remaining suite had 204 passes and two existing deprecation warnings. Later commits must be checked on their own SHA; future counts can change.

Earlier captured-feed and multi-model reports remain historical via the [documentation index](README.md). Their host-local paths and reported reset/clock actions are not prerequisites for a new setup or proof of current deployment.

## Authentic data acceptance

Preserve a complete consecutive capture with matching frozen config/revision, initial state and relevant predecessor/warmup sessions. Audit timestamps, missing data, books, fees, lifecycle and settlement coverage. Replay into another database and report attempted orders, cancellations, first buy fills, completed results, open positions and rejection evidence. Compact paper opportunity counts are fill-selected; they do not count all evaluations. See [Backtesting](BACKTESTING.md).

Use the same honest acceptance rule for no-trade sessions: identify whether startup, a signal gate, submission, matching or settlement blocked progress. Fix reproducible software defects before changing strategy assumptions. Negative performance and zero qualifying trades are valid findings.

For a live-connected run, verify credentials/entitlements, startup/resume, fresh execution data, real clock behavior, official results, disk reserve and healthy shutdown. Then test interruption/restart with outstanding simulated exposure, rollovers and UTC daily budgets across consecutive days. Keep the machine awake and monitor it; this chat and a browser tab are not an always-on host.

Performance scripts ([throughput](../scripts/check_processing_throughput.py), [dashboard load](../scripts/check_dashboard_load.py)) are development fixtures; the load script's injected warmup is synthetic. Do not use their inputs/results as trading-performance evidence. PostgreSQL, browser rendering, sustained host load and actual-data replay require their own reported verification. Live trading is [out of scope](LIVE_TRADING.md).
