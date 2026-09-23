# Crypto 82% signal screening

Read-only scan of completed, compressed live-executor archives with evaluations since 2026-09-21 02:23 UTC, through roughly 05:10 UTC. Filtered to current ATR values BTC 0.9 / ETH 1.1 / SOL 0.8 / XRP 1.0. Deduplicated collector decision IDs. Selected 82% <= capped confidence < 83%, MIN_PROBABILITY as the only recorded strategy rejection, and a healthy collector. First such observation per market. Standard and late entries included within the configured window.

11 markets: BTC 4, ETH 4, SOL 1, XRP 2. Settlement outcomes: 10 matching sides, 1 losing side. Nine markets subsequently received real bot buys. Two with no recorded real buy were BTC: 02:55:42 UTC YES at 90 cents (YES settlement), and 04:58:00 UTC YES at 85 cents (NO settlement).

Illustrative gross hold-to-settlement P&L for those two markets at ten contracts: +$1.00 and -$8.50, total -$7.50 before fees. This is NOT a simulation of the bot's stop, take profit, fills, fees, or final incremental P&L. All 11 signals combined would have only +$0.02 gross at settlement despite 10/11 winning sides; fees excluded. Earlier fills for the nine overlapping markets could have different results.

Limitations: executor checks are conditional on real control/order state; completed archive files only; known recording gaps and collector interruptions; config/restart history and daily-loss-policy changes within period. Not an exhaustive paired replay or calibrated win-probability estimate. No live settings changed.
