# Sequential trades within a market

The paper engine continues evaluating an active market after a completed position.
It can open another position when all entry conditions pass again, more than 15
seconds remain under the active late-entry settings, and the existing five-second
`entry_retry_cooldown` has elapsed since the prior position closed.

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
