# Testing and validation

Run commands from the repository root. Tests use isolated fixtures and temporary
ledgers; do not point them at live databases. A passing test suite establishes
code behavior, not profitability or readiness of the live feeds.

## Routine checks

```bash
uv sync --python 3.12 --extra dev --locked
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
uv build
```

For a focused change, run the relevant test modules first, for example:

```bash
uv run --locked pytest -q tests/test_strategy_settings.py tests/test_live_automation.py
uv run --locked pytest -q tests/test_research_log.py tests/test_cloud_signals.py
```

With Node installed, `node --check src/btc15/static/app.js` checks JavaScript syntax.
Node is not required to run the bot. Check formatting on changed Python files;
keep unrelated repository-wide formatting changes separate.

## Offline smoke test

```bash
uv run --locked btc15 demo --output data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run --locked btc15 --database sqlite:///data/demo.db dashboard --no-collect
```

Select BACKTEST and inspect order/fill/result records. These are synthetic inputs,
not historical trading evidence. Repeating the replay creates another run.

## Development scripts

The performance checks below use temporary ledgers and do not connect to an
exchange. Run `uv run --locked python scripts/SCRIPT.py --help` for arguments.

| Script | Purpose |
| --- | --- |
| [check_processing_throughput.py](../scripts/check_processing_throughput.py) | Replay a captured burst and measure processing headroom |
| [check_collector_throughput.py](../scripts/check_collector_throughput.py) | Replay a prepared incident/checkpoint, optionally profile and compare results |
| [check_dashboard_load.py](../scripts/check_dashboard_load.py) | Pace a capture while polling a temporary local HTTP/SSE server |
| [check_overload_drain.py](../scripts/check_overload_drain.py) | Check recorded drain transitions against independent book-depth accounting |
| [verify_settlement_behavior.py](../scripts/verify_settlement_behavior.py) | Save normalized synthetic outcomes for comparison with a known baseline |

Warm-history modes inject synthetic prices to exercise a warmed model. Drain
checks disable strategy evaluation. Neither measures trading profitability.
A comparison baseline must use the same inputs/environment and a known revision.
Choose new output filenames to avoid overwriting earlier evidence.

## What each check establishes

| Check | Establishes | Does not establish |
| --- | --- | --- |
| Unit/regression tests | Expected calculations, accounting, failure handling | Host/feed readiness or statistical edge |
| Synthetic end-to-end replay | Engine progresses through supported decisions and execution | Authentic market opportunities or real fills |
| Public discovery | REST connectivity and metadata parsing | Authenticated streaming |
| Authentic tape audit/replay | Behavior on captured inputs and declared starting state | Exact live timing, queue position or guaranteed fills |
| Live-connected paper session | Feed operation and simulated execution on that host | Real-money performance |

Before trusting replay, check timestamp continuity, warm-up/predecessor history,
book depth, fees, lifecycle and settlement coverage. Report missing data and open
positions as well as completed trades. Sampled research books have additional
[execution limits](RESEARCH_LOGGING.md#analysis-limits); see [backtesting](BACKTESTING.md).
Zero trades and negative performance are valid findings, not reasons to alter fixtures.

CI definitions: [paper validation](../.github/workflows/paper-validation.yml) and
[documentation validation](../.github/workflows/documentation-validation.yml).
Their trigger branches and commands are defined in those files. Dated validation
counts belong to the [historical reports](README.md#historical-evidence), not a
current acceptance claim. Real-money execution has separate [live controls](LIVE_AUTOMATION.md).
