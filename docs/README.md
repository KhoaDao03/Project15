# Documentation index

Use the guides below for the current code. Preset settings are not proof of the
configuration loaded by a running service.

## Operations and strategy

| Task | Guide |
| --- | --- |
| Fresh cloud deployment | [Cloud](CLOUD.md), [IONOS VPS setup](IONOS_VPS_SETUP.md) |
| Entry/exit behavior and settings | [Strategy](STRATEGY.md), [crypto presets](ACTIVE_PAPER_SETTINGS.md), [commodities](COMMODITIES.md) |
| Probability and contract outcomes | [Probability model](PROBABILITY_MODEL.md), [settlement model](SETTLEMENT_MODEL.md) |
| Real order execution | [Live automation](LIVE_AUTOMATION.md), [manual trading](MANUAL_TRADING.md) |
| Missing trades, stale data, recovery | [Troubleshooting](TROUBLESHOOTING.md), [operational state](OPERATIONAL_STATE.md), [settlement recovery](SETTLEMENT_RECOVERY.md) |
| Startup history and confirmation | [Reference preload](REFERENCE_PRELOAD.md), [lead confirmation](SUSTAINED_LEAD.md) |
| Resource maintenance | [VPS maintenance](VPS_MAINTENANCE.md), [safety](SAFETY.md) |
| Portable recordings and analysis | [Research logging](RESEARCH_LOGGING.md), [backtesting](BACKTESTING.md) |

## Development and local simulation

Start with [validation](VALIDATION.md) for test commands and a benchmark-script map.
For local paper execution, see [getting started](GETTING_STARTED.md),
[paper lifecycle](PAPER_TRADING.md), [full-position execution](FULL_POSITION_EXECUTION.md)
and [crypto portfolios](CRYPTO_PAPER.md). Paper and replay outcomes are separate
from real fills. The signal-only cloud profile runs no paper workers; research
recording must be explicitly enabled.

Implementation references: [architecture](ARCHITECTURE.md), [data model](DATA_MODEL.md),
[entry economics](ENTRY_ECONOMICS.md), [trade memory](TRADE_MEMORY.md),
[collector recovery](COLLECTION_RECOVERY.md), and [exit execution](EXIT_EXECUTION.md).

## Historical evidence

Dated reports describe a specific revision and environment, not current deployment
status or current test counts. Retain them when comparing old behavior:

- [Single-strategy validation, September 9](SINGLE_STRATEGY_VALIDATION.md)
- [Reliability audit, September 11](RELIABILITY_AUDIT_20260911.md)
- [Collector profile, September 11](COLLECTOR_PROFILE_20260911.md)
- [Overnight incident](OVERNIGHT_FAILURE_20260911.md) and [fix](OVERNIGHT_FAILURE_FIX_20260911.md)
- [Connection handshake fix](CONNECTION_HANDSHAKE_FIX_20260911.md)
- [Dashboard price flash investigation](DASHBOARD_PRICE_FLASH_20260911.md)

Older predictor implementations remain in Git history. Replaying an old model
requires its original code/configuration; do not relabel old records as current results.
