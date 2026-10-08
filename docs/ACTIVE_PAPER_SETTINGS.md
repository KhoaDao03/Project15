# Active crypto presets

BTC, ETH, SOL, XRP, BNB, HYPE and DOGE use the same Bleep strategy settings except `asset` and `early_min_probability`; fixed volatility multipliers and market-cap premiums depend on asset identity.
The tracked files are `config/settlement-edge-{active,eth,sol,xrp,bnb,hype,doge}-paper.json`.
Despite their filenames, these are also the source for fresh cloud signal configs.
`prepare_cloud.py` freezes them into `data/cloud/{BTC,ETH,SOL,XRP,BNB,HYPE,DOGE}.json`.

| Setting | Shared value |
| --- | --- |
| Model | Bleep ATR finish estimate |
| Early window | `420 < seconds_remaining <= 600` for all seven cryptos |
| Early probability floor | BTC 86%; ETH 89%; SOL 86%; XRP 85%; DOGE 83%; BNB 85%; HYPE 88% |
| Standard window | `120 < seconds_remaining <= 420` |
| Late window | `1 < seconds_remaining <= 120` |
| Standard / late probability floor | `min_probability=0.83`, `late_min_probability=0.83` |
| ATR sigma multiplier | BTC 0.95; ETH 0.95; SOL 1.00; XRP 1.15; BNB 1.25; HYPE 0.90; DOGE 1.10 |
| Market-respect cap | Selected-side mid +6pp BTC/ETH/BNB/HYPE/DOGE; +10pp SOL/XRP; maximum 98% |
| Spread filter | Disabled for all bots; zero spreads allowed. Missing/invalid/crossed quotes still fail validation. Legacy `max_spread` is ignored. |
| Purchase range | 80–96¢ for all seven cryptos |
| Same-side confirmations | 1 standard; 1 late |
| Normalized lead minimum | Disabled; fresh side confirmation remains |
| Directional Bollinger entry filter | Disabled |
| Net-edge / expected-value vetoes | Disabled |
| Paper sizing | 10 contracts, all or none within the price cap |
| Take-profit / resting profit orders | Disabled (`take_profit=null`) |
| Hard stop | Held-side bid ≤55¢ for all seven cryptos |
| Model probability / hold-value exits | Disabled |
| Standard cashout / profit-value exits | Disabled |
| Paper post-close cooldown | 60 seconds |
| Unfilled-order retry cooldown | 0; previous outcome must be known |

Any remaining inventory settles at expiration. Full-position paper sells stay
committed and consume available depth; triggers are not guaranteed fill prices.
Live automation shares the probability/entry strategy and the per-asset stop thresholds,
with separate resting-order handling and user-selected quantity; see
[live automation](LIVE_AUTOMATION.md).

All seven presets have a regression test comparing shared strategy fields except
asset identity, early probability threshold, stop price, and entry window. Exchange identifiers, official feeds and settlement precision are
necessarily asset-specific. Read [the probability model](PROBABILITY_MODEL.md) for
its inputs and assumptions and [cloud deployment](CLOUD.md) for preparation.

Frozen runtime configs do not change when presets change. This version rejects
removed probability-mode fields and uses new configuration hashes. Existing
histories and portfolios must not be reset or relabeled to claim they ran Bleep.

## BNB and HYPE

Added presets use ETH's settings, including its +6pp market-respect cap, with the early probability thresholds listed above.
ATR sigma multipliers are BNB **1.25** and HYPE **0.90**.
Kalshi public metadata captured on September 26, 2026 confirms the same
60-second CF Benchmarks settlement average, with BNB rounded to two decimal
places and HYPE to four. HYPE uses `HYPEUSD_RTI`, not the retired
`U_HYPEUSD_RTI` stream.

Sources: [BNB series](https://api.elections.kalshi.com/trade-api/v2/series/KXBNB15M),
[HYPE series](https://api.elections.kalshi.com/trade-api/v2/series/KXHYPE15M),
[BNB index](https://www.cfbenchmarks.com/data/indices/BNBUSD_RTI),
[HYPE index](https://www.cfbenchmarks.com/data/indices/HYPEUSD_RTI).
Captured contracts are in `tests/fixtures/{bnb,hype}15-20260926.json`.

For an existing fleet, add separate BNB and HYPE configuration files, data
directories, run IDs and manifest entries through the normal deployment process.
Do not rerun fresh cloud preparation over an existing directory or change existing
checkpoint hashes. Repository changes alone do not start collectors or enable
live buying; use the existing per-asset controls after deployment and feed warmup.

## DOGE

DOGE uses the shared settings with an early probability threshold of 83% and an ATR sigma multiplier of
**1.10** and a +6pp market-respect cap. Its official reference is `DOGEUSD_RTI`.
DOGE settlement uses seven decimal places. The captured Kalshi metadata exposes
its full strike in `custom_strike.floor_strike`, while the legacy top-level
`floor_strike` is truncated to six decimal places. The parser requires the precise
strike, validates its operator and consistency with the legacy value, and uses all
seven decimals for settlement and probability calculations. Dashboard reference
prices and strikes preserve that precision.

Public contract fixture: `tests/fixtures/doge15-20260926.json`.
Source: [DOGE series](https://api.elections.kalshi.com/trade-api/v2/series/KXDOGE15M).

At exactly 600 seconds remaining, the early threshold applies. At exactly 420 seconds remaining, the floor becomes 83%, including the existing late window; the final one-second entry cutoff remains in force. These thresholds apply after the market-respect cap.

## ETHD, XRPD and HYPED hourly ladders

Separate `settlement-edge-ethd-paper.json`, `settlement-edge-xrpd-paper.json` and
`settlement-edge-hyped-paper.json` presets add hourly ETH/XRP/HYPE above/below ladders without changing the seven
15-minute presets above. All use a single 83% floor for `60 < seconds_left <= 600`,
80–96¢ asks, 10 contracts, two bought/pending strikes per hourly event, and no
stop, take-profit or entry blackout. ETHD sigma is 1.00 with a 6pp confidence
premium; XRPD sigma is 1.10 with 10pp; HYPED sigma is 1.40 with 6pp.
All three use `entry_limit_offset: null`; the owner disabled HYPED's initially
requested 1¢ offset on October 6, 2026. HYPED version: `9e1dff29e24034e1`.
The 15-minute HYPE sigma remains 1.15.
Fresh cloud preparation now includes all three. See [hourly settings and go-live
checks](HOURLY.md) for frozen versions, strict settlement precision, fleet
migration and fill-quality review. Owner live-buy authorization remains required.
