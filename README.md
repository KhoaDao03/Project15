# Project15 — Bleep settlement trading

Kalshi 15-minute trading for BTC, ETH, SOL, XRP, gold, silver and WTI. Signal
collectors calculate an ATR-based finish probability; a separate executor manages
real orders. Paper execution is available for local testing.

## Start here

| Task | Guide |
| --- | --- |
| Deploy a fresh cloud installation | [Cloud deployment](docs/CLOUD.md) |
| Set up a VPS and dashboard access | [IONOS setup](docs/IONOS_VPS_SETUP.md) |
| Understand entry and exit decisions | [Strategy](docs/STRATEGY.md) |
| Check the tracked settings | [Crypto presets](docs/ACTIVE_PAPER_SETTINGS.md), [commodities](docs/COMMODITIES.md) |
| Understand probability calculations | [Probability model](docs/PROBABILITY_MODEL.md) |
| Understand real orders and exits | [Live automation](docs/LIVE_AUTOMATION.md) |
| Record portable research data | [Research logging](docs/RESEARCH_LOGGING.md) |
| Run tests or diagnose problems | [Test commands](tests/README.md), [validation](docs/VALIDATION.md), [troubleshooting](docs/TROUBLESHOOTING.md) |

See the [documentation index](docs/README.md) for specialist guides and historical reports.

## Configuration and deployment

Research logging v2 was deployed with authorization on 2026-09-20. See the
[deployment status](docs/RESEARCH_LOGGING.md#deployment-status).

Tracked presets are templates. Existing bots use frozen runtime configurations;
editing a preset does not update a running bot. ATR multipliers live in the
[probability implementation](src/btc15/strategies/settlement_edge/bleep.py).
Preserve existing histories and reconcile outstanding exposure before a cutover.
Do not rewrite checkpoint hashes to bypass configuration checks.

Fresh cloud preparation:

```bash
uv sync --locked --no-default-groups
.venv/bin/python scripts/prepare_cloud.py
```

This prepares configurations; it does not start trading. Follow the cloud guide
for services and credentials. Research recording is opt-in and disabled by default.
The probability estimate is uncalibrated, and price triggers do not guarantee fills.
