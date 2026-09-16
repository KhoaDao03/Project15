# One trade per market and historical re-entry

The active paper run sets `one_trade_per_market=true`: a market can have only one
filled position over its lifetime in that run, across YES/NO and standard/late
entries. After it closes, that market stays closed to new entries. Partial fills
of the original order still belong to one trade; partial exits do not free another
entry. Unfilled attempts retain their existing bounded retries and five-second
retry cooldown. A new market can trade after the existing global cooldown.

The limit uses checkpointed fills/completion markers on the latest order, inventory,
and cycle numbers. A later unfilled cycle also proves an earlier completed trade
and cannot bypass the limit after enabling it. Restart does not reset the allowance;
history and risk balances are retained. Existing positions keep their exit rules.

Historical configurations omit this switch or set it to false and retain the
sequential behavior below. The disabled default preserves their configuration hashes.

With the one-trade limit disabled, the paper engine continues evaluating an active market after a completed position.
It can open another position when all entry conditions pass again, more than 15
seconds remain under the active late-entry settings, and the active sixty-second
`post_close_cooldown` has elapsed since the most recent fully closed position in
any market. A partial sale does not start the pause. The timestamp survives restart,
and a new position in a different market must observe the same pause. This applies
after every full closure, including a safety exit or settlement.

The separate five-second `entry_retry_cooldown` still controls unfilled-order retries
and the historical same-market minimum pause. Configurations that omit
`post_close_cooldown` retain zero additional pause across markets. The active
sixty-second pause takes precedence when both checks apply.

Only one position or active entry order per market is allowed. Partial exits do not
permit another entry. Each completed trade permits a new entry cycle; cycle numbers
and completion timestamps are checkpointed. Each cycle starts at attempt 1 and keeps
the existing two additional retries for unfilled orders. An exhausted unfilled cycle
does not reset itself. Durable claims include cycle and attempt, preventing duplicate
submissions. No fixed cap is imposed on completed cycles; existing time, entry, health,
metadata, daily-loss and exposure checks still apply. Accounting and risk budgets are
not reset between cycles. Positions can switch sides only after full closure and a
new qualifying assessment.

Previously completed orders recover their completion timestamp from their own trade
result when resuming older checkpoints. Closed historical markets remain outside the
processing loop; only still-tradable markets with completed orders resume evaluation.
Each new trade has a separate opportunity/trade ID for fills, fees and results.

The stop-confirmation observer pairs results by trade ID. If it is still holding an
earlier trade when a new primary trade starts in that market, the entire new trade is
excluded from the shadow comparison (`shadow_trade_excluded` in its separate ledger).
It never merges those inventories or blocks the primary trade. Exclusions survive
restart. Such exclusions limit the completeness of the experiment and are not zero-P&L
comparisons. Normal sequential shadow trades continue when its previous position has
closed.
