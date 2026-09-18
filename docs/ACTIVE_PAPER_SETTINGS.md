# Active crypto presets

BTC, ETH, SOL and XRP use the same Bleep strategy settings except `asset`; fixed volatility multipliers and market-cap premiums depend on asset identity.
The tracked files are `config/settlement-edge-{active,eth,sol,xrp}-paper.json`.
Despite their filenames, these are also the source for fresh cloud signal configs.
`prepare_cloud.py` freezes them into `data/cloud/{BTC,ETH,SOL,XRP}.json`.

| Setting | Shared value |
| --- | --- |
| Model | Bleep ATR finish estimate |
| Standard window | `120 < seconds_remaining <= 480` |
| Late window | `1 < seconds_remaining <= 120` |
| Standard / late probability floor | `min_probability=0.83`, `late_min_probability=0.83` |
| ATR sigma multiplier | BTC 1.35; ETH 1.25; SOL/XRP 1.00 |
| Market-respect cap | Selected-side mid +6pp BTC/ETH; +10pp SOL/XRP; maximum 98% |
| Purchase range | 80–95¢ |
| Same-side confirmations | 1 standard; 1 late |
| Normalized lead minimum | Disabled; fresh side confirmation remains |
| Directional Bollinger entry filter | Enabled |
| Net-edge / expected-value vetoes | Disabled |
| Paper sizing | 10 contracts, all or none within the price cap |
| Paper take-profit | Held-side bid ≥99¢ |
| Hard stop | Held-side bid ≤55¢ |
| Model probability / hold-value exits | Disabled |
| Standard cashout / profit-value exits | Disabled |
| Paper post-close cooldown | 60 seconds |
| Unfilled-order retry cooldown | 0; previous outcome must be known |

Any remaining inventory settles at expiration. Full-position paper sells stay
committed and consume available depth; triggers are not guaranteed fill prices.
Live automation shares the probability/entry strategy and 99¢/55¢ exit thresholds,
with separate resting-order handling and user-selected quantity; see
[live automation](LIVE_AUTOMATION.md).

All four presets have a regression test comparing every strategy field except
asset identity. Exchange identifiers, official feeds and settlement precision are
necessarily asset-specific. Read [the probability model](PROBABILITY_MODEL.md) for
its inputs and assumptions and [cloud deployment](CLOUD.md) for preparation.

Frozen runtime configs do not change when presets change. This version rejects
removed probability-mode fields and uses new configuration hashes. Existing
histories and portfolios must not be reset or relabeled to claim they ran Bleep.
