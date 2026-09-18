# Project15 documentation

The cloud deployment uses one Bleep settlement-probability strategy for BTC, ETH,
SOL and XRP. These are the current guides:

| Task | Guide |
| --- | --- |
| Deploy the live-only cloud runtime | [Cloud deployment](CLOUD.md) |
| Set up an IONOS VPS, HTTPS and dashboard access | [IONOS setup](IONOS_VPS_SETUP.md) |
| Understand entry and exit rules | [Strategy](STRATEGY.md) |
| Inspect shared crypto configuration | [Active presets](ACTIVE_PAPER_SETTINGS.md) |
| Understand probability and settlement | [Bleep model](PROBABILITY_MODEL.md), [contract settlement](SETTLEMENT_MODEL.md) |
| Understand actual order execution | [Live automation](LIVE_AUTOMATION.md), [manual trading](MANUAL_TRADING.md) |
| Diagnose missing trades or stale data | [Troubleshooting](TROUBLESHOOTING.md) |
| Understand confirmation and startup history | [Lead confirmation](SUSTAINED_LEAD.md), [reference preload](REFERENCE_PRELOAD.md) |
| Recover positions or metadata failures | [Settlement recovery](SETTLEMENT_RECOVERY.md), [safety](SAFETY.md) |

Local paper execution remains available for testing the same Bleep strategy:
[paper lifecycle](PAPER_TRADING.md), [full-position execution](FULL_POSITION_EXECUTION.md),
[crypto portfolios](CRYPTO_PAPER.md), [commodity paper bots](COMMODITIES.md),
[validation](VALIDATION.md) and [backtesting](BACKTESTING.md).
The cloud profile does not run paper workers or capture raw tapes.

Old probability experiment documentation has been removed. Existing recorded
runs retain their original evidence; replaying a removed predictor requires its
original checkout. Do not resume an old checkpoint by replacing its config hash.
