> **Scope update:** current runtime is Settlement Edge only. Multi-strategy descriptions
> and measurements below describe historical revisions, not current execution.
> See [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) for current architecture, history and recovery.

# Trade memory and exports

Each meaningful evaluation has a UUID, mode, run, market, complete effective
configuration, Git/source/config version hashes, model versions, settlement spec,
causal features, raw/conservative probabilities, uncertainty, model quality,
normalized book and structured rejection reasons. An unavailable model still
produces an opportunity containing the diagnostic failure. Rejected opportunities
are never discarded simply because no order was submitted.

Orders, partial fills, cancellations, exit intents and trade results reference
the originating opportunity. Market-level transitions and authoritative settlement
records are attached when constructing replay. Historical predictions remain
separate from outcomes. Full source and dependency-lock snapshots accompany runs,
including changes not yet committed to Git. Credentials are not part of snapshots.

`btc15 export <opportunity-id>` creates a new directory under
`data/trade_packets/YYYY/MM/<opportunity-id>/`. It contains summary, features,
probability, decision, orders, fills, config, versions, software snapshot, timeline,
market/probability Parquet paths and an analysis Markdown file. Existing packet
directories are not overwritten. Exports carry the recorded mode.

`/api/replay/<id>` returns opportunity, market timeline and probability/market path.
`/api/records` supplies paginated/filterable JSON. `/api/analytics` supplies scores,
counts, rejections, P&L paths and grouped research metrics. These APIs support an
AI analyst without scraping HTML. Analysis can propose experiments; no API can
change the running strategy or activate live orders.

Limitations: process-quality letter grading is not implemented; counts and grades
must not equate a winning outcome with sound reasoning. Replay charts do not yet
render all requested entry/exit/window overlays. Fee/slippage estimates and actual
simulated fees are distinct; they should not be silently substituted in analysis.


## Dashboard views

**Opportunity memory** groups evaluations by market, newest market activity first.
Each expandable group shows matching evaluations, skipped entries, passed entry
checks, latest evaluation time and run count. Counts cover all matching records
before pagination. Expanding loads that market's evaluation cards, with additional
pages available. Mode, run, search and decision filters apply to groups and cards.
History stays still while reading; **Refresh history** loads newer records.

**View explanation** shows reasons and saved inputs before charts. Technical data
is expandable, and JSON remains accessible. The timeline covers the whole market
within that run, so not every event belongs to the selected evaluation. Passing
entry checks does not prove an order or fill; **Completed trades** shows results.

`GET /api/records?group_by_market=true` returns market summaries and a total
matching evaluation count. `market=TICKER` selects an exact market for its
paginated records. The underlying immutable evaluations are unchanged.

**Results & accuracy** uses completed trades for realized P&L, win rate and
drawdown. Calibration uses the last eligible entry-window prediction per
run/market with a recorded official settlement, even without a trade. Fill rate
uses orders and fills; skipped-entry counts use evaluations. A selected run with
no recorded settlements has no calibration observations. Small samples do not
establish predictive accuracy or profitability.
