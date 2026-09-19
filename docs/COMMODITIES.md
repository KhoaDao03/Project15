# Commodity bots

Gold (`GOLD`), silver (`SILVER`), and WTI oil (`WTI`) use the same isolated
collector, strategy, paper execution, history, and private dashboard structure
as the crypto assets. The shared live executor also supports commodity contracts
through the same per-asset real-money dashboard controls.

Verified against Kalshi public series/market metadata on September 17, 2026:

| Asset | Series | Official Pyth underlying | Decimals |
|---|---|---|---|
| Gold | KXGOLD15M | Metal.Index.1OZGOLD/USD | 2 |
| Silver | KXSILVER15M | Metal.Index.SILVER/USD | 3 |
| WTI oil | KXWTI15M | Commodities.Index.PYTHOIL/USD | 2 |

These contracts compare the closing price of a one-minute Pyth candle against
the published strike. They do not settle on the crypto 60-sample average.
The probability model is `bleep-reference-atr-finish-v5`, shared with crypto.
It uses rolling ATR from official Pyth reference candles, a Normal-CDF estimate
of spot-to-strike distance, and the fading indicator adjustment. Commodity sigma
uses BTC's **1.35 multiplier**; selected-side confidence is capped at book mid
**+6 percentage points**, up to 98%, and checked again before paper submission.

| Setting | GOLD / SILVER / WTI |
| --- | --- |
| Entry window | `1 < seconds_remaining <= 420` |
| Confidence after safety and market-respect caps | At least 85% |
| Price range / maximum spread | 80–95¢ / 4¢ |
| Same-side confirmation | One fresh sample |
| Paper hard stop | 55¢ |
| Take-profit | Held-side bid ≥99¢; remaining contracts settle |

The safety clamp, quality/freshness checks, sizing and risk checks remain.
The directional Bollinger entry veto is disabled; Bollinger inputs still contribute
to the probability model’s indicator adjustment. Settlement uses the parsed Pyth terminal-price rule and official
outcome, not the crypto final-minute average. These are uncalibrated estimates.

Rolling ATR averages 14 true ranges from the last 15 contiguous reference candles,
including the live minute. Until available, it uses the price-relative floor,
never crypto exchange seeds. Other indicators still require 33 contiguous candles.

The parser checks rule wording, source identity, time boundaries, strike,
precision, payout, and price grid. Unknown rules fail closed. The collector
subscribes to Kalshi's authenticated `pyth_value` channel for the exact index,
not a similarly named spot instrument. Official exchange results determine
paper settlement, including the contract's last-published-data fallback;
stale input still blocks new entries.

Sources: [Pyth feed protocol](https://docs.kalshi.com/websockets/pyth-value),
[gold series](https://api.elections.kalshi.com/trade-api/v2/series/KXGOLD15M),
[silver series](https://api.elections.kalshi.com/trade-api/v2/series/KXSILVER15M),
[WTI series](https://api.elections.kalshi.com/trade-api/v2/series/KXWTI15M).
Captured contract fixtures are in `tests/fixtures/commodities-20260917.json`.

## Local operation

The frozen configurations are copied from `config/settlement-edge-{asset}-paper.json`
to `data/{asset}/paper-v1.json`, using lowercase asset names. Each has its own
`paper.db` and `{asset}-paper-v1` run. Add corresponding entries to the private
fleet manifest. Install `deploy/btc15-commodity@.service` in the user systemd
directory, then run:

```sh
systemctl --user daemon-reload
systemctl --user start btc15-commodity@gold btc15-commodity@silver btc15-commodity@wti
```

Commodity candles come from observed official Pyth ticks, not crypto exchange
seed candles. A fresh bot needs approximately 33 contiguous minutes of history
for Bleep indicators. A valid saved reference cache helps subsequent restarts.
There is no synthetic production warmup. These paper collectors are separate
from the existing live-only crypto cloud profile.

## Stop one bot

Each private dashboard asset card has **Stop {asset} bot**. It disables automatic
entries only for that asset, requests cooperative collector shutdown, saves its
paper positions, and waits for acknowledgement. Other collectors, the executor,
and the dashboard remain running. Restart the matching systemd service to resume.

The executor rejects shutdown while that asset has a managed live position or
unresolved order that requires its feed. Close the position or explicitly take
manual control first. The stop button does not liquidate exchange positions.
Errors remain visible; an HTTP acceptance alone is not a completed shutdown.

## Optional historical indicator preload

Pyth history can shorten the 33-minute indicator warmup using the exact commodity
index's closed one-minute candles. It requires a Pyth Pro key entitled to that
index's history: [Pyth History API](https://docs.pyth.network/price-feeds/pro/api/history).
The retired public TradingView endpoint is not used.

Set `PYTH_PRO_API_KEY` in the server's private environment (or its protected `.env`),
and enable `bleep_exchange_seed_enabled` in the commodity strategy config. Despite
its legacy name, that setting selects official Pyth history for commodities and
exchange history only for crypto. Deploy config changes through the normal backed-up
migration; do not replace a checkpoint hash without an audit and portfolio checks.

The loader validates index, asset, contiguous closed candles, OHLC and freshness.
It records the seed for replay and feeds only the Bleep indicators. It does not
fabricate reference ticks, settlement samples or fresh entry confirmations, and
it does not replace the separately calculated reference ATR or bypass health gates.
Missing keys leave normal live-history warmup in place; API errors retry without
interrupting price collection. No key is written to the seed record or logs.

This optional loader is not enabled in the current commodity presets. Until a key
is available, the running bots continue collecting and using their local reference cache.

## Buying and selling

Commodities use the same full-position paper executor as crypto: buy exactly
10 contracts or none within the price cap, subject to current depth and risk limits.
Once a 99¢ take-profit or 55¢ hard-stop triggers, sell the full held quantity,
consuming available bids and continuing on fresh depth until closed or settled.
Partial exits are possible when liquidity is insufficient; trigger prices do not
guarantee fill prices. One trade per market and the 60-second post-close cooldown
are retained. See [full-position execution](FULL_POSITION_EXECUTION.md).
Live execution uses the same fill-or-kill buys, reduce-only sells, resting 99¢
take-profit, and committed 55¢ stop as crypto. Enable it per asset using the
dashboard confirmation controls; deploying support does not turn these switches on.
See [live automation](LIVE_AUTOMATION.md) for live order handling and safeguards.
