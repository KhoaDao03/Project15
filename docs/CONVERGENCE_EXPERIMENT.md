# Settlement Edge convergence paper experiment

The combined preset is `config/settlement-edge-combined-paper.json`. It retains
IOC execution, one bounded retry, the 120–480 second entry window, 90% minimum
conservative probability, quality checks, position sizing and existing risk limits.
It changes only:

- `min_edge` and `min_ev` to 0.02 (both check the same net value).
- `hold_value_exit_enabled` to false.

With the hold-value exit disabled, exits remain hard stop, take profit, probability
invalidation, and official settlement of remaining inventory. Holding longer can
give back unrealized gains. This preset is an experiment, not a profitability claim.

The dashboard shows the effective entry ceiling at the current conservative
probability, after estimated entry costs and rounding down to supported ticks.
This is an ask-price ceiling for probability/value filters, not an assurance that
quality, liquidity, timing, risk or execution checks pass. “No eligible price”
means probability/value requirements cannot be satisfied inside the configured
price range. Historical evaluations without this field show an unavailable value.

## Compatibility and selection

Built-in defaults and existing preset files retain their behavior. The new boolean
defaults to true and is omitted from the configuration hash when true, preserving
old frozen-run hashes. A false value changes the hash, so it cannot silently change
an existing checkpoint. Settings saves still apply to future sessions only.

After safely stopping the previous collector and resolving its exposure, select a
new run explicitly (do not run two collectors against the same database):

```bash
uv run --locked btc15 --config config/settlement-edge-combined-paper.json dashboard --run-id settlement-convergence-v1 --port 8000
```

An installed service with its own frozen `--config` continues using that file;
installing this code does not switch or restart the service.

## Controlled replay comparison

Replay identical recorded inputs with these configurations, using a separate
research database. Every backtest invocation creates a separate run.

| Configuration file in `config/` | Net edge / EV | Hold-value exit |
| --- | --- | --- |
| `settlement-edge-fill-taker-paper.json` | 0.03 | Enabled |
| `settlement-edge-entry-paper.json` | 0.02 | Enabled |
| `settlement-edge-exit-paper.json` | 0.03 | Disabled |
| `settlement-edge-combined-paper.json` | 0.02 | Disabled |

For each file, run:

```bash
uv run --locked btc15 --config config/settlement-edge-combined-paper.json --database sqlite:///data/convergence-replay.db backtest PATH_TO_RECORDED_INPUT
```

Compare fills, net P&L after fees, drawdown, holding time and exit reasons. These
presets provide the comparison; no measured replay results are claimed here.
