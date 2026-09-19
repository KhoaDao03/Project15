# Project15 — Bleep settlement trading

A single Bleep ATR-based finish-probability model for Kalshi 15-minute BTC, ETH,
SOL and XRP markets. All four crypto presets share the same strategy parameters;
asset identity selects the reference feed, market series, settlement precision,
volatility multiplier and market-cap premium.

## Cloud deployment

Start with [Cloud deployment](docs/CLOUD.md), or the
[IONOS VPS walkthrough](docs/IONOS_VPS_SETUP.md) for server/domain setup.
The cloud profile runs four signal collectors, one order executor and a shared
private dashboard. It starts no paper simulation workers and records no raw tapes.
An optional [public dashboard](docs/CLOUD.md#optional-public-view-only-dashboard)
provides viewing and passcode-protected owner controls.

```bash
uv sync --locked --no-default-groups
.venv/bin/python scripts/prepare_cloud.py
```

This creates a fresh frozen cloud configuration; it does not start trading.
Follow the cloud guide for credentials and services. Live automation defaults off
and requires explicit per-asset confirmation. Keep trading credentials and the
private dashboard private.

## Shared crypto rules

| Setting | Value |
| --- | --- |
| Probability model | Bleep ATR finish estimate |
| Standard entry window | 7–2 minutes remaining |
| Late entry window | 2 minutes down to, but excluding, 1 second remaining |
| Bleep entry probability | At least 83% after safety and market-respect caps in both windows |
| ATR source | Rolling 14 true ranges from official-reference candles |
| ATR multiplier | BTC 1.35; ETH 1.10; SOL/XRP 1.00 |
| Market-respect cap | Mid +6pp BTC/ETH; +10pp SOL/XRP; maximum 98% |
| Strategy purchase range | 80–95¢ |
| Confirmation | One fresh same-side reference sample |
| Directional Bollinger filter | Disabled |
| Net-edge / EV entry vetoes | Disabled; estimates remain reported |
| Take-profit / hard stop | 99¢ / 55¢ |

Remaining contracts settle at expiration. Price triggers do not guarantee fills.
Live execution uses separate resting-order mechanics; see
[live automation](docs/LIVE_AUTOMATION.md). Probability is an uncalibrated estimate,
not an established profitable edge.

See [Strategy](docs/STRATEGY.md), [Probability model](docs/PROBABILITY_MODEL.md),
[active presets](docs/ACTIVE_PAPER_SETTINGS.md), and the [documentation index](docs/README.md).

## Configuration changes

There is no probability-mode selector. The old blended and Project15 Monte Carlo
modes and their simulation settings have been removed. The ATR probability supplies entry confidence and EV. Settlement-average evidence
remains a separate confirmation check.

Old configurations containing removed fields are rejected. Prepare fresh cloud
configs from the current presets; do not copy old frozen configs or change a
checkpoint hash to bypass compatibility checks. Existing trade records remain
readable, but this version does not reproduce old models. Keep the previous
checkout for any still-running installation until an orderly deployment/cutover.
The cloud guide describes the boundary between a fresh deployment and an existing
account migration.
