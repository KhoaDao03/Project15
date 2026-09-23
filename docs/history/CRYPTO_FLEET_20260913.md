# Earlier crypto-fleet installation and validation

Archived from the former crypto guide. These are historical host/revision notes,
not current settings, service status, or deployment instructions. Use the
[current crypto guide](../CRYPTO_PAPER.md) and [settings](../ACTIVE_PAPER_SETTINGS.md).

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


## Validation scope

`tests/test_crypto_assets.py` covers public metadata parsing and rejection,
asset-specific discovery/subscriptions, four-decimal model behavior, seed and
reference isolation, paper fills/restart/official settlement, the current Bleep model
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

Fleet historical performance checks for changes in the background every five seconds, with
`performance_updated_at` exposed per asset. Quote requests do not scan completed-trade
history. If a history refresh fails, performance is unavailable and fleet totals are
marked incomplete; fresh contract quotes can still be served. Trading decisions and
collector processing do not use this display cache.


The live worker is owned by `btc15-live-execution.service`, not the dashboard.
Start it with `systemctl --user enable --now btc15-live-execution` when installing
this setup. `systemctl --user restart btc15-dashboard` only restarts the UI/API proxy;
real order management continues in the execution service. Explicit **Stop all safely**
still asks execution to verify its obligations and disable future buys before stopping
collectors. See [live execution](../LIVE_AUTOMATION.md) for handoff and recovery details.

