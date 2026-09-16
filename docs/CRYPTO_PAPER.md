# BTC, ETH, SOL and XRP paper trading

Settlement Edge supports four Kalshi 15-minute series. Each process runs one
asset with its own configuration, reference history and paper ledger.
Use separate collector processes to run assets concurrently; the shared dashboard
shows all four on one port. The `btc15` executable and
Python package name remain unchanged.

| Asset | Series | Official feed index | Settlement decimal places | Paper preset |
| --- | --- | --- | --- | --- |
| BTC | KXBTC15M | BRTI | 2 | `config/settlement-edge-active-paper.json` |
| ETH | KXETH15M | ETHUSD_RTI | 2 | `config/settlement-edge-eth-paper.json` |
| SOL | KXSOL15M | SOLUSD_RTI | 4 | `config/settlement-edge-sol-paper.json` |
| XRP | KXXRP15M | XRPUSD_RTI | 4 | `config/settlement-edge-xrp-paper.json` |

The ETH/SOL/XRP presets copy the BTC paper experiment's entry, exit,
sizing and Bleep blend settings at the time multi-asset support was added,
changing only `asset`. Later BTC setting changes do not propagate into the
other assets' frozen runs. These thresholds have
not been calibrated separately for the additional assets.

## Shared dashboard (installed)

All four paper collectors are running for this installation. The shared UI is
at **http://127.0.0.1:8000**. Its four cards show current official prices,
collector state, open positions and reported realized P&L. Click BTC, ETH, SOL
or XRP to select the asset used by the detailed overview, trade history,
results, settings, downloads and live-price stream. Balances remain separate.

The active manifest is `data/runtime/crypto-dashboard.json`. It points BTC to
its existing `settlement-bleep-entry-75-v1` run and original ledger, and ETH,
SOL and XRP to their named `*-paper-v1` runs under `data/{eth,sol,xrp}`.
The existing BTC run and ledger were preserved. A concurrent BTC configuration
update restarted BTC and the shared viewer during verification; that current
configuration is retained. The new asset runs keep their own frozen settings.

```bash
uv run --locked btc15 fleet-dashboard data/runtime/crypto-dashboard.json --port 8000
```

The installed `btc15-dashboard.service` already owns port 8000; do not start
another viewer there. This command only serves the UI. Collectors run separately
as `btc15-ioc-collector`, `btc15-eth-collector`, `btc15-sol-collector` and
`btc15-xrp-collector` systemd user services. All four collectors are enabled for
automatic startup when WSL's user service manager starts; user lingering is
enabled. `start` alone does not enable startup on the next boot. Status and start commands:

```bash
systemctl --user status btc15-ioc-collector btc15-eth-collector btc15-sol-collector btc15-xrp-collector btc15-dashboard
systemctl --user start btc15-ioc-collector btc15-eth-collector btc15-sol-collector btc15-xrp-collector btc15-dashboard
```

To restore automatic startup if a collector has been disabled:

```bash
systemctl --user enable --now btc15-ioc-collector btc15-eth-collector btc15-sol-collector btc15-xrp-collector
```

These services cannot collect while Windows or WSL is shut down or suspended.

**Stop all safely** explicitly stops all four collectors and then closes the
shared dashboard after every writer confirms its saved state. Open positions
are preserved, not liquidated. A failure to acknowledge any collector leaves
the dashboard open with the affected asset identified. This behavior is tested
with temporary ledgers; deployment verification does not stop active portfolios.

For another installation, supply a JSON array with one entry per asset:

```json
[
  {"asset":"ETH", "config":"eth/paper-v1.json", "data_dir":"eth", "run_id":"eth-paper-v1"},
  {"asset":"SOL", "config":"sol/paper-v1.json", "data_dir":"sol", "run_id":"sol-paper-v1"}
]
```

Paths resolve relative to the manifest. The database defaults to each data
directory's `paper.db`; `database_url` can select an existing SQLite ledger,
including BTC's original `btc15.db`. Asset, directory and database must be
unique. The manifest configuration must match the selected run's checkpoint
(or original run record when no checkpoint exists). Audited checkpoint
configuration migrations preserve the immutable original run record.

The first asset is also served at `/`, preserving existing BTC bookmarks in
this installation. Asset views use `/assets/ETH/`, `/assets/SOL/`, etc. The
shared viewer never acquires collector ownership or starts a second engine.

## Start separate named runs

Complete the existing [installation and feed credentials](GETTING_STARTED.md)
setup. The same Kalshi feed credentials can be used by these local processes.
Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`.

Freeze a configuration per asset once. This works in Bash and PowerShell and
refuses to overwrite existing frozen files:

```bash
uv run --locked python -c "from pathlib import Path; [(p.parent.mkdir(parents=True, exist_ok=True), p.open('x').write(Path('config/settlement-edge-'+a+'-paper.json').read_text())) for a in ('eth','sol','xrp') for p in (Path('data/'+a+'/paper-v1.json'),)]"
```

Check public discovery for each asset:

```bash
uv run --locked btc15 --config data/eth/paper-v1.json --data-dir data/eth discover
uv run --locked btc15 --config data/sol/paper-v1.json --data-dir data/sol discover
uv run --locked btc15 --config data/xrp/paper-v1.json --data-dir data/xrp discover
```

Run each command below in its own terminal:

```bash
uv run --locked btc15 --config data/eth/paper-v1.json --data-dir data/eth dashboard --run-id eth-paper-v1 --port 8001
uv run --locked btc15 --config data/sol/paper-v1.json --data-dir data/sol dashboard --run-id sol-paper-v1 --port 8002
uv run --locked btc15 --config data/xrp/paper-v1.json --data-dir data/xrp dashboard --run-id xrp-paper-v1 --port 8003
```

The dashboards are at `http://127.0.0.1:8001`, `:8002` and `:8003`. An existing
BTC collector/dashboard can continue using its original command and port.
Each dashboard identifies its asset and official index; SOL/XRP underlying
prices and strike differences show four decimal places.

`--data-dir` selects that process's tapes, saved settings, reference cache and
halt file. It also defaults the database to `<data-dir>/paper.db`, overriding
`DATABASE_URL`; an explicit `--database` overrides that default. Global options
must precede the subcommand. Always keep a separate data directory **and**
database per asset, including with custom database URLs. A paper engine rejects
a database containing another asset's paper runs before recording its own run.

Each asset has independent bankroll and risk limits. With all four presets,
the nominal paper bankroll totals $4,000; there is no combined cross-asset
exposure limit. The shared dashboard sums reported P&L while detailed views remain
scoped to the selected asset. Collector ownership still prevents
concurrent writers to the same database. Four collectors also make more API
requests; rate-limit responses use the existing backoff behavior.

Use **Shut down safely** on the relevant dashboard and repeat its exact command
to resume. Open inventory, fees, risk counters and trade limits are recovered.
Pending order remainders are cancelled on restart, as for BTC. Do not change
`asset` on an existing run. Saving dashboard settings cannot switch its asset.
For headless operation, replace `dashboard ... --port ...` with
`paper-service --run-id ...`; provision separate service units and data paths
using the [existing service guide](AUTONOMOUS_PAPER.md).

## Offline demo

The synthetic generator uses the asset in the selected configuration. For
example, generate and replay XRP with a separate test directory:

```bash
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json demo --output data/xrp-demo/synthetic.jsonl
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json --data-dir data/xrp-demo backtest data/xrp-demo/synthetic.jsonl
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json --data-dir data/xrp-demo dashboard --no-collect --port 8013
```

Choose BACKTEST in that dashboard. Synthetic inputs remain labelled synthetic;
use a separate directory for subsequent actual-feed paper runs. Replay needs
the matching asset configuration and refuses foreign metadata/reference data
through the existing integrity gates. It never substitutes BTC prices.

## Contract and model boundaries

The parser validates the series, market prefix, official index wording,
15-minute duration, 60 one-second settlement observations, comparison operator,
rounding precision and payout. Public metadata fetched on September 12, 2026
is retained in `tests/fixtures/{eth,sol,xrp}15-20260912.json`. The rule prose uses
`ETHUSDRTI`, `SOLUSDRTI` and `XRPUSDRTI`; WebSocket IDs use the underscores shown
above. See the official [Kalshi value-feed documentation](https://docs.kalshi.com/websockets/cfbenchmarks-value)
and public series endpoints for [ETH](https://external-api.kalshi.com/trade-api/v2/series/KXETH15M),
[SOL](https://external-api.kalshi.com/trade-api/v2/series/KXSOL15M), and
[XRP](https://external-api.kalshi.com/trade-api/v2/series/KXXRP15M).

Monte Carlo settlement rounding and tie handling use each contract's precision.
The sustained-lead denominator and Bleep's minimum absolute ATR use one unit of
settlement precision: $0.01 for BTC/ETH and $0.0001 for SOL/XRP. Relative
volatility calculations and contract-price ticks/fees retain their existing rules.

Bleep seeds request the selected asset's Coinbase USD candles, Kraken USD
candles, or Binance USDT candles. The recorded seed carries the asset and cannot
be loaded by another asset's engine. Historical seed records without an asset
remain BTC-only. Official-history caches also carry and validate their index.
Exchange candles only seed Bleep indicators; settlement and the Project15 model
use the selected official CF Benchmarks reference. Freshness, feed recovery,
quality checks and official final-settlement evidence remain required.

BTC remains the default when `asset` is omitted. Adding the default field does
not change historical BTC configuration hashes or checkpoint compatibility.
Automated live submission remains blocked regardless of asset. The shared dashboard
also supports [user-confirmed manual real orders](MANUAL_TRADING.md) in a separate ticket and journal.

## Validation scope

`tests/test_crypto_assets.py` covers public metadata parsing and rejection,
asset-specific discovery/subscriptions, four-decimal model behavior, seed and
reference isolation, paper fills/restart/official settlement, the current blend
presets, collector routing, and dashboard identity. These tests use temporary
ledgers and simulated transports. Public REST metadata checks do not establish
authenticated streaming readiness or paper profitability. A connected endurance
session for each new asset remains a deployment validation step.

Public exchange seeding was also checked directly: ETH, SOL and XRP each loaded
100 validated closed Coinbase one-minute candles. CLI configuration and isolated
database creation were checked for all three presets. These read-only public
checks did not start an authenticated collector or change an existing ledger.

Working-tree regression validation: the complete 1,103-test suite finished with
1,101 passing and two outdated test expectations (historical hash reconstruction
and the browser formatter stub). Both tests were corrected and passed in a
separate `pytest --lf` rerun. The new 28 asset tests passed in the complete run.
Ruff, JavaScript syntax and Git whitespace checks passed. No authenticated
collectors or existing services were started/restarted during validation.


## Shared UI deployment validation — September 13, 2026

All four systemd collectors and the shared dashboard were observed running with
fresh official references and READY feed recovery. ETH, SOL and XRP completed
the 300-second reference warmup and evaluated the following market interval.
Their first post-warmup decisions were NO_TRADE because the configured entry
window and other entry conditions had not passed; no fills were forced.

Browser checks exercised each asset's overview, trade-history request and settings
view on port 8000. There were no JavaScript errors or mobile horizontal overflow.
Safe shutdown was tested with isolated ledgers, including waiting for every owner
and reporting a failed asset without closing the viewer. The affected 96-test
set passed after replacing a brittle test assertion tied to the mutable active
BTC preset with the immutable built-in control hash (95 passed in the combined
run, the corrected test passed on rerun). Ruff, JavaScript syntax and whitespace
checks passed.

Backups, service definitions, browser screenshots, live status and verification
reports are retained under `data/runtime/crypto-fleet-20260913T035357Z/`. An
independent BTC setting update/restart during this session was retained rather
than overwritten; it did not reset the BTC ledger or alter the other frozen runs.
This short connected check is not a multi-day endurance result.

### Four-market live overview

The shared Live overview displays BTC, ETH, SOL, and XRP in four desktop columns.
Each column includes realized net P&L after recorded fees, completed/open counts,
the current contract with strike, close countdown and YES/NO bid/ask quotes, and
the latest three purchases (open or closed). The header sums realized net P&L
across the manifest's active paper runs. These figures use completed trade results;
open trades show a pending result. Other runs and the selected history filters do
not change this active-paper overview.

Quotes are hidden when their collector snapshot is stale or belongs to another
run. Asset diagnostics expand below the overview; selecting an asset still opens
its own full history, results and settings. The overview polls every two seconds
plus request time. Verified with 13 targeted regression tests and desktop/mobile
browser checks on September 13, 2026.

Each asset also shows win rate, current win/loss streak, and longest win/loss
streaks for its active paper run. These use completed trade P&L after fees,
ordered by completion time (record ID breaks ties). Break-even trades count in
the win-rate denominator and reset streaks. Open trades are excluded; an empty
trade history displays a dash rather than a zero-percent win rate.

The overview also shows wins / losses per asset. Positive net P&L counts as a
win, negative as a loss; break-even trades count as neither.

## Fixed 62¢ hard stop across all assets

BTC, ETH, SOL and XRP now use `fixed_stop_price=0.62`: the hard stop
triggers when the held-side bid is at or below 62¢, regardless of entry price.
This replaces BTC’s 49¢ stop and the other assets’ entry-price multiplier.
Other per-asset entries, exits and probability settings are preserved.
The trigger is not a guaranteed execution price. Paper history and balances
are retained. Deployment evidence: `data/runtime/stop62-all-v1/`.

Fleet quote freshness is checked using a clock sampled after reading the reference
and market snapshots. A snapshot published during an HTTP request is not considered
future-dated merely because it is newer than the request's start time. The existing
two-second display freshness limits remain unchanged, including rejection of truly
future-dated snapshots. The response server time is sampled when building the response.

Fleet historical performance refreshes in the background every five seconds, with
`performance_updated_at` exposed per asset. Quote requests do not scan completed-trade
history. If a history refresh fails, performance is unavailable and fleet totals are
marked incomplete; fresh contract quotes can still be served. Trading decisions and
collector processing do not use this display cache.


The live worker is owned by `btc15-live-execution.service`, not the dashboard.
Start it with `systemctl --user enable --now btc15-live-execution` when installing
this setup. `systemctl --user restart btc15-dashboard` only restarts the UI/API proxy;
real order management continues in the execution service. Explicit **Stop all safely**
still asks execution to verify its obligations and disable future buys before stopping
collectors. See [live execution](LIVE_AUTOMATION.md) for handoff and recovery details.

## Separate paper simulation (staged; explicit activation)

Add `--separate-paper` to each existing `btc15 ... paper-service --run-id ...`
command at the approved deployment. The flag is opt-in so installing this code
alone does not change running services or a later unplanned restart. Keep each
asset's existing database, configuration and run ID. No service units are changed
by this implementation.

In this mode the collector maintains its own validated books and publishes strategy
signals without submitting simulated orders or managing simulated positions. A
spawned paper process consumes the same recorded events in order and owns the
existing paper ledger, paper risk state, fills and atomic portfolio checkpoints.
Live execution continues to use the collector's signal/book projections and its
own real-order authorization and reconciliation. Paper portfolio restrictions are
applied by the paper worker; collector signals do not incorporate simulated holdings.
The existing explicit HALT and collector freshness/integrity checks remain in force.

The handoff is nonblocking and bounded to 64 batches of at most 256 events. Paper
execution retains wall-clock freshness checks: backlog cannot produce artificial
fills at old quotes. Reference receipt markers cross the process boundary so a
pending newer reference still postpones execution. A full queue or failed worker
stops paper simulation explicitly; it never drops a book delta and continues filling.
Collection and real-order monitoring continue. The dashboard reports paper state
separately, retaining last-known paper positions with an interruption warning.

The collector owns the child process lifecycle. A normal stop drains the accepted
input queue before closing the worker, with a 30-second shutdown bound. On failure,
completed atomic transactions/checkpoints remain durable. Resume the same run on
the next approved collector restart; pending paper entry remainders are canceled,
held positions are restored, and fresh metadata/reference/books are required.
There is no automatic replay of downtime into paper fills. Raw recordings remain
available for explicit offline analysis. An interrupted paper session is not a
continuous execution-equivalence test.

This removes paper execution CPU work from the collector process, but repeats
strategy calculations in the simulator and shares the host and SQLite database.
It does not eliminate CPU/disk contention. Verify per-process CPU, queue lag and
paper-worker warnings during the monitored session after deployment.
