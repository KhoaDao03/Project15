# Conditional Bollinger entry filter

The paper experiment adds one entry veto to BTC15 Settlement Edge. With
`bollinger_entry_filter_enabled=true`, reject a new YES entry when the causal
model reference exceeds the upper Bollinger Band, or a new NO entry when it is
below the lower band. Equality passes. Both standard and late entries use the
side selected by their existing rules. A market may qualify again later.

The active settings use 20 completed, contiguous UTC one-minute BRTI candles and
bands at the mean close plus/minus two population standard deviations. Each candle
requires at least 58 received samples and no internal source-time gap over two
seconds. The sequence must end at the latest completed minute of the model
snapshot. Open candles, future source times and references received after that
snapshot are excluded. Existing model caching is retained.

If fresh bands are unavailable, the filter reports `unavailable` and leaves the
original decision to the probability, lead, price, value, quality, health and risk
checks. Missing bands do not bypass those checks. A restart begins with empty
reference history: the existing five-minute model warmup still applies, while
this filter waits for 20 usable closed candles. Feed gaps can extend that wait.
The dashboard shows the filter status in Evaluation details.

Exits and sizing are unchanged, including the existing 70% adjusted-probability
exit, hard stop, take profit and enabled profit-only value exit. The proposed 50%
partial probability exit is not implemented. With the active
`revalidate_entry_signal=false`, an already submitted IOC order retains its
original entry commitment and price cap; a subsequent band crossing does not
cancel it. Execution health and risk checks still apply.

## Evidence for the paper test

The filter defaults to disabled, preserving historical configuration hashes.
The approved active paper file is `config/settlement-edge-active-paper.json`;
the installed service uses `data/runtime/settlement-convergence-v8.json`.
Enabled configuration hash: `ebc936c466e8f0bf` (previously `75d8de22c3ccaf5f`).
The existing run is resumed with an audited `config_update`; no history or risk
balance is reset. The inactive observer's ledger is retained as historical data.

Entry evidence includes `bollinger_entry_filter`, with enabled/status/reference
and usable band bounds. Rejections include `BOLLINGER_EXTENSION` and the relevant
bound. Existing minute-level `entry_rejection_summary` records retain a sample,
configuration version, and counts. For Bollinger rejections, the summary also
counts evaluations that pass all other entry checks and have positive risk sizing,
and retains the first such decision as `otherwise_eligible_sample` with timestamp,
input snapshot ID and model time. These are evaluation counts, not distinct
trades or proven avoided losses. Compact market inputs remain available for replay.

Separate trades by their opportunity's configuration version when comparing the
new phase with earlier trades in the same run. Track net P&L after fees, drawdown,
loss severity, win rate, band availability, and how often blocked entries later
qualify. Keep interrupted executions identifiable. A proper counterfactual replay
must regenerate entries, fills and risk state; rejected evaluations alone cannot
establish the P&L of a bot without this filter.

The motivating historical screen rejected four losing entries and one winning
entry from the 43-trade v8 sample excluding trades 16 and 17. That screen retained
actual fills/exits for accepted trades and did not simulate replacement entries.
Its thresholds were selected after examining historical results. New paper data
is needed to assess whether the improvement persists.
