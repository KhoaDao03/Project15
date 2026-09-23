# Documentation

Start with one guide for the task below. Tracked presets describe templates, not
necessarily the frozen configuration loaded by a running service.

Research logging v2 was deployed on 2026-09-20; see [deployment status](RESEARCH_LOGGING.md#deployment-status).
Documentation edits alone do not authorize service restarts.

## Main guides

| Task | Start here |
| --- | --- |
| Understand entries, exits and settings | [Strategy](STRATEGY.md), [crypto settings](ACTIVE_PAPER_SETTINGS.md), [commodity settings](COMMODITIES.md) |
| Understand the calculation | [Probability model](PROBABILITY_MODEL.md), [settlement rules](SETTLEMENT_MODEL.md) |
| Set up local paper testing | [Getting started](GETTING_STARTED.md), [multiple assets](CRYPTO_PAPER.md) |
| Install on a fresh VPS | [VPS setup](IONOS_VPS_SETUP.md), then [cloud services](CLOUD.md) |
| Update, back up or restart an installation | [VPS maintenance](VPS_MAINTENANCE.md) |
| Use real orders | [Live automation](LIVE_AUTOMATION.md), [manual orders](MANUAL_TRADING.md) |
| Diagnose missing trades or stale data | [Troubleshooting](TROUBLESHOOTING.md), [operational status](OPERATIONAL_STATE.md) |
| Handle a halt or crash | [Safety and recovery](SAFETY.md), [settlement recovery](SETTLEMENT_RECOVERY.md) |
| Record and replay research data | [Research logging](RESEARCH_LOGGING.md), [backtesting](BACKTESTING.md) |
| Test a change | [Test commands](../tests/README.md), [validation and benchmarks](VALIDATION.md) |

## Implementation references

- Structure and persistence: [architecture](ARCHITECTURE.md), [data model](DATA_MODEL.md), [recording](TRADE_RECORDING.md), [trade history](TRADE_MEMORY.md).
- Inputs: [discovery](MARKET_DISCOVERY.md), [reference preload](REFERENCE_PRELOAD.md), [lead confirmation](SUSTAINED_LEAD.md), [entry revalidation](ENTRY_REFERENCE_REVALIDATION.md).
- Paper execution: [lifecycle](PAPER_TRADING.md), [managed service](AUTONOMOUS_PAPER.md), [position management](POSITION_MANAGEMENT.md), [full-position execution](FULL_POSITION_EXECUTION.md), [exit matching](EXIT_EXECUTION.md).
- Recovery: [overload handling](OVERLOAD_RECOVERY.md), [venue pauses](MARKET_PAUSE.md), [legacy portfolio retirement](SINGLE_STRATEGY.md).
- Research experiments: [fill assumptions](FILL_EXPERIMENT.md), [convergence](CONVERGENCE_EXPERIMENT.md), [stop confirmation](STOP_CONFIRMATION_SHADOW.md).

## Historical evidence

These reports preserve measurements and actions for specific revisions and hosts.
Their service states, thresholds and test counts are not current deployment claims.
Do not use incident commands as setup instructions for another machine.

- [Research hardening, September 8](RESEARCH_HARDENING.md)
- [Single-strategy validation, September 9](SINGLE_STRATEGY_VALIDATION.md) and [collection recovery](COLLECTION_RECOVERY.md)
- [Reliability audit, September 11](RELIABILITY_AUDIT_20260911.md), [collector profile](COLLECTOR_PROFILE_20260911.md), [replay validation](REPLAY_RECOVERY_VALIDATION.md)
- [Overnight incident](OVERNIGHT_FAILURE_20260911.md) and [fix](OVERNIGHT_FAILURE_FIX_20260911.md)
- [Connection handshake fix](CONNECTION_HANDSHAKE_FIX_20260911.md) and [dashboard price investigation](DASHBOARD_PRICE_FLASH_20260911.md)
- [Earlier crypto-fleet installation and validation](history/CRYPTO_FLEET_20260913.md)

Older models require their original code and configuration. Preserve their ledgers;
do not relabel old results or rewrite checkpoint hashes as a migration shortcut.
