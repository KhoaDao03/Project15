# BTC15 Settlement Edge — rules and configuration

## Single-strategy scope

Only Settlement Edge is executable on this branch. The original and moderate files are configurations of that one algorithm, not separate simultaneously running strategies. Current scope is correctness and paper validation first. Retired algorithms are available only through Git history; retained old records remain readable. See [legacy recovery](SINGLE_STRATEGY.md).

## Decision rule

The model asks whether the probability of the contract's **settlement average** satisfying its comparator exceeds the cost of buying the favored side after estimated fees and slippage. The favored side follows the comparator/reference/strike relationship; at the strike it is undefined. A favored side alone is not a signal.

For one purchased binary contract, net expected value is `p - ask - fee_bound - slippage`, equivalent to `p*(1-ask) - (1-p)*ask - fees - slippage`. The ask already includes crossing the spread, so the spread is not deducted a second time. `min_edge` and `min_ev` both gate this same adjusted value. Probability is reduced by uncertainty/calibration and volatility-disagreement penalties; quality is a different diagnostic, not a probability.

All configured checks must pass: enabled entries, open/validated market, entry window, available favored side, healthy reference/book/clock/processing, verified fee metadata, warmup/quality, price limits, conservative probability, net EV, spread, liquidity, non-EXTREME regime and risk limits. The executor rechecks submission eligibility after computation. See [model](PROBABILITY_MODEL.md) and [settlement semantics](SETTLEMENT_MODEL.md).

## Original control

| Setting | Built-in value |
| --- | --- |
| Entry timing | `120 < close_time - now <= 480` seconds |
| Ask range | $0.85 to $0.99, also subject to net EV |
| Conservative probability | At least 0.90 |
| Quality | At least 85 |
| Net edge and net EV | At least $0.03 per contract each |
| Spread / top-level liquidity | At most $0.04 / at least 5 contracts |
| Reference warmup | 300 seconds, subject to data-quality requirements |
| Model calculation interval | 1 second |
| Passive execution | Enabled; default discount $0.01, patience 20 seconds, latency 0.25 seconds, queue multiplier 1.5 |
| Direct-account fee precision setting | `0.0001` |

These are research assumptions, not fitted/optimal values. The actual contract price grid and supported effective fee metadata are checked in code. Do not treat this table as an assertion about today's account charges.

At `545.962` seconds remaining, original timing correctly rejects entry as too early. Exactly 480 seconds passes timing; exactly 120 seconds does not. For a 15:00–15:15 contract, original timing permits entries from 15:07:00 inclusive to 15:13:00 exclusive. Passing timing alone does not authorize an order.

## Moderate Settlement Edge paper preset

The repository file [settlement-edge-paper-moderate.json](../config/settlement-edge-paper-moderate.json) changes exactly four fields from the current built-in control:

| Field | Original | Moderate |
| --- | --- | --- |
| `entry_window_start` | 480 | 600 |
| `min_entry_price` | 0.85 | 0.80 |
| `min_edge` | 0.03 | 0.02 |
| `min_ev` | 0.03 | 0.02 |

Other settings, including the exclusive 120-second cutoff, probability/quality thresholds, calibration, fees, passive execution and risk limits, are unchanged. The moderate window passes timing at `545.962`. It does not guarantee a qualifying signal or fill. More eligible quotes can still produce worse results.

To experiment with it, use the [exclusive configuration-freeze command](GETTING_STARTED.md#optional-moderate-experiment) and a new run after earlier exposure is resolved:

```bash
uv run --locked btc15 --config data/runtime/settlement-moderate.json dashboard --run-id settlement-moderate --port 8000
```

No repository update proves what an untracked local `data/strategy.json` contains or what a running process loaded. Earlier local gate-review measurements are historical evidence in the prior [strategy document](https://github.com/KhoaDao03/Project15/blob/c23981dd0475d10af24caf1dde372d9477e78e08/docs/STRATEGY.md), not bundled recordings or a guarantee for this experiment.

## Sizing, attempts and exits

Fixed-contract, fixed-dollar and bankroll-percentage sizing remain subject to per-trade, bankroll, open-exposure and daily limits. The default daily maximum is 20 submitted attempts; unfilled cancellations still consume attempt and gross-exposure budget. Daily buckets use UTC, not the operator's local midnight. A new control run carries forward applicable paper risk history.

The daily loss threshold blocks entries based on realized daily P&L; it is not a guarantee that an outstanding position cannot take losses beyond that threshold. No martingale sizing or automatic parameter optimization is implemented.

One entry attempt is permitted per market/run. Passive quotes discount the ask within configured spread/edge bounds and snap to valid ticks; there is no automatic chase/reprice loop. Exits consider executable take-profit, hard stop (default entry price times 0.75), probability/hold-value invalidation, or official settlement. Slippage, latency, liquidity and missing data can prevent an exit; full loss remains possible. [Paper trading](PAPER_TRADING.md) describes the matching assumptions.

Entry permission and existing-position management use separate safety gates.
Disabling entries or latching the risk kill switch does not disable an otherwise
safe configured exit. A missing/quality-blocked model allows only price-based
stops/take-profit, not an invented probability-invalidation signal. Stale inputs,
clock/feed problems, unverified fees and contract quarantine still block sells.
See [position-management policy](POSITION_MANAGEMENT.md) for the complete matrix.

## Configuration precedence and reproducibility

CLI startup uses, in order:

1. Explicit global `--config PATH`.
2. `DATA_DIR/strategy.json`, if it exists.
3. Built-in `Strategy` defaults.

A partial JSON file fills omitted fields from built-in defaults, not from the saved settings file. The explicit file is not merged with `data/strategy.json`. `config/defaults.json` is loaded only if selected and retains a different `fee_balance_precision="0.01"`; do not confuse it with the current built-in control. Freeze complete settings for reproducibility.

```bash
uv run --locked btc15 config
uv run --locked btc15 --config data/runtime/settlement-original.json config
uv run --locked btc15 --config data/runtime/settlement-original.json paper --resume RUN_ID
```

The first command shows settings for a new invocation, not necessarily the configuration of a running process. The second reads the frozen file created by the setup guide. Replace `RUN_ID` with the matching checkpoint's ID. Place global flags before the subcommand. Preserve config/source hashes and the exact source revision with evidence. Never modify a checkpoint to accept another config version.

## Edit settings in the dashboard

Open **Settings**, edit **Enable entries in new sessions** and the other fields, then use **Save for new sessions**. The save validates JSON and atomically replaces `DATA_DIR/strategy.json`. The page compares saved values to that dashboard's startup values, not every external writer's configuration. Probability inputs are fractions; blank take-profit disables that target.

Saving does not reconfigure or start a process. When startup uses an explicit frozen `--config`, that file still wins. Before adopting a changed setting, resolve exposure, stop cleanly, freeze the intended new config into another file, and choose a new run ID. Do not use changed settings to resume old inventory. The enable switch is not an emergency stop; use [Safety](SAFETY.md#operating-the-kill-switch).

Mode/run/history filters do not select another settings file and cannot activate real trading. `/api/strategy` exposes next-session settings; `/api/strategies` is the retained route for one overview card, not a registry.

## Code organization

[config.py](../src/btc15/strategies/settlement_edge/config.py) validates `Strategy`; [model.py](../src/btc15/strategies/settlement_edge/model.py) computes features/probabilities/quality; [rules.py](../src/btc15/strategies/settlement_edge/rules.py) applies entry, fee, pricing and sizing rules. [Engine](../src/btc15/engine.py) consumes causal events; [PaperExecutor](../src/btc15/execution.py) owns matching/accounting; [models.py](../src/btc15/models.py) contains only identity/history/recovery helpers. No retired strategy or multi-engine dispatcher is instantiated.
