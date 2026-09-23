# Real-money trading

Real orders use the separate execution service, with explicitly confirmed
per-asset controls. See [live automation](LIVE_AUTOMATION.md) for order handling,
[manual trading](MANUAL_TRADING.md) for tickets, and [cloud deployment](CLOUD.md)
for installation.

Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`: those flags guard the
legacy strategy executor, not the separate authorized real-order service.
Fresh installations start with automation disabled; existing journals retain
saved controls across restarts.

Research logging v2 is deployed; see [deployment status](RESEARCH_LOGGING.md#deployment-status).
