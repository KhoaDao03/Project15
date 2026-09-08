# Paper trading

`btc15 paper` combines authenticated recording, model evaluation and the paper
executor. `btc15 collect` records/evaluates without simulated submissions. Both use
PAPER-labelled operational data and never send trading requests to Kalshi.

An approved signal is persisted before execution. Immediately before submission,
execution rechecks backend wall time, reference/book freshness, open status, EV,
valid price grid, risk limits and unique market-entry claim. Orders have a latency
eligibility time; public trades with source times before eligibility cannot fill.

Passive matching requires opposing taker direction and eligible traded volume at
or through the limit. Visible same-side depth at/better than the quote, multiplied
by 1.5 by default, is counted as queue ahead. Cancellation of visible queue is
not assumed to help. After queue depletion, volume can produce fractional partial
fills; duplicate trade IDs cannot fill twice. Quote touches alone never fill.

Resting orders are revalidated on each material reference, book or trade event,
and heartbeats. Cancellation leaves filled positions exposed and retains a
conservative reservation. Invalidation never magically undoes an earlier fill.
Aggressive orders use executable depth after latency; unfilled IOC remainders
are cancelled. Exit intents also incur latency, consume visible bids, and do not
reuse unchanged displayed depth across repeated events. This is conservative:
V1 does not infer replenishment hidden within unchanged aggregate depth.

Trade results include entry cost, bought amount, remaining quantity, proceeds,
fees, gross/net P&L, holding time and observed price excursions. Settlement only
uses a received official result, never a prediction. A data gap at expiry leaves
a pending position until the result arrives. Partially exited positions receive
a payout only for their remaining quantity.

Limitations: inferred queue, unobserved hidden liquidity, counterfactual market
impact, assumed latency/slippage, no exchange queue reconciliation, no automatic
cancel/replace, no full order/position crash-resume. A restart with unresolved prior
paper fills is blocked. Realized P&L, daily reservations and attempt counts from
closed prior runs are restored so a restart cannot reset loss limits. Account
history outside this research database is not used. Do not call this production
paper validation until authenticated stream/recovery/rollover tests and a soak run
have passed against real observations.
