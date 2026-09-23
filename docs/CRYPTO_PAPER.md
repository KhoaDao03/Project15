# Multi-asset paper trading

Run one collector per asset with its own frozen configuration, reference history,
data directory and ledger. The fleet dashboard can display them together.
For real orders use [live automation](LIVE_AUTOMATION.md); fresh cloud setup is
[signal-only](CLOUD.md), with no paper simulation worker by default.

| Asset | Series | Official index | Settlement decimals | Preset suffix |
| --- | --- | --- | --- | --- |
| BTC | KXBTC15M | BRTI | 2 | `active` |
| ETH | KXETH15M | ETHUSD_RTI | 2 | `eth` |
| SOL | KXSOL15M | SOLUSD_RTI | 4 | `sol` |
| XRP | KXXRP15M | XRPUSD_RTI | 4 | `xrp` |

Presets are `config/settlement-edge-SUFFIX-paper.json`. Use
[current settings](ACTIVE_PAPER_SETTINGS.md), not earlier installation thresholds.
Each frozen run keeps its own settings and risk state. There is no combined
cross-asset paper exposure limit. [Commodities](COMMODITIES.md) use separate Pyth
contract semantics.

## Start separate named runs

Complete [local setup](GETTING_STARTED.md), then freeze each additional asset once:

```bash
uv run --locked python -c "from pathlib import Path; [(p.parent.mkdir(parents=True, exist_ok=True), p.open('x', encoding='utf-8').write(Path('config/settlement-edge-'+a+'-paper.json').read_text())) for a in ('eth','sol','xrp') for p in (Path('data/'+a+'/paper-v1.json'),)]"
uv run --locked btc15 --config data/eth/paper-v1.json --data-dir data/eth discover
uv run --locked btc15 --config data/eth/paper-v1.json --data-dir data/eth dashboard --run-id eth-paper-v1 --port 8001
```

Run SOL and XRP in separate terminals with their matching paths/run IDs and ports
8002/8003. Use `paper-service --run-id ...` instead of the dashboard command for
[headless operation](AUTONOMOUS_PAPER.md). Do not start a second owner for an existing
ledger or use a port already occupied by a service.

Global CLI options precede the subcommand. `--data-dir` defaults the database to
that directory's `paper.db`; explicit `--database` overrides it. Keep both paths
separate per asset. Resume with the same run/configuration after safe shutdown;
pending entry remainders cancel, while positions, fees and risk counters remain.
Never change the asset of an existing run.

## Shared dashboard

A fleet manifest contains entries such as:

```json
[
  {"asset":"ETH", "config":"eth/paper-v1.json", "data_dir":"eth", "run_id":"eth-paper-v1"},
  {"asset":"SOL", "config":"sol/paper-v1.json", "data_dir":"sol", "run_id":"sol-paper-v1"}
]
```

Paths are relative to the manifest. Asset, directory and database must be unique;
`database_url` can select an existing ledger. Configuration must match the run's
checkpoint. Serve the manifest with:

```bash
uv run --locked btc15 fleet-dashboard data/runtime/crypto-dashboard.json --port 8000
```

The viewer does not start a second engine. Asset pages live under `/assets/ETH/`,
`/assets/SOL/`, etc.; the first asset also serves `/`. Safe shutdown waits for
collector acknowledgments and applicable live-execution guards; it does not
liquidate positions. The separate executor owns real orders across UI restarts.
Dashboard totals can include [verified live-derived history](LIVE_AUTOMATION.md#dashboard-history).

## Offline demo and model boundaries

Use a separate synthetic directory and the matching asset:

```bash
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json demo --output data/xrp-demo/synthetic.jsonl
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json --data-dir data/xrp-demo backtest data/xrp-demo/synthetic.jsonl
uv run --locked btc15 --config config/settlement-edge-xrp-paper.json --data-dir data/xrp-demo dashboard --no-collect --port 8013
```

Select BACKTEST. Synthetic results are not observed market performance.
Contract parsing validates series, index, timing, averaging, precision, payout and
grid; foreign metadata/reference/seed history is rejected. Indicator seeds use
the selected asset's candles. Official references and outcomes determine
settlement; see [probability](PROBABILITY_MODEL.md) and [settlement](SETTLEMENT_MODEL.md).

[Earlier fleet installation and validation notes](history/CRYPTO_FLEET_20260913.md)
preserve dated measurements and obsolete stop settings. Tests are synthetic;
authenticated-feed and endurance validation remain deployment work.
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

### Recent-trade refreshes

Fleet recent-trade cards and the current-run five-trade panel use a background
snapshot checked with the historical statistics worker (five seconds after each
check completes). Unchanged history reuses the cached snapshot and performance totals.
Record revisions and a persistent SQLite journal connection's `data_version` detect
new fills, updates to existing orders, closed trades, and settlements without parsing
the order journal on every poll. Other journal writes can also invalidate the cache.
Requests for up to five current-run trades do not rebuild
journals. Paper records are read within one database transaction, and the live-fill
fallback is computed once per build. Other history filters and pagination retain
the full-history endpoint behavior.

Responses include `updated_at` and `stale`. A failed refresh keeps the previous
snapshot; checks delayed by more than 15 seconds are marked stale. Before the first
successful snapshot the endpoint returns 503 rather than inventing an empty history.
The stale threshold measures time since the last successful change check, so unchanged
history does not become stale merely because it has not been rebuilt.
The fleet cards retain their last successful rows and show “Update delayed” on
request failure or stale data. A successful fresh response clears the notice.

Trade history loads on tab entry, filter changes, pagination, or manual Refresh;
it does not poll. Analytics also loads on demand. Overview polling pauses while
another tab is selected or the browser document is hidden. Recent trades are fetched
only when their snapshot version changes (or an earlier request needs retrying), and
unchanged trade rows retain their existing rendered content. Closed results can still
refresh when a later settlement or order reconciliation changes the recorded outcome.
