# Bleep settlement strategy

An ATR-based Normal-CDF predictor supplies probability for BTC, ETH, SOL and XRP.
Settlement-average evidence remains a separate confirmation check. See [Probability model](PROBABILITY_MODEL.md) and
[active presets](ACTIVE_PAPER_SETTINGS.md) for the exact calculation and thresholds.

## Entry

The standard window selects the side favored by the official reference relative
to the strike. The late window uses the expected settlement average. The bot
requires fresh same-side confirmation, valid contract metadata, reference and book
health, warmup/quality, timing, price/spread and risk checks.
The separate directional Bollinger entry veto is disabled for all seven assets;
Bollinger inputs remain part of the probability model’s indicator adjustment.

The selected-side Bleep probability must meet the configured floor in evaluation
and at order submission. Missing/nonfinite probability cannot pass. For the crypto
presets the floor is 83% after safety and market-respect caps in both windows and the purchase range is 80–95¢.

Entry confidence is capped at the selected-side book midpoint +6 percentage points
for BTC/ETH or +10 for SOL/XRP, with a maximum of 98%. The cap is reapplied to
the current book at submission. Both windows require capped confidence ≥83%.

Per-contract estimated EV is `capped_confidence - ask - fee_bound - slippage`. The ask already
includes crossing the spread. EV and costs are recorded, but the current presets
disable EV/net-edge entry vetoes. A probability threshold alone does not establish
positive expected value at the offered price.

## Exit

All four crypto presets use a 99¢ take-profit and 55¢ hard-stop trigger. Remaining
contracts settle. See [full-position paper execution](FULL_POSITION_EXECUTION.md)
and [live automation](LIVE_AUTOMATION.md) for execution details. Market liquidity,
latency and feed health affect execution; a trigger is not a promised fill price.

## Configuration and deployment

Fresh cloud preparation copies the current presets. There are no blended or
Project15 probability modes, component thresholds, simulation paths or random seeds.
`min_probability` and `late_min_probability` are the only entry probability floors.

An explicit CLI `--config` takes precedence over saved settings, then built-in
settings. Dashboard edits are saved for a future session; they do not reconfigure
an executing engine. A frozen run must match its checkpoint configuration.
Removed fields are rejected, rather than silently translated into different odds.
Use a fresh cloud preparation for a new deployment and retain old source/configs
for any existing run until its exposure and journal are reconciled.

Implementation: [configuration](../src/btc15/strategies/settlement_edge/config.py),
[Bleep probability](../src/btc15/strategies/settlement_edge/bleep.py),
[features and lead](../src/btc15/strategies/settlement_edge/model.py),
[entry rules](../src/btc15/strategies/settlement_edge/rules.py).
