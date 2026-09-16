# Real-money trading is out of scope

This branch is deliberately limited to proving **BTC15 Settlement Edge** in research and paper operation before considering anything else. There is no live-activation runbook and no supported real-money order path.

Both [Settings](../src/btc15/config.py) and [LiveTrader](../src/btc15/execution.py) fail closed. Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`. Changing either to LIVE/true causes rejection; it is not an upgrade procedure. Authenticated market data is needed for live-connected paper testing, but those local simulated orders are not exchange orders.

The existing `live_payload` helper and read-only `KalshiClient.reconciliation` are code-review/test boundaries only. They do not provide a functioning order-submission, unknown-submission recovery or account-reconciliation state machine. No dashboard history filter, next-session settings save, analyst output or passing test enables them.

Any future real-money work would require a separately authorized scope and implementation review, authentic paper evidence, account/fee/collateral reconciliation, network-time and unknown-order recovery, tested cancellation/risk controls and secured deployment. Those are not current capabilities or a promise to expand this branch. For this milestone use [Getting started](GETTING_STARTED.md), [Paper trading](PAPER_TRADING.md) and [Validation](VALIDATION.md).
