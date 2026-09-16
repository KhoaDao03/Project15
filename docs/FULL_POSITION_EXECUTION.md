# Full-position paper execution

The active BTC, ETH, SOL and XRP presets enable `full_position_execution=true`.
Each new order requests exactly 10 contracts. Risk limits must permit all 10;
the bot skips the order if they would reduce its size.

After the existing execution latency, the matcher checks current executable ask
depth within the original price cap. It buys all 10, cheapest prices first, or
cancels without buying any. Prices can span multiple levels. Insufficient depth
records `full_size_unavailable`; existing unfilled-order cooldown and retry limits
still apply. The price cap remains best ask at submission plus configured slippage,
bounded by maximum entry price. No unavailable liquidity is simulated.

Once an exit decision is made, it remains committed until the held quantity is
sold or the market settles. Matching consumes highest bids first after latency,
then continues on fresh available depth when a remainder exists. A recovering
probability or price does not cancel the sale. Take-profit thresholds trigger the
sale but no longer impose a minimum fill price in this mode, so subsequent fills
can occur below the trigger price. The 55-cent hard stop remains a trigger, not
a guaranteed execution price. Existing freshness, venue and recovery checks apply.

Sell quantities can be partial while liquidity is insufficient; the bot cannot
guarantee immediate liquidation. Existing smaller positions sell their actual
remaining quantity. It never sells contracts it does not hold. Consumed-depth
accounting and committed intents survive checkpoints and restarts.

The setting defaults to false so historical configurations retain their previous
execution semantics and hashes. Current probability settings, timing and risk
limits are unchanged. Deployment evidence and backups:
`data/runtime/full-position-v1/`.
