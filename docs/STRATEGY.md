# Strategy and assumptions

The question is whether the probability of the **settlement average** satisfying
the contract is higher than the executable cost after fees and slippage. A favored
side follows the actual comparator and reference/strike relationship; it is not a
standalone signal. Exactly at the strike, V1 does not choose a favored side.

Default entry window: `120 < close_time−now <= 480`. Suggested .85 minimum price,
.99 maximum price, .90 minimum conservative probability, quality ≥85 and net
edge/EV ≥.03 are configurable **ASSUMPTIONS**. Prices must have adequate executable
depth, spread ≤.04, valid metadata and fresh feeds. EXTREME regimes are rejected.
No indicators or Kalshi-implied prices dominate the base probability model.

For one purchased binary contract: `EV=p*(1-c)-(1-p)*c-fees-slippage`, equivalently
`p-c-fees-slippage`. Ask already incorporates crossing the spread, so the spread
is not subtracted a second time. Entry uses conservative side probability and an
upper fee estimate. Partial-fill fees are charged at execution. Remaining orders
are cancelled when the signal deteriorates, the side changes, data ages, the
no-entry boundary arrives, or the configured patience expires.

Passive quotes discount the ask by at most the configured discount, half-spread
and available edge, then snap to a valid tick. They never reprice upward after an
invalid signal. V1 makes **one entry attempt per market per run**; cancel/replace
and automatic chasing are deliberately not enabled until execution validation.
Aggressive entry is an explicit config choice with latency and depth-limited IOC.

Sizing modes: fixed contracts, fixed dollars or bankroll percentage, always capped
by worst-case cost, bankroll percentage, trade contracts/dollars, open exposure,
daily gross exposure, daily net loss and daily order count. Cancelled attempts
still consume the daily attempt/exposure budget. No martingale or loss-based sizing.

Exits: executable configured TP, probability invalidation/hold versus net sell
value, or entry-price × .75 hard stop. Otherwise wait for official settlement.
Stops are not guaranteed; full worthless settlement is the sizing baseline. TP
rounds upward to the actual market grid and still must cover simulated slippage.

Every numeric default in `config/defaults.json` is an explicit research assumption
except constants validated as contract semantics. Do not treat these settings as
optimal. No experiment updates running paper or live configuration. The required
improvement sequence remains collect → hypothesis → replay → held-out validation
→ paper validation → review → explicit approval before a live change.


## Code organization

The implemented algorithm is **BTC15 Settlement Edge** (`settlement_edge`):

| File | Responsibility |
| --- | --- |
| `src/btc15/strategies/settlement_edge/config.py` | Validated `Strategy` settings and configuration version |
| `src/btc15/strategies/settlement_edge/model.py` | Reference features, volatility, settlement probability and quality |
| `src/btc15/strategies/settlement_edge/rules.py` | Entry evaluation, fees, sizing, risk and passive pricing |
| `src/btc15/engine.py` | Shared causal event processing and recorded decisions |
| `src/btc15/execution.py` | Shared paper orders, fills, positions and checkpoints |

`btc15.config.Strategy` remains the public configuration import. Only one algorithm
is implemented. The folder layout gives future algorithms a clear location;
adding one still requires engine integration, UI support and validation. There is
no dynamic plugin loader or simultaneous multi-strategy execution.

## Edit settings in the dashboard

### Moderate Settlement Edge paper preset

`config/settlement-edge-paper-moderate.json` is a frozen paper experiment requested
on 2026-09-09. It changes only these original entry thresholds:

| Setting | Original control | Moderate paper |
| --- | --- | --- |
| Start accepting entries before close | 480 seconds | 600 seconds |
| Minimum entry ask | $0.85 | $0.80 |
| Minimum net edge and minimum net EV | $0.03 each | $0.02 each |

Both edge fields gate the same value after the conservative probability adjustment,
estimated fees and slippage, so both must change together. The lower price floor
admits cheaper favored contracts while still requiring conservative probability
at least 0.90. The wider window adds two minutes; entries still stop with 120
seconds remaining. Quality, warmup, calibration penalty, fresh-feed checks, spread,
liquidity, passive execution, sizing and loss limits retain their original values.
The built-in defaults remain available as the original control.

The local saved settings in `data/strategy.json` select this preset for new
sessions. The existing `dashboard-paper` checkpoint requires its original config;
start a new group after a clean shutdown and resolution of any open exposure:

```bash
uv run btc15 --config config/settlement-edge-paper-moderate.json dashboard --run-id dashboard-paper-moderate --port 8001
```

To resume the original local group, explicitly supply its frozen configuration:

```bash
uv run btc15 --config data/runtime/settlement-edge-paper-20260909.json dashboard --run-id dashboard-paper --port 8001
```

This preset is an assumption for paper observation, not calibrated or optimized
on historical returns. More eligible quotes need not produce passive fills or
better results. Review new trades and realized losses before further relaxation.

Local validation replayed 1,123,175 recorded input events from the last
`dashboard-paper` capture through the causal engine with execution disabled.
On 1,007,670 paired entry checks, the control admitted zero quotes; the preset
admitted 1,462 checks in **one market**. These are correlated quote updates, not
1,462 trade opportunities or fills. Net edge was the most frequent rejection
under the original settings. The diagnostic and its script are retained locally
as `data/runtime/settlement-edge-moderate-gate-review.json` and
`data/runtime/settlement_edge_gate_review.py`. This was an entry-gate diagnostic,
not a fill simulation, dataset-quality audit or held-out performance test.

### Saving settings

1. Open **Strategies** in the sidebar.
2. Set **Enable entries in new sessions** and edit the entry/risk fields.
   Expand **Advanced model, execution and risk settings** for other parameters.
   Probability fields use fractions: `0.95` means 95%. Prices and net value are
   dollars per contract. Leave take-profit blank to disable that exit threshold.
3. Select **Save for new sessions**. Invalid settings are rejected. A successful
   save atomically replaces `DATA_DIR/strategy.json`, default `data/strategy.json`.
4. Start a new session to use the saved configuration. Restart a dashboard that
   owns collection to update that collector. Saving does not start paper execution.

Settings are shared across dashboard modes and dashboards using the same
`DATA_DIR`; the Mode and Run filters do not choose separate settings files.
Running sessions keep their configuration. Historical records are not rewritten.
The page compares saved settings with that dashboard's startup configuration,
not with every external collector or paper service.

The enable switch defaults to true. When false, the strategy records skipped
entries with `STRATEGY_DISABLED`, and paper submission also rejects new entries.
Reference collection, evaluations and position-management logic remain available.
It is a next-session setting, not an emergency stop for a running process; see
[the kill switch](SAFETY.md#operating-the-kill-switch).

## Configuration precedence and reproducibility

CLI startup chooses settings in this order:

1. An explicit global `--config PATH` argument.
2. `DATA_DIR/strategy.json`, if present.
3. The built-in `Strategy` defaults.

A JSON file may contain partial overrides; omitted fields use built-in defaults.
An explicit file replaces the saved file as the input rather than merging with it.
`config/defaults.json` is an example and is loaded only when explicitly selected.
Use `uv run btc15 config` to inspect the effective settings for your environment.

```bash
uv run btc15 config > config/session.json
uv run btc15 --config config/session.json paper
# Resume that run using the same frozen settings:
uv run btc15 --config config/session.json paper --resume RUN_ID
```

A changed UI configuration must not be used to resume a run whose checkpoint
requires the original version. The new `enabled=true` default preserves legacy
configuration hashes; `enabled=false` changes the version. Every new run and
recorded evaluation still carries its effective configuration and version.

`GET /api/strategy` returns saved settings (or startup settings before the first
save), their version and the dashboard startup version. `PUT /api/strategy`
accepts a JSON settings object, validates it and saves it for new sessions. It
requires JSON and rejects a supplied foreign Origin. This is a local dashboard
control, not an authenticated multi-user administration service. Neither endpoint
activates live execution or changes an existing run.
