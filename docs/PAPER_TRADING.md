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

Each paper execution action commits its ledger records, duplicate claims, state
transitions and accounting checkpoint together. A failed write restores the
in-memory projection and rolls back SQL. Checkpoints include fractional positions,
fee carry, queue state, trade IDs, exit depth consumption and daily risk totals.

`btc15 paper --resume RUN_ID` restores a checkpoint under the same configuration
and mode. It cancels resting remainders, keeps filled exposure, starts with feeds
unhealthy, and polls previously tracked markets for official settlement. No fills
are inferred for downtime. Older runs without atomic checkpoints require forensic
recovery. Crash writer leases intentionally require operator review before release.

Series and event fee schedules are applied at their effective times. Supported
profiles are unit-multiplier quadratic and quadratic-with-maker-fees; other
schedules explicitly block execution. Quadratic maker trading fees are zero;
fractional balance alignment can still cost money. Direct accounting defaults to
`0.0001`; applicable FCM accounts use `0.01`. Configured coefficients remain
versioned research assumptions for cost stress tests and special account arrangements.
See the [official schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).

Remaining empirical boundaries: aggregate depth does not reveal actual queue
position, hidden liquidity, counterfactual impact or attainable latency. Test
predeclared pessimistic queue/latency/slippage configurations on consecutive data.
The one-entry/no-automatic-reprice policy is retained; changing that policy is a
strategy experiment, not a prerequisite for correct conservative simulation.
Unattended production paper acceptance and LIVE remain separate, uncompleted gates.
The [managed PAPER service](AUTONOMOUS_PAPER.md) provides a stable run identity,
graceful shutdown, checkpoint resume, disk reserve and health probe for validation.


## Dashboard settings and new sessions

The **Strategies** page saves entry, risk, model and execution settings for new
sessions. `btc15 paper` loads `DATA_DIR/strategy.json` unless a global `--config`
file is supplied. Disabling entries blocks candidate selection and paper order
submission while keeping evaluation and position-management logic available.
Saving or enabling does not start the paper executor; dashboard-owned collection
continues to be collection-only.

For checkpoint resume, use the original configuration even if UI settings have
changed: `btc15 --config config/session.json paper --resume RUN_ID`. Do not create
a replacement run to bypass unresolved positions. The UI switch does not affect
an already-running session; use the [kill switch](SAFETY.md#operating-the-kill-switch)
when a running paper session must stop accepting entries.

## Dashboard shutdown

Use **Shut down safely** in the dashboard header and confirm the prompt. This stops
new entries across the current collector's strategies, cancels unfilled paper order
remainders, drains received events, saves portfolio checkpoints and flushes raw data.
The dashboard closes its server only after the collector acknowledges completion
and releases its database writer lease. The browser tab remains open with the result.
If confirmation fails or times out, the dashboard stays open for inspection; it does
not force-kill the collector or remove its lease.

Open paper positions are retained without forced liquidation. Position management
and settlement processing resume when you restart the same run. For the current
momentum paper session, run `btc15 model-paper --run-id momentum-paper` and restart
the dashboard with `btc15 dashboard --no-collect --port 8001`. Strategy activation
settings are retained and the persistent HALT switch is not set by routine shutdown.
The button affects the collector sharing this dashboard's database, irrespective of
the selected history run or display mode. Live trading remains disabled.

Dashboard startup now evaluates Settlement Edge and all active momentum configurations
on the same ordered BRTI and order-book events. `btc15 dashboard --port 8001` starts
observation only; it does not resume paper portfolios. To resume paper execution,
start `btc15 model-paper --run-id momentum-paper` and use
`btc15 dashboard --no-collect --port 8001` alongside it. Only one collector can own
the database at a time. Strategy cards distinguish current evaluations, waiting/stale
evaluations, and stopped strategies; they also identify observation versus paper
execution. Momentum candle confirmation and volatility warmup still apply even
when incoming data is current.
